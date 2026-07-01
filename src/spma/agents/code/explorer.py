"""CodeExplorer——多轮探索引擎（design-13 §3.5）。

独立于 LangGraph：通过 explore() 一次性调用，也可注入 mock 状态做单测。
6 阶段方法：refine / glob / grep / read / expand_via_ast / assess。
"""
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from spma.agents.code.completeness import CodeCompletenessResult
    from spma.agents.code.state import CodeAgentState

logger = logging.getLogger(__name__)


@dataclass
class ExplorerState:
    """CodeExplorer 内部状态——独立于 LangGraph CodeAgentState。

    与 LangGraph state 边界：
        - 入口（explore() 接收）：从 CodeAgentState 读 entities / candidate_repos / query
        - 出口（explore() 返回）：把 ripgrep_results / expanded_context / convergence 写回
        - 不双向同步：LangGraph state 在 explore() 期间冻结
    """
    round: int = 0                          # 当前轮次（1-indexed）
    previous_new_files: int = 0              # 上轮新增文件数（stuck 判定用）
    new_files_this_round: int = 0            # 本轮新增文件数
    search_terms: dict = field(default_factory=dict)
    ripgrep_results: list[dict] = field(default_factory=list)
    expanded_context: list[dict] = field(default_factory=list)
    seen_files: set[tuple[str, str]] = field(default_factory=set)
    fallback_layer: int = 0
    call_depth: int = 0
    convergence: "CodeCompletenessResult | None" = None
    # 输入字段（从 CodeAgentState 传入）
    query: str = ""
    entities: dict = field(default_factory=dict)
    candidate_repos: list[str] = field(default_factory=list)
    # Task 1: 反思层状态字段
    reflection_count: int = 0
    consecutive_no_progress_reflections: int = 0
    # Task 8: glob pattern 解析来源——LLM 主导 + 3 层降级（spec §2.2）
    glob_patterns_resolved: str = ""  # "llm" | "fallback_query" | "fallback_wildcard"


class CodeExplorer:
    """多轮探索引擎——封装 Glob→Grep→Read→Refine→Assess 循环。"""

    def __init__(
        self,
        ripgrep_executor,
        ast_parser,
        llm,
        on_round_complete: Callable[[ExplorerState], Awaitable[None]] | None = None,
        max_rounds: int = 6,
        max_files: int = 50,
        repo_whitelist: frozenset[str] | None = None,
    ):
        self._executor = ripgrep_executor
        self._ast = ast_parser
        self._llm = llm
        self._on_round_complete = on_round_complete
        self._max_rounds = max_rounds
        self._max_files = max_files
        self._repo_whitelist = repo_whitelist

    async def explore(self, graph_state: "CodeAgentState") -> "CodeAgentState":
        """一次性跑完多轮探索，返回写回的 graph_state。"""
        state = self._init_from_graph_state(graph_state)
        while not self._is_converged(state):
            await self._run_one_round(state)
            if self._on_round_complete:
                await self._on_round_complete(state)
        return self._write_back_to_graph_state(graph_state, state)

    def _init_from_graph_state(self, graph_state: dict) -> ExplorerState:
        """从 LangGraph state 填充 ExplorerState。"""
        return ExplorerState(
            round=0,
            ripgrep_results=list(graph_state.get("ripgrep_results", [])),
            expanded_context=list(graph_state.get("expanded_context", [])),
            fallback_layer=graph_state.get("fallback_layer", 0),
            call_depth=graph_state.get("call_depth", 0),
            query=graph_state.get("query", graph_state.get("original_query", "")),
            entities=dict(graph_state.get("entities", {})),
            candidate_repos=list(graph_state.get("candidate_repos", [])),
        )

    def _write_back_to_graph_state(self, graph_state: dict, state: ExplorerState) -> dict:
        """把 ExplorerState 写回 graph_state。"""
        graph_state["ripgrep_results"] = state.ripgrep_results
        graph_state["expanded_context"] = state.expanded_context
        graph_state["rounds_used"] = state.round
        if state.convergence:
            graph_state["assessment"] = state.convergence.verdict
            graph_state["convergence_reason"] = f"{state.convergence.level}:{state.convergence.reason}"
            graph_state["final_results"] = state.ripgrep_results
        else:
            graph_state["convergence_reason"] = "no_assessment"
            graph_state["final_results"] = state.ripgrep_results
        return graph_state

    def _is_converged(self, state: ExplorerState) -> bool:
        """收敛判定：verdict ∈ {"converge", "cap", "stuck"} 之一视为终止条件。

        注意："cap" 也是终止条件 —— 反思空术语 / 连续 2 次反思无进展 /
        max_rounds 到达都会把 verdict 设为 "cap"，explore() 主循环必须在
        下一轮开始前停止（spec §2.4 错误矩阵 + plan 第 9 行）。
        只识别 "converge" 会让 cap 设置后仍多跑一轮，浪费 LLM/ripgrep。
        """
        if state.convergence is None:
            return False
        return state.convergence.verdict in {"converge", "cap", "stuck"}

    async def _run_one_round(self, state: ExplorerState) -> None:
        """一轮 6 阶段：refine→glob→grep→read→expand→assess→reflect（Task 4 + P1/P2/P3 对策见 §3.5.1）。

        反思触发条件：_assess 返回 should_reflect=True（diminishing_returns / borderline_progress）。
        Cap 机制（硬错误）：
            1. 反思后 search_terms 全空 → verdict="cap", reason="reflection_empty_terms"
            2. 连续 2 次反思无新增文件 → verdict="cap", reason="reflection_no_progress"

        Task 5 可观测性埋点：
            - code_reflection_consecutive_no_progress Gauge：每次更新时 .set()
            - code_reflection_total.labels(outcome="capped")：cap 触发时 inc()
        """
        # Task 5 埋点：延迟导入（避免循环依赖）
        from spma.observability.code_metrics import (
            code_reflection_consecutive_no_progress,
            code_reflection_total,
        )

        state.round += 1
        state.call_depth = state.round
        await self._refine_terms(state)
        glob_hits = await self._glob(state)
        grep_hits = await self._grep(state)
        await self._read(state, glob_hits + grep_hits)
        await self._expand_via_ast(state)
        await self._assess(state)

        # Task 4：反思触发 + cap 机制
        if state.convergence and state.convergence.should_reflect:
            await self._reflect_and_replan(state)

            # 反思后空术语 → 强制 cap（硬错误）
            # 注意：必须在 _reflect_and_replan 之后，因为 drop_terms 可能清空整个集合。
            if not state.search_terms or all(
                not terms for terms in state.search_terms.values()
            ):
                from spma.agents.code.completeness import CodeCompletenessResult
                state.convergence = CodeCompletenessResult(
                    verdict="cap",
                    reason="reflection_empty_terms",
                    level="L1",
                )
                code_reflection_total.labels(outcome="capped").inc()
                return

            # 反思后无进展 → 强制 cap
            # 语义：反思触发的那一轮累计新增文件为 0（即 read+AST 合计 = 0），
            # 表示该轮探索未产出新内容。Spec §2.4 错误矩阵第 257 行
            # "new_files_this_round == 0" 即此语义（与 §2.3 数据流图的
            # previous_new_files==0 不一致，以 §2.4 错误矩阵为准）。
            # 连续 ≥ 2 次触发即视为反思无效 → 强制 cap。
            if state.new_files_this_round == 0:
                state.consecutive_no_progress_reflections += 1
                # Task 5：更新 gauge
                code_reflection_consecutive_no_progress.set(
                    state.consecutive_no_progress_reflections,
                )
                if state.consecutive_no_progress_reflections >= 2:
                    from spma.agents.code.completeness import CodeCompletenessResult
                    state.convergence = CodeCompletenessResult(
                        verdict="cap",
                        reason="reflection_no_progress",
                        level="L1",
                    )
                    code_reflection_total.labels(outcome="capped").inc()
                    return
            else:
                state.consecutive_no_progress_reflections = 0
                # Task 5：重置 gauge
                code_reflection_consecutive_no_progress.set(0)

    # ---- 6 阶段方法（占位实现，下一 Task 逐个填充）----
    async def _refine_terms(self, state: ExplorerState) -> None:
        """基于上轮 expanded_context 调 LLM 重组关键词（P3 对策）。

        首轮（round=1 且 expanded_context 空）退化：用 query + entities 构造 search_terms。
        后续轮：调 LLM 基于上轮 expanded_context 重组关键词；LLM 失败时 search_terms 保持上轮值。
        """
        if state.round == 1 and not state.expanded_context:
            # 首轮退化：直接用 query + entities
            state.search_terms = {
                "query": state.query,
                "entities_code_refs": list(state.entities.get("code_refs", []) or []),
                "entities_module": state.entities.get("module", ""),
                "refined_via": "degraded_query_entities",
            }
            return

        if self._llm is None:
            return  # 无 LLM，search_terms 保持上轮值

        # 后续轮：调 LLM 重组
        try:
            from spma.agents.code.term_builder import build_search_terms
            base = build_search_terms(state.entities)
            from spma.agents.code.prompts import REFINE_TERMS_PROMPT
            prompt = REFINE_TERMS_PROMPT.format(
                query=state.query,
                expanded_context_count=len(state.expanded_context),
                ripgrep_results_count=len(state.ripgrep_results),
            )
            resp = await self._llm.ainvoke(prompt)
            import json, re
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content.strip())
            refined = json.loads(content)
            state.search_terms = {
                "exact_terms": refined.get("exact_terms", base.get("exact_terms", [])),
                "fuzzy_terms": refined.get("fuzzy_terms", base.get("fuzzy_terms", [])),
                "tag_terms": base.get("tag_terms", []),
                "glob_patterns": refined.get("glob_patterns", []),
                "refined_via": "llm",
            }

            # ─── glob_patterns 解析（spec §4 Trace 1/2/3 + §5 错误矩阵）───
            # 仅在 LLM 成功路径跑 6 步解析，保留"保持上轮 search_terms"语义
            # （避免与 _run_one_round 的 cap 机制冲突——spec §2.2 风险评估 line 465）
            from spma.agents.code.term_builder import (
                extract_extensions_from_query,
                validate_glob_pattern,
            )

            llm_patterns = state.search_terms.get("glob_patterns", []) or []
            valid = [p for p in llm_patterns if validate_glob_pattern(p)]
            if valid:
                state.search_terms["glob_patterns"] = valid
                state.glob_patterns_resolved = "llm"
            else:
                # 降级链：query 词法抽扩展名 → **/*.* 泛底
                fallback = extract_extensions_from_query(state.query)
                if fallback:
                    state.search_terms["glob_patterns"] = fallback
                    state.glob_patterns_resolved = "fallback_query"
                else:
                    state.search_terms["glob_patterns"] = ["**/*.*"]
                    state.glob_patterns_resolved = "fallback_wildcard"
        except Exception as e:
            logger.warning(f"_refine_terms LLM 调用失败: {e}，保持上轮 search_terms")
            # glob_patterns_resolved 兜底（spec §2.2: 永远 3 选 1，不允许空）
            state.glob_patterns_resolved = "fallback_wildcard"

    async def _glob(self, state: ExplorerState) -> list[dict]:
        """调 ripgrep_executor.glob_files。"""
        try:
            return await self._executor.glob_files("**/*.py", state.candidate_repos)
        except Exception as e:
            logger.warning(f"_glob failed: {e}")
            return []

    async def _grep(self, state: ExplorerState) -> list[dict]:
        """调 ripgrep_executor.search（4 层降级由 fallback_layer 控制）。"""
        try:
            return await self._executor.search(
                state.search_terms or {},
                state.candidate_repos,
                state.fallback_layer,
            )
        except Exception as e:
            logger.warning(f"_grep failed: {e}")
            return []

    async def _read(self, state: ExplorerState, candidates: list[dict]) -> None:
        """调 ripgrep_executor.read_files；新文件追加到 expanded_context。"""
        # 过滤已 seen 的文件
        new_files = [
            c for c in candidates
            if (c.get("repo"), c.get("file_path")) not in state.seen_files
        ]
        if not new_files:
            state.new_files_this_round = 0
            return
        try:
            read_results = await self._executor.read_files(new_files)
        except Exception as e:
            logger.warning(f"_read failed: {e}")
            state.new_files_this_round = 0
            return
        added = 0
        for r in read_results:
            state.expanded_context.append(r)
            state.seen_files.add((r["repo"], r["file_path"]))
            added += 1
        state.new_files_this_round = added

    async def _expand_via_ast(self, state: ExplorerState) -> None:
        """AST 辅助（增量追加到 expanded_context）。"""
        from spma.agents.code.ast_expander import expand_via_ast
        try:
            new_expanded = await expand_via_ast(
                ripgrep_results=state.ripgrep_results,
                repo_paths=self._executor._repo_paths,
                ast_parser=self._ast,
            )
        except Exception as e:
            logger.warning(f"_expand_via_ast failed: {e}")
            return
        for f in new_expanded:
            key = (f.get("repo", ""), f.get("file_path", ""))
            if key not in state.seen_files:
                state.expanded_context.append(f)
                state.seen_files.add(key)
                state.new_files_this_round += 1
        # 维护 previous_new_files 给下一轮 stuck 判定
        # 语义：new_files_this_round = _read 新增 + AST 扩展新增（合计）
        # 这与"反思后无进展"（new_files_this_round == 0）的判定一致：
        # read 和 AST 都未新增文件才计为无进展。spec §2.4 错误矩阵无明确
        # 区分 read vs AST，故采用合计语义（Task 4 review C3 决策）。
        state.previous_new_files = state.new_files_this_round

    async def _assess(self, state: ExplorerState) -> None:
        """调 assess_code_completeness（P1 对策：放最后）。"""
        from spma.agents.code.completeness import assess_code_completeness
        try:
            outcome = await assess_code_completeness(
                ripgrep_results=state.ripgrep_results,
                expanded_context=state.expanded_context,
                entities=state.entities,
                call_depth=state.call_depth,
                new_files_this_round=state.new_files_this_round,
                fallback_layer=state.fallback_layer,
                llm=self._llm,
                previous_new_files=state.previous_new_files,
                max_files=self._max_files,
                max_rounds=self._max_rounds,
                round=state.round,
                total_files=len(state.seen_files),
                legacy_levels=False,
            )
            state.convergence = outcome
        except Exception as e:
            logger.warning(f"_assess failed: {e}，默认 expand")
            from spma.agents.code.completeness import CodeCompletenessResult
            state.convergence = CodeCompletenessResult(
                verdict="expand", level="expand", reason=f"assess_error:{e}",
            )

    async def _reflect_and_replan(self, state: ExplorerState) -> None:
        """调 LLM 反思，重新生成 search_terms（Task 3：方案 B 第三阶段 + Task 5：可观测性埋点）。

        错误处理（按 spec §2.4）：
            - LLM 超时/5xx/JSON 解析失败：跳过反思（软错误），reflection_count 不增
            - schema 违反（drop_terms 不在原 set）：ValueError，被捕获后跳过
            - 反思后 search_terms 为空：强制 cap（verdict="cap"）—— 由 Task 4 处理
            - reasoning 字段不进入 state 回写，仅 log 截断

        可观测性埋点（Task 5）：
            - code_reflection_duration_seconds.observe() — LLM 调用耗时（无论成败）
            - code_reflection_total.labels(outcome=...).inc() — outcome ∈ {skipped/failed/triggered}
            - code_reflection_search_terms_changed.inc() — search_terms 真的变更时

        注意：本方法不修改 expanded_context / seen_files / previous_new_files。
        """
        import time

        from spma.agents.code.prompts.reflection import (
            apply_reflection_decision,
            build_reflection_prompt,
            parse_reflection_response,
        )
        from spma.observability.code_metrics import (
            code_reflection_duration_seconds,
            code_reflection_search_terms_changed,
            code_reflection_total,
        )

        if self._repo_whitelist is None:
            # 无 repo_whitelist 时跳过（防御性编程；正常调用路径不进入此分支）
            code_reflection_total.labels(outcome="skipped").inc()
            return

        prompt = build_reflection_prompt(state)

        # Task 5：埋点 LLM 调用耗时（无论成败都 observe）
        start = time.monotonic()
        try:
            llm_response = await asyncio.wait_for(
                self._llm.ainvoke(prompt),
                timeout=30.0,  # 30 秒超时
            )
            code_reflection_duration_seconds.observe(time.monotonic() - start)
        except Exception as e:  # noqa: BLE001 — 软错误兜底（含 TimeoutError / 5xx / 任意 LLM SDK 异常）
            # 软错误：跳过反思（仍记录耗时）
            code_reflection_duration_seconds.observe(time.monotonic() - start)
            logger.warning(
                "reflection_llm_failed",
                extra={"error": str(e), "round": state.round},
            )
            code_reflection_total.labels(outcome="failed").inc()
            return

        try:
            raw_content = (
                llm_response.content
                if hasattr(llm_response, "content")
                else str(llm_response)
            )
            decision = parse_reflection_response(raw_content)
        except ValueError as e:
            logger.error(
                "reflection_parse_failed",
                extra={"error": str(e), "raw": str(raw_content)[:500]},
            )
            code_reflection_total.labels(outcome="failed").inc()
            return

        # Task 5：search_terms 变更埋点（记录 apply 前后的差异）
        terms_before = {
            k: list(v) if isinstance(v, list) else v
            for k, v in state.search_terms.items()
        }
        try:
            apply_reflection_decision(state, decision, self._repo_whitelist)
        except ValueError as e:
            logger.error("reflection_apply_failed", extra={"error": str(e)})
            code_reflection_total.labels(outcome="failed").inc()
            return

        # 比较 apply 后的 search_terms 与之前的差异
        terms_after = {
            k: list(v) if isinstance(v, list) else v
            for k, v in state.search_terms.items()
        }
        if terms_after != terms_before:
            code_reflection_search_terms_changed.inc()

        # reasoning 不进入 state，但记录到日志（截断 200 字符）
        if decision.reasoning:
            logger.info(
                "reflection_reasoning",
                extra={"reasoning": decision.reasoning[:200]},
            )

        code_reflection_total.labels(outcome="triggered").inc()


class ReflectionDecision(BaseModel):
    """LLM 反思输出结构（Task 1：数据契约）。

    pydantic 严格校验，防止 LLM 输出污染 state。
    extra="ignore" 容忍 LLM 输出额外字段（不被 schema 接收、不进入 state 回写）。
    """

    model_config = ConfigDict(extra="ignore")

    new_search_terms: dict[str, list[str]] = Field(
        default_factory=dict,
        description="新生成的搜索词，按 entities key 分组（module/function/concept）",
    )
    drop_terms: list[str] = Field(
        default_factory=list,
        description="已知无结果的 term（必须 ⊆ 原始 search_terms）",
    )
    add_repos: list[str] = Field(
        default_factory=list,
        description="追加的候选 repo（必须 ∈ repo_registry 白名单）",
    )
    reasoning: str = Field(
        default="",
        description="反思 reasoning（不进入 state 回写，仅 log）",
    )

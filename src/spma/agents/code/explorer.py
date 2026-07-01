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
        """收敛判定：cap_reached / goal_verified / stuck / regression / diminishing_returns / llm_judged 之一。"""
        if state.convergence is None:
            return False
        return state.convergence.verdict == "converge"

    async def _run_one_round(self, state: ExplorerState) -> None:
        """一轮 6 阶段：refine→glob→grep→read→expand→assess（P1/P2/P3 对策见 §3.5.1）。"""
        state.round += 1
        state.call_depth = state.round
        await self._refine_terms(state)
        glob_hits = await self._glob(state)
        grep_hits = await self._grep(state)
        await self._read(state, glob_hits + grep_hits)
        await self._expand_via_ast(state)
        await self._assess(state)

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
            prompt = (
                f"基于以下上轮探索结果，重组更精准的代码搜索关键词。\n"
                f"用户查询: {state.query}\n"
                f"已有 expanded_context: {len(state.expanded_context)} 个文件\n"
                f"已有 ripgrep_results: {len(state.ripgrep_results)} 个匹配\n"
                f"输出 JSON: {{\"exact_terms\": [...], \"fuzzy_terms\": [...]}}"
            )
            resp = await self._llm.ainvoke(prompt)
            import json, re
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content.strip())
            refined = json.loads(content)
            state.search_terms = {
                "exact_terms": refined.get("exact_terms", base.get("exact_terms", [])),
                "fuzzy_terms": refined.get("fuzzy_terms", base.get("fuzzy_terms", [])),
                "tag_terms": base.get("tag_terms", []),
                "refined_via": "llm",
            }
        except Exception as e:
            logger.warning(f"_refine_terms LLM 调用失败: {e}，保持上轮 search_terms")

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
        """调 LLM 反思，重新生成 search_terms（Task 3：方案 B 第三阶段）。

        错误处理（按 spec §2.4）：
            - LLM 超时/5xx/JSON 解析失败：跳过反思（软错误），reflection_count 不增
            - schema 违反（drop_terms 不在原 set）：ValueError，被捕获后跳过
            - 反思后 search_terms 为空：强制 cap（verdict="cap"）—— 由 Task 4 处理
            - reasoning 字段不进入 state 回写，仅 log 截断

        注意：本方法不修改 expanded_context / seen_files / previous_new_files。
        """
        from spma.agents.code.prompts.reflection import (
            apply_reflection_decision,
            build_reflection_prompt,
            parse_reflection_response,
        )

        # Task 5 才会定义 code_reflection_total；先尝试导入并容错
        try:
            from spma.observability.code_metrics import code_reflection_total
            _reflection_metric_available = True
        except ImportError:
            _reflection_metric_available = False
            logger.debug("code_reflection_total 未定义（Task 5 才会添加），跳过 metric 上报")

        if self._repo_whitelist is None:
            # 无 repo_whitelist 时跳过（防御性编程；正常调用路径不进入此分支）
            if _reflection_metric_available:
                code_reflection_total.labels(outcome="skipped").inc()
            return

        prompt = build_reflection_prompt(state)

        try:
            llm_response = await asyncio.wait_for(
                self._llm.ainvoke(prompt),
                timeout=30.0,  # 30 秒超时
            )
        except (asyncio.TimeoutError, Exception) as e:
            # 软错误：跳过反思
            logger.warning(
                "reflection_llm_failed",
                extra={"error": str(e), "round": state.round},
            )
            if _reflection_metric_available:
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
            if _reflection_metric_available:
                code_reflection_total.labels(outcome="failed").inc()
            return

        try:
            apply_reflection_decision(state, decision, self._repo_whitelist)
        except ValueError as e:
            logger.error("reflection_apply_failed", extra={"error": str(e)})
            if _reflection_metric_available:
                code_reflection_total.labels(outcome="failed").inc()
            return

        # reasoning 不进入 state，但记录到日志（截断 200 字符）
        if decision.reasoning:
            logger.info(
                "reflection_reasoning",
                extra={"reasoning": decision.reasoning[:200]},
            )

        if _reflection_metric_available:
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

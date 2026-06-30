"""CodeExplorer——多轮探索引擎（design-13 §3.5）。

独立于 LangGraph：通过 explore() 一次性调用，也可注入 mock 状态做单测。
6 阶段方法：refine / glob / grep / read / expand_via_ast / assess。
"""
import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, TYPE_CHECKING

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
    ):
        self._executor = ripgrep_executor
        self._ast = ast_parser
        self._llm = llm
        self._on_round_complete = on_round_complete
        self._max_rounds = max_rounds
        self._max_files = max_files

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
    async def _refine_terms(self, state: ExplorerState) -> None: ...
    async def _glob(self, state: ExplorerState) -> list[dict]: return []
    async def _grep(self, state: ExplorerState) -> list[dict]: return []
    async def _read(self, state: ExplorerState, candidates: list[dict]) -> None: ...
    async def _expand_via_ast(self, state: ExplorerState) -> None: ...
    async def _assess(self, state: ExplorerState) -> None:
        """判断本轮是否收敛——用 legacy_levels=False 保留 v2 level 名（如 goal_verified）。"""
        from spma.agents.code.completeness import assess_code_completeness

        result = await assess_code_completeness(
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
        state.convergence = result

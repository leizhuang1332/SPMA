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

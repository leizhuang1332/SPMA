"""共享类型定义模块。

SPMA 所有 Agent 和 API 层的类型契约集中于此。
包括: Agent状态基类, Worker输出契约, 实体抽取模型, 意图分类模型, 收敛判断模型, 检索日志模型, 通用类型
"""

from spma.models.agent_state import AgentState
from spma.models.classification import ClassificationResult, QueryType, SourceType
from spma.models.common import AgentTrace, DataFreshness, RequestMetadata
from spma.models.convergence import (
    AssessmentVerdict,
    ConvergenceResult,
    ConvergenceSource,
)
from spma.models.entities import (
    CompletenessLevel,
    ExtractedEntities,
    WorkerEntities,
)
from spma.models.search_log import SearchLogEntry
from spma.models.worker_output import (
    Citation,
    DegradationInfo,
    DiscoveredEntities,
    WorkerDispatch,
    WorkerOutput,
)

__all__ = [
    "AgentState",
    "ClassificationResult",
    "QueryType",
    "SourceType",
    "AgentTrace",
    "DataFreshness",
    "RequestMetadata",
    "ConvergenceResult",
    "ConvergenceSource",
    "AssessmentVerdict",
    "ExtractedEntities",
    "WorkerEntities",
    "CompletenessLevel",
    "SearchLogEntry",
    "WorkerOutput",
    "WorkerDispatch",
    "Citation",
    "DegradationInfo",
    "DiscoveredEntities",
]

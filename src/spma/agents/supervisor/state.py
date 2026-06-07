"""Supervisor Agent 专属状态定义。

设计依据: SPMA-design-01 §1 Agent状态数据模型
"""

from spma.models.agent_state import AgentState
from spma.models.classification import ClassificationResult
from spma.models.entities import ExtractedEntities
from spma.models.worker_output import WorkerOutput


class SupervisorState(AgentState, total=False):
    """Supervisor Agent 专属状态字段。"""

    original_query: str
    classification: ClassificationResult
    entities: ExtractedEntities
    rewritten_queries: dict[str, str]
    worker_outputs: list[WorkerOutput]
    quality_scores: dict[str, float]
    reschedule_count: int
    final_results: list[dict]

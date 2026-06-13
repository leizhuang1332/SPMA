"""Supervisor Agent 专属状态定义。

设计依据: SPMA-design-01 §1 Agent状态数据模型
"""

import operator
from typing import Annotated

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
    # Annotated reducer 使得 Send API 并行派发的多个 worker 输出能通过 operator.add
    # （列表拼接）自然收敛，避免后写入者覆盖先写入者
    worker_outputs: Annotated[list[WorkerOutput], operator.add]
    quality_scores: dict[str, float]
    reschedule_count: int
    final_results: list[dict]

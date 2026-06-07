"""Supervisor 的 Send API 并行派发逻辑。

设计依据: API-02 第三节 Supervisor→Worker 派发协议
"""

from spma.models.worker_output import WorkerDispatch


def build_dispatches(state: "SupervisorState") -> list[dict]:
    """根据分类结果构造 Send API 的并行派发列表。"""
    raise NotImplementedError


def collect_worker_outputs(worker_outputs: list["WorkerOutput"]) -> dict[str, "WorkerOutput"]:
    """LangGraph reducer: 收集并索引 Worker 返回结果。"""
    raise NotImplementedError

"""检索日志数据结构。

设计依据: SPMA-design-02 §1.5.3 埋点日志结构
"""

from typing import TypedDict, NotRequired


# 使用函数式语法以支持 $schema 字段名（$ 在 class 语法中是非法标识符）
SearchLogEntry = TypedDict("SearchLogEntry", {
    "$schema": NotRequired[str],
    "log_id": NotRequired[str],
    "timestamp": NotRequired[str],
    "worker_type": NotRequired[str],
    "worker_version": NotRequired[str],
    "query_id": NotRequired[str],
    "query_text": NotRequired[str],
    "query_type": NotRequired[str],
    "trigger": NotRequired[str],
    "entities": NotRequired[dict],
    "agent_rounds": NotRequired[int],
    "convergence_reason": NotRequired[str],
    "latency_ms": NotRequired[int],
    "feedback": NotRequired[dict],
}, total=False)

"""硬编码常量——Agent 收敛参数默认值、模型名、超时值、权重默认值。

设计依据: SPMA-design-07 §2 收敛契约
"""

DEFAULT_MAX_ROUNDS = {
    "supervisor": 5,
    "doc": 3,
    "code": 3,
    "sql": 5,
    "synthesis": 2,
}

DEFAULT_TIMEOUT_MS = {
    "supervisor": 5000,
    "doc": 2000,
    "code": 2000,
    "sql": 3000,
    "synthesis": 2000,
    "hard_limit": 10000,
}


QUALITY_THRESHOLD = 0.6
MAX_RESCHEDULE_ATTEMPTS = 2
RRF_K = 60
DEFAULT_RRF_WEIGHTS = {"doc": 1.0, "code": 1.0, "sql": 1.0}

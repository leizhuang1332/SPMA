"""查询改写流水线——6种可插拔改写方案。

设计依据: SPMA-design-01 第八节 查询改写设计
"""


async def normalize_query(user_query: str) -> str:
    """方案1: 标准化——同义词映射表替换。始终开启，~1ms。"""
    raise NotImplementedError


async def expand_query(user_query: str) -> list[str]:
    """方案2: 扩展——LLM生成3-5个相关关键词。始终开启，~300ms。"""
    raise NotImplementedError


async def decompose_query(user_query: str) -> list[dict]:
    """方案3: 分解——跨源查询时拆分为独立子查询。条件触发，~500ms。"""
    raise NotImplementedError


async def hyde_generate(user_query: str) -> str:
    """方案4: HyDE——LLM生成假设性文档用于向量检索。条件触发，~1500ms。"""
    raise NotImplementedError


async def step_back_rewrite(user_query: str) -> str:
    """方案5: 退一步改写——具体问题→更广泛的背景问题。Phase 3+，~2000ms。"""
    raise NotImplementedError


async def context_aware_rewrite(user_query: str, history: list) -> str:
    """方案6: 上下文感知改写——多轮对话中指代词消解。Phase 3+。"""
    raise NotImplementedError

"""LLM 客户端——Haiku/Sonnet API + Qwen3-8B vLLM 本地。

统一接口: chat(messages, model, **kwargs) → str
动态模型选择: 运行时按 state 自动切换 Haiku/Sonnet
指数退避重试: tenacity, 429→重试3次, multiplier=0.5s, max_wait=2s
降级: 非 429 错误直接降级到 Qwen3-8B
"""

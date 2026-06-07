"""Langfuse 集成——trace → span → generation 嵌套。

Agent 循环追踪: round N → search → assess → convergence
Token 成本: 自动从 LLM 响应中提取 usage 信息
Prompt 版本: 通过 Langfuse Prompt Management 管理
"""

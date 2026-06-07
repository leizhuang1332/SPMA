"""Redis 缓存——热点问答(TTL=1h) + 查询结果(TTL=5min) + LLM翻译(TTL=24h)。

写入策略: Write-through(Agent状态), Write-around(热点问答), Write-through(翻译)

设计依据: API-06 §2 缓存契约
"""

"""审计日志——每次查询异步写入 PostgreSQL audit_logs 表。

记录: 用户/时间/原始query/分类/Agent结果/成本/降级级别
不阻塞 Agent 循环（asyncio.create_task）

设计依据: API-00 §6 审计日志结构
"""

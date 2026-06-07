"""Supervisor Agent — 编排中枢。

职责: 理解用户意图 → 抽取关键实体 → 改写查询 → Send API 并行派发
     → 收集 Worker 结果 → 质量评估 → 收敛/重调度。

收敛契约: ≤5轮, 超时5s
设计依据: SPMA-design-01
"""

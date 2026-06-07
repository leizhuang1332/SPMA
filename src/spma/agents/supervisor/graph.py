"""Supervisor Agent 的 LangGraph StateGraph 定义。

构建模式:
  分类+抽取(Round 1) → Send API 并行派发 → fan-in 收集
  → 质量评估 → 评分≥0.6 收敛 / <0.6 + 重调度<2 → 调整参数重派

设计依据: SPMA-design-01 §1 编排循环总览
"""

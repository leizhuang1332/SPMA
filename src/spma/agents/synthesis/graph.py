"""Synthesis Agent 的 LangGraph StateGraph 定义。

节点: RRF融合 → LLM生成初稿 → 引用完整性检查
条件边: 不够 → 修正回到生成 / 够了 → END

设计依据: API-04 Synthesis Agent
"""

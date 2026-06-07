"""Doc Agent 的 LangGraph StateGraph 定义。

节点: search(混合检索) → assess(完备度判断)
条件边: 不够 → 线索扩展 → 回到search / 够了 → END

设计依据: SPMA-design-02 Agent循环图
"""

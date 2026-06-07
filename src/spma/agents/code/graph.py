"""Code Agent 的 LangGraph StateGraph 定义。

节点: ripgrep搜索 → 完备度判断
条件边: 不够 → 调用链展开 → 回到ripgrep / 够了 → END

设计依据: SPMA-design-03 Agent循环图
"""

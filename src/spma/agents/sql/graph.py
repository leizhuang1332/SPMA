"""SQL Agent 的 LangGraph StateGraph 定义。

节点: generate(LLM SQL生成) → guard(SQL Guard) → execute(只读执行) → verify(语义验证)
条件边: guard失败→带错误回到generate / verify不通过→带异常回到generate / 通过→END

设计依据: SPMA-design-04 Agent循环图
"""

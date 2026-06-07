"""SQL Agent — Text-to-SQL 执行Agent。

执行Agent。Schema RAG → LLM SQL生成 → SQL Guard 五层校验
→ 只读副本执行 → 语义验证 → 不够 → 携带错误反馈重新生成。

收敛契约: ≤5轮, 超时3s
设计依据: SPMA-design-04
"""

"""Doc Agent 完备度判断。

确定性条件: 结果≥5条 AND req_ids命中 → 自动收敛（不调LLM）
LLM兜底: 确定性条件不满足 → Haiku判断是否充足

设计依据: SPMA-design-02 收敛契约
"""

"""Synthesis Agent — 审计融合Agent。

审计Agent。RRF融合多Worker引用 → LLM生成初稿
→ 引用完整性检查 → 跨源一致性检查 → 问题覆盖度检查 → 不够 → 修正。

收敛契约: ≤2轮, 超时2s
设计依据: API-04
"""

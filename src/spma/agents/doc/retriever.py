"""Doc Agent 混合检索——BM25 + BGE-M3 向量检索 + RRF 融合。

分层权重: precise(BM25主导) / semantic(向量主导) / hybrid(等权)

设计依据: SPMA-design-02 §1 检索策略
"""

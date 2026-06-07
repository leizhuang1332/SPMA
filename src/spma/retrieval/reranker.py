"""重排序——RRF 等权/加权融合 + BGE-Reranker v2 M3 Cross-encoder。

Phase 1-2: RRF 等权融合（k=60）
Phase 2: 按 query_type 分层权重 (precise/semantic/hybrid)
Phase 3: BGE-Reranker v2 M3 对 RRF Top-20 精排

设计依据: SPMA-design-02 §1.5 混合检索权重确定
"""

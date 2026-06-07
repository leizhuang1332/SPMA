"""混合检索编排——BM25 + 向量并行检索 → RRF 融合 → 可选 Reranker。

编排流程:
1. 并行调用 BM25.search() 和 vector_store.search()
2. RRF 融合两个 Top-20 为 Top-10
3. (Phase 3+) BGE-Reranker 对 Top-20 精排
"""

"""PGVector 向量存储客户端。

索引: HNSW (m=16, ef_construction=200, ef_search=100)
距离: cosine
能力: 向量检索 + SQL JOIN 元数据过滤（同一事务内）
"""
from spma.infrastructure.circuit_breaker import circuit_breaker

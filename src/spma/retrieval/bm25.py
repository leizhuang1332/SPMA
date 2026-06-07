"""BM25 关键词检索。

Phase 1-2: PostgreSQL tsvector + zhparser 中文分词
Phase 3+: Elasticsearch ik_smart 中文分词 + kNN 向量搜索
"""

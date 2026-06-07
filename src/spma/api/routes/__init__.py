"""API 路由模块——对应 API-01 文档的 8 个端点。

- query: POST /api/v1/query + /query/stream (SSE)
- session: GET/DELETE /api/v1/session/{id}
- feedback: POST /api/v1/feedback
- health: GET /api/v1/health + /agent-card
- ingestion: POST /ingest/* + GET /ingest/status + GET /ingest/freshness
- admin: GET/PUT feature-flags + GET/POST degradation
"""

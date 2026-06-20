"""IngestionController — 摄入管道编排层。

职责: 参数校验 → 创建 run → 异步执行 pipeline → 更新 stats → 返回响应
纯编排层，不包含业务逻辑。
"""

import asyncio
import logging

from spma.api.schemas.ingestion import (
    DocIngestionRequest,
    CodeIngestionRequest,
    SchemaIngestionRequest,
    IngestionResponse,
    PipelineRunDetail,
    IngestionResult,
    DocIngestionSource,
)

logger = logging.getLogger(__name__)


class IngestionController:
    """摄入管道编排控制器。"""

    def __init__(
        self,
        doc_pipeline,
        code_pipeline,
        sql_pipeline,
        run_store,
        synonym_map,
        freshness_service,
        config: dict | None = None,
    ):
        self._doc_pipeline = doc_pipeline
        self._code_pipeline = code_pipeline
        self._sql_pipeline = sql_pipeline
        self._run_store = run_store
        self._synonym_map = synonym_map
        self._freshness = freshness_service
        self._config = config or {}

    async def ingest_documents(self, request: DocIngestionRequest) -> IngestionResponse:
        """手动触发文档摄入 (API-05 §3.1)。"""
        if request.options.dry_run:
            return IngestionResponse(
                pipeline_run_id="dry-run",
                source=request.source.value,
                mode=request.mode,
                status="dry_run",
            )
        run_id = await self._run_store.create(
            pipeline_type="doc",
            source=request.source.value,
            mode=request.mode,
            created_by="manual",
        )

        async def _run():
            result = await self._doc_pipeline.run(request)
            if result:
                await self._run_store.update(
                    run_id=run_id,
                    status=result.status,
                    stats=result.stats,
                    errors=result.errors,
                )

        asyncio.create_task(_run())
        return IngestionResponse(
            pipeline_run_id=run_id,
            source=request.source.value,
            mode=request.mode,
            status="running",
        )

    async def ingest_code(self, request: CodeIngestionRequest) -> IngestionResponse:
        """手动触发代码仓库摄入 (API-05 §4.1)。"""
        run_id = await self._run_store.create(
            pipeline_type="code",
            source=None,
            mode=request.mode,
            created_by="manual",
        )

        async def _run():
            result = await self._code_pipeline.run(
                repos=request.repos,
                mode=request.mode,
                options=request.options,
            )
            if result:
                await self._run_store.update(
                    run_id=run_id,
                    status=result.status,
                    stats=result.stats,
                    errors=result.errors,
                )

        asyncio.create_task(_run())
        return IngestionResponse(
            pipeline_run_id=run_id,
            mode=request.mode,
            status="running",
        )

    async def ingest_schema(self, request: SchemaIngestionRequest) -> IngestionResponse:
        """手动触发 SQL Schema 摄入 (API-05 §5.1)。"""
        run_id = await self._run_store.create(
            pipeline_type="sql",
            source=None,
            mode=request.mode,
            created_by="manual",
        )

        async def _run():
            result = await self._sql_pipeline.run(
                databases=request.databases,
                mode=request.mode,
                options=request.options,
            )
            if result:
                await self._run_store.update(
                    run_id=run_id,
                    status=result.status,
                    stats=result.stats,
                    errors=result.errors,
                )

        asyncio.create_task(_run())
        return IngestionResponse(
            pipeline_run_id=run_id,
            mode=request.mode,
            status="running",
        )

    async def get_pipeline_status(self) -> dict:
        """查询全局摄入管道状态 (API-05 §6.1)。"""
        pipelines = {}
        for ptype in ["doc", "code", "sql"]:
            latest = await self._run_store.get_latest(ptype)
            if latest:
                pipelines[ptype] = {
                    "status": "healthy" if latest["status"] == "completed" else "degraded",
                    "last_run_at": latest.get("completed_at"),
                    "last_run_status": latest["status"],
                    "stats": latest.get("stats", {}),
                }
            else:
                pipelines[ptype] = {
                    "status": "unknown",
                    "last_run_at": None,
                    "last_run_status": None,
                    "stats": {},
                }
        freshness = await self._freshness.get_freshness()
        return {"pipelines": pipelines, "freshness": freshness["freshness"]}

    async def get_pipeline_run(self, run_id: str) -> PipelineRunDetail | None:
        """查询特定运行状态 (API-05 §6.2)。"""
        row = await self._run_store.get(run_id)
        if not row:
            return None
        started = row.get("started_at")
        completed = row.get("completed_at")
        duration = None
        if started and completed:
            try:
                from datetime import datetime

                s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
                c = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
                duration = int((c - s).total_seconds())
            except (ValueError, TypeError):
                pass
        return PipelineRunDetail(
            pipeline_run_id=row["pipeline_run_id"],
            pipeline_type=row["pipeline_type"],
            status=row["status"],
            started_at=str(row.get("started_at")) if row.get("started_at") else None,
            completed_at=str(row.get("completed_at")) if row.get("completed_at") else None,
            duration_seconds=duration,
            stats=row.get("stats", {}),
            errors=row.get("errors", []),
        )

    async def get_freshness(self) -> dict:
        return await self._freshness.get_freshness()

    async def refresh_synonym_map(
        self,
        sources: list[str],
        auto_apply_high_confidence: bool = True,
        confidence_threshold: float = 0.9,
    ) -> int:
        threshold = confidence_threshold if auto_apply_high_confidence else 1.0
        return await self._synonym_map.refresh(sources, threshold)

    async def query_synonym_map(self, status: str = "all", limit: int = 100) -> dict:
        return await self._synonym_map.query(status=status, limit=limit)

    async def handle_confluence_webhook(self, payload: dict) -> IngestionResponse | None:
        """处理 Confluence Webhook (API-05 §8.1)。"""
        page_id = payload.get("page_id", "")
        if not page_id:
            return None
        run_id = await self._run_store.create(
            pipeline_type="doc",
            source=DocIngestionSource.CONFLUENCE,
            mode="incremental",
            created_by="webhook",
        )

        async def _run():
            result = await self._doc_pipeline.run_from_webhook(payload)
            if result:
                await self._run_store.update(
                    run_id=run_id,
                    status=result.status,
                    stats=result.stats,
                    errors=result.errors,
                )

        asyncio.create_task(_run())
        return IngestionResponse(
            pipeline_run_id=run_id,
            source=DocIngestionSource.CONFLUENCE,
            mode="incremental",
            status="running",
        )

    async def handle_git_webhook(
        self, repo_name: str, changed_files: list[str]
    ) -> IngestionResponse | None:
        """处理 Git Webhook (API-05 §8.2)。"""
        if not repo_name:
            return None
        from spma.api.schemas.ingestion import CodeIngestionOptions

        run_id = await self._run_store.create(
            pipeline_type="code",
            source=None,
            mode="incremental",
            created_by="webhook",
        )

        async def _run():
            result = await self._code_pipeline.run(
                repos=[repo_name],
                mode="incremental",
                options=CodeIngestionOptions(),
                changed_files={repo_name: changed_files},
            )
            if result:
                await self._run_store.update(
                    run_id=run_id,
                    status=result.status,
                    stats=result.stats,
                    errors=result.errors,
                )

        asyncio.create_task(_run())
        return IngestionResponse(
            pipeline_run_id=run_id,
            mode="incremental",
            status="running",
        )

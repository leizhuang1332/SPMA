"""摄入管理 REST API — 8 个端点 (API-05 §2-7, §10)。"""
from fastapi import APIRouter, Depends, HTTPException, Query
from spma.api.dependencies import get_ingestion_controller
from spma.api.middleware.auth import get_current_admin, get_current_user
from spma.api.schemas.ingestion import (
    DocIngestionRequest, CodeIngestionRequest, SchemaIngestionRequest, SynonymRefreshRequest,
)

router = APIRouter()


@router.post("/ingest/documents")
async def trigger_doc_ingestion(body: DocIngestionRequest, controller=Depends(get_ingestion_controller),
                                 _admin=Depends(get_current_admin)):
    """POST /api/v1/ingest/documents — 手动触发 PRD 文档摄入 (API-05 §3.1)。"""
    return await controller.ingest_documents(body)


@router.post("/ingest/code")
async def trigger_code_ingestion(body: CodeIngestionRequest, controller=Depends(get_ingestion_controller),
                                  _admin=Depends(get_current_admin)):
    """POST /api/v1/ingest/code — 手动触发代码仓库摄入 (API-05 §4.1)。"""
    return await controller.ingest_code(body)


@router.post("/ingest/schema")
async def trigger_schema_ingestion(body: SchemaIngestionRequest, controller=Depends(get_ingestion_controller),
                                    _admin=Depends(get_current_admin)):
    """POST /api/v1/ingest/schema — 手动触发 SQL Schema 摄入 (API-05 §5.1)。"""
    return await controller.ingest_schema(body)


@router.get("/ingest/status")
async def get_ingestion_status(controller=Depends(get_ingestion_controller), _admin=Depends(get_current_admin)):
    """GET /api/v1/ingest/status — 查询摄入管道状态 (API-05 §6.1)。"""
    return await controller.get_pipeline_status()


@router.get("/ingest/status/{pipeline_run_id}")
async def get_ingestion_run_status(pipeline_run_id: str, controller=Depends(get_ingestion_controller),
                                    _admin=Depends(get_current_admin)):
    """GET /api/v1/ingest/status/{pipeline_run_id} — 查询特定运行状态 (API-05 §6.2)。"""
    result = await controller.get_pipeline_run(pipeline_run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Pipeline run {pipeline_run_id} not found")
    return result


@router.get("/ingest/freshness")
async def get_freshness(controller=Depends(get_ingestion_controller), _user=Depends(get_current_user)):
    """GET /api/v1/ingest/freshness — 查询知识新鲜度 (API-05 §7)。"""
    return await controller.get_freshness()


@router.post("/ingest/synonym-map/refresh")
async def refresh_synonym_map(body: SynonymRefreshRequest, controller=Depends(get_ingestion_controller),
                               _admin=Depends(get_current_admin)):
    """POST /api/v1/ingest/synonym-map/refresh — 刷新同义词映射表 (API-05 §10.1)。"""
    added = await controller.refresh_synonym_map(
        sources=body.sources, auto_apply_high_confidence=body.auto_apply_high_confidence,
        confidence_threshold=body.confidence_threshold,
    )
    return {"status": "ok", "entries_added": added}


@router.get("/ingest/synonym-map")
async def query_synonym_map(status: str = Query("all"), limit: int = Query(100, ge=1, le=500),
                             controller=Depends(get_ingestion_controller), _admin=Depends(get_current_admin)):
    """GET /api/v1/ingest/synonym-map — 查询同义词映射表 (API-05 §10.2)。"""
    return await controller.query_synonym_map(status=status, limit=limit)

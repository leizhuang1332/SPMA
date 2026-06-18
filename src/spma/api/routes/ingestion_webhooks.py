"""Webhook 端点——Confluence + Git 外部触发器 (API-05 §8)。"""
import hashlib
import hmac
import json
import logging
import os
from fastapi import APIRouter, Depends, HTTPException, Request
from spma.api.dependencies import get_ingestion_controller
from spma.ingestion.code.git_manager import GitManager

logger = logging.getLogger(__name__)
router = APIRouter()


def _verify_confluence_token(request: Request) -> dict:
    expected = os.environ.get("CONFLUENCE_WEBHOOK_SECRET", "")
    if not expected:
        return {}
    actual = request.headers.get("X-Confluence-Webhook-Token", "")
    if not actual or not hmac.compare_digest(actual, expected):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    return {}


@router.post("/webhooks/confluence")
async def confluence_webhook(request: Request, controller=Depends(get_ingestion_controller),
                              _verified=Depends(_verify_confluence_token)):
    """POST /api/v1/webhooks/confluence (API-05 §8.1)。"""
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    page_id = payload.get("page_id", "")
    if not page_id:
        raise HTTPException(status_code=400, detail="Missing page_id")
    # Redis 防抖
    try:
        from spma.infrastructure.cache import get_cache_service
        cache = get_cache_service()
        version = payload.get("version", 0)
        debounce_key = f"ingest:debounce:confluence:{page_id}:{version}"
        if cache and await cache.get(debounce_key):
            return {"status": "debounced"}
        if cache:
            await cache.set(debounce_key, "1", ttl=30)
    except Exception:
        pass
    result = await controller.handle_confluence_webhook(payload)
    return result if result else {"status": "ignored"}


@router.post("/webhooks/git")
async def git_webhook(request: Request, controller=Depends(get_ingestion_controller)):
    """POST /api/v1/webhooks/git (API-05 §8.2)。"""
    raw_body = await request.body()
    # HMAC 验签
    expected = os.environ.get("GIT_WEBHOOK_SECRET", "")
    if expected:
        sig_header = request.headers.get("X-Hub-Signature-256", "")
        if not sig_header.startswith("sha256="):
            raise HTTPException(status_code=401, detail="Invalid signature format")
        computed = hmac.new(expected.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(f"sha256={computed}", sig_header):
            raise HTTPException(status_code=401, detail="Signature mismatch")
    try:
        payload = json.loads(raw_body)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    git_manager = GitManager()
    parsed = await git_manager.handle_webhook(payload)
    if parsed is None:
        return {"status": "ignored", "reason": "not a push event"}
    repo_name = parsed["repo_name"]
    branch = parsed["branch"]
    changed_files = parsed["changed_files"]
    # Redis 防抖
    try:
        from spma.infrastructure.cache import get_cache_service
        cache = get_cache_service()
        debounce_key = f"ingest:debounce:git:{repo_name}:{branch}"
        if cache and await cache.get(debounce_key):
            return {"status": "debounced"}
        if cache:
            await cache.set(debounce_key, "1", ttl=10)
    except Exception:
        pass
    result = await controller.handle_git_webhook(repo_name, changed_files)
    return result if result else {"status": "ignored"}

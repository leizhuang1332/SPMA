"""Canary 灰度控制 API(运维调用,需要 canary:write 权限)。"""
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/canary", tags=["canary"])


class AdvanceRequest(BaseModel):
    strategy_name: str
    stage: str
    operator: str


class HaltRequest(BaseModel):
    strategy_name: str
    operator: str
    reason: str


class RollbackRequest(BaseModel):
    weights_set_id: int
    approver: str


# 注入容器(由 app factory 注入)
_canary = None
_rollback = None


def set_canary(canary, rollback):
    global _canary, _rollback
    _canary = canary
    _rollback = rollback


@router.post("/advance")
async def canary_advance(req: AdvanceRequest):
    if _canary is None:
        raise HTTPException(503, "canary not initialized")
    await _canary.advance(req.strategy_name, req.stage, operator=req.operator)
    return {"status": "ok"}


@router.post("/halt")
async def canary_halt(req: HaltRequest):
    if _canary is None:
        raise HTTPException(503, "canary not initialized")
    await _canary.halt(req.strategy_name, operator=req.operator, reason=req.reason)
    return {"status": "halted"}


@router.post("/rollback")
async def rollback(req: RollbackRequest):
    if _rollback is None:
        raise HTTPException(503, "rollback not initialized")
    success = await _rollback.rollback_to(req.weights_set_id, approver=req.approver)
    if not success:
        raise HTTPException(404, "version not found")
    return {"status": "ok"}


@router.get("/versions")
async def list_versions():
    if _rollback is None:
        raise HTTPException(503, "rollback not initialized")
    return await _rollback.list_versions()

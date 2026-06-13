"""LLM 管理端点——运行时查询和热切换 router 配置。"""

import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from spma.llm.router import LLMRouter
from spma.llm.providers.base import LLMConfigError

logger = logging.getLogger(__name__)

router = APIRouter()


class RoleSwitchRequest(BaseModel):
    provider: str
    model: str


@router.post("/api/v1/admin/llm/role/{role_name}")
async def switch_role(role_name: str, body: RoleSwitchRequest):
    """热切换指定 role 的 provider/model——零延迟生效。"""
    try:
        router_instance = LLMRouter.get_instance()
        router_instance.set_role(role_name, body.provider, body.model)
        new_cfg = router_instance.get_role_config(role_name)
        return {
            "status": "ok",
            "role": role_name,
            "current": {
                "provider": new_cfg.provider,
                "model": new_cfg.model,
            },
        }
    except LLMConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/admin/llm/roles")
async def list_roles():
    """查询所有 role 的当前配置。"""
    try:
        router_instance = LLMRouter.get_instance()
        roles = router_instance.list_roles()
        return {
            role_name: {
                "provider": cfg.provider,
                "model": cfg.model,
                "max_tokens": cfg.max_tokens,
                "temperature": cfg.temperature,
                "thinking": cfg.thinking,
            }
            for role_name, cfg in roles.items()
        }
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.get("/api/v1/admin/llm/providers")
async def list_providers():
    """查询所有已注册 provider 及健康状态。"""
    try:
        router_instance = LLMRouter.get_instance()
        providers = router_instance.list_providers()
        result = {}
        for pname in providers:
            provider = router_instance._providers.get(pname)
            healthy = await provider.ping() if provider else False
            result[pname] = {
                "type": provider.name if provider else "unknown",
                "healthy": healthy,
            }
        return result
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

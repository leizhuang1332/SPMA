"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览 + Phase 4 hardening design spec §6.3
"""

import logging
import os

import yaml
from fastapi import FastAPI, HTTPException, Depends, Query
from pydantic import BaseModel

from spma.infrastructure.degradation import DegradationManager
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker, has_circuit_breaker
from spma.api.dependencies import get_degradation_manager
from spma.api.routes.llm_admin import router as llm_admin_router

logger = logging.getLogger(__name__)


def _resolve_config_path() -> str:
    """解析 spma 配置文件路径。优先级: 环境变量 > spma.local.yaml > spma.yaml"""
    yaml_path = os.environ.get("SPMA_CONFIG_PATH", "")
    if yaml_path:
        return yaml_path
    config_dir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "config")
    local_config = os.path.join(config_dir, "spma.local.yaml")
    default_config = os.path.join(config_dir, "spma.yaml")
    return local_config if os.path.exists(local_config) else default_config


# --- Request/Response Models ---

class DegradationTriggerRequest(BaseModel):
    level: str  # L0-L5
    reason: str = "manual trigger"
    operator: str = "admin"


# --- Route Handlers ---

async def get_degradation_status(
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """GET /api/v1/admin/degradation/status — 当前降级状态。"""
    return manager.get_status()


async def trigger_degradation(
    body: DegradationTriggerRequest,
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """POST /api/v1/admin/degradation/trigger — 手动触发降级。"""
    valid_levels = ["L0", "L1", "L2", "L3", "L4", "L5"]
    if body.level not in valid_levels:
        raise HTTPException(400, f"Invalid level: {body.level}. Must be one of {valid_levels}")
    await manager.manual_degrade(body.level, body.reason, body.operator)
    return {"status": "ok", "current_level": manager.current_level}


async def recover_degradation(
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """POST /api/v1/admin/degradation/recover — 手动恢复。"""
    await manager.manual_recover()
    return {"status": "ok", "current_level": manager.current_level}


async def get_degradation_history(
    limit: int = Query(50, ge=1, le=200),
    manager: DegradationManager = Depends(get_degradation_manager),
):
    """GET /api/v1/admin/degradation/history — 降级历史。"""
    history = manager.get_history(limit=limit)
    result = []
    for h in history:
        if hasattr(h, '__dict__'):
            d = {}
            for k, v in h.__dict__.items():
                if hasattr(v, 'value'):
                    d[k] = v.value
                else:
                    d[k] = str(v) if not isinstance(v, (int, float, str, list, dict, type(None), bool)) else v
            result.append(d)
        else:
            result.append(h)
    return result


async def list_circuit_breakers():
    """GET /api/v1/admin/circuit-breakers — 所有熔断器状态。"""
    stats = get_all_stats()
    return [
        {
            "name": s.name,
            "state": s.state.value,
            "failure_count": s.failure_count,
            "total_failures": s.total_failures,
            "total_successes": s.total_successes,
            "opened_at": s.opened_at,
        }
        for s in stats
    ]


async def reset_circuit_breaker(name: str):
    """POST /api/v1/admin/circuit-breakers/{name}/reset — 手动重置熔断器。"""
    if not has_circuit_breaker(name):
        raise HTTPException(404, f"Circuit breaker '{name}' not found")
    cb = get_circuit_breaker(name)
    await cb.reset()
    return {"status": "ok", "name": name, "state": cb.state.value}


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。"""
    app = FastAPI(
        title="SPMA",
        version="0.2.0",
        description="企业级多源RAG智能问答系统",
    )

    # 注册查询路由
    from spma.api.routes.query import router as query_router
    app.include_router(query_router)

    # 管理 API — LLM 路由
    app.include_router(llm_admin_router)

    # 健康检查
    @app.get("/health")
    async def health_check():
        return {"status": "ok", "version": "0.2.0"}

    # 管理 API — 降级
    app.add_api_route(
        "/api/v1/admin/degradation/status",
        get_degradation_status, methods=["GET"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/trigger",
        trigger_degradation, methods=["POST"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/recover",
        recover_degradation, methods=["POST"],
    )
    app.add_api_route(
        "/api/v1/admin/degradation/history",
        get_degradation_history, methods=["GET"],
    )

    # 管理 API — 熔断器
    app.add_api_route(
        "/api/v1/admin/circuit-breakers",
        list_circuit_breakers, methods=["GET"],
    )
    app.add_api_route(
        "/api/v1/admin/circuit-breakers/{name}/reset",
        reset_circuit_breaker, methods=["POST"],
    )

    # 启动时初始化 LLMRouter 单例
    @app.on_event("startup")
    async def startup_llm_router():
        """启动时初始化 LLMRouter 单例。"""
        from spma.llm.router import LLMRouter

        yaml_path = _resolve_config_path()
        LLMRouter.initialize(os.path.abspath(yaml_path))

    # 启动时初始化 Code Agent 基础设施依赖
    @app.on_event("startup")
    async def startup_code_agent_deps():
        """初始化 db_pool → FilePathCache → RipgrepExecutor → ASTParser 链路。

        复用 spma.yaml 的 connections.postgres.readonly_replica DSN。
        任一组件初始化失败优雅降级，不阻塞应用启动。
        """

        # 1. 读取配置
        try:
            yaml_path = _resolve_config_path()
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("无法读取 YAML 配置，跳过 Code Agent 依赖初始化: %s", e)
            return

        postgres_cfg = raw.get("spma", {}).get("connections", {}).get("postgres", {})
        dsn = postgres_cfg.get("readonly_replica", "")
        if not dsn:
            logger.warning("connections.postgres.readonly_replica 未配置，跳过 Code Agent 依赖初始化")
            return

        repo_base = os.environ.get(
            "SPMA_REPO_BASE",
            postgres_cfg.get("repo_base", "/repos"),
        )

        # 2. 创建 db_pool
        try:
            import asyncpg
            db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        except Exception as e:
            logger.warning("db_pool 创建失败，跳过 Code Agent 依赖初始化: %s", e)
            return

        # 3. 调用 init_code_agent_deps（内部已做全量或全无 + 失败清理）
        try:
            from spma.bootstrap import init_code_agent_deps
            await init_code_agent_deps(db_pool, repo_base=repo_base)
        except Exception as e:
            logger.warning("Code Agent 依赖初始化失败: %s", e)

    return app


def main():
    """uvicorn 入口: uv run spma-api"""
    import uvicorn
    uvicorn.run("spma.api.app:create_app", host="0.0.0.0", port=8000, factory=True)

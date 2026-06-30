"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览 + Phase 4 hardening design spec §6.3
"""

import logging
import os

import yaml
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from spma.infrastructure.degradation import DegradationManager
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker, has_circuit_breaker
from spma.api.dependencies import get_degradation_manager
from spma.api.routes.llm_admin import router as llm_admin_router

logger = logging.getLogger(__name__)


# --- RepoRegistry 全局单例（启动期注入）---

_repo_registry_singleton = None


def get_repo_registry():
    """全局 RepoRegistry 单例访问器。"""
    global _repo_registry_singleton
    if _repo_registry_singleton is None:
        raise RuntimeError("RepoRegistry 未初始化；请先调用 set_repo_registry()")
    return _repo_registry_singleton


def set_repo_registry(reg):
    """测试 / 重新初始化时设置 RepoRegistry 单例。"""
    global _repo_registry_singleton
    _repo_registry_singleton = reg


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

    # 配置 CORS — 开发环境允许 localhost，生产环境由 K8s ingress 处理
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
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

    # 注册摄入路由
    from spma.api.routes.ingestion import router as ingestion_router
    from spma.api.routes.ingestion_webhooks import router as webhook_router

    app.include_router(ingestion_router, prefix="/api/v1")
    app.include_router(webhook_router, prefix="/api/v1")

    # 注册会话路由
    from spma.api.routes.session import router as session_router
    app.include_router(session_router, prefix="/api/v1")


    # 新增 startup 事件 — 初始化摄入管道
    @app.on_event("startup")
    async def startup_ingestion():
        """初始化摄入管道——ES/PGVector/Embedder → IngestionController。"""
        try:
            yaml_path = _resolve_config_path()
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("无法读取配置，跳过摄入管道初始化: %s", e)
            return

        ingestion_cfg = raw.get("ingestion", {})
        pg_cfg = raw.get("spma", {}).get("connections", {}).get("postgres", {})

        from spma.api.dependencies import (
            get_db_pool as _get_db,
            set_ingestion_controller,
            set_es_client,
            set_vector_store,
            set_embedder,
        )

        try:
            db_pool = _get_db()
        except RuntimeError:
            dsn = pg_cfg.get("readonly_replica") or pg_cfg.get("vector_db", "")
            if dsn:
                import asyncpg
                db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
            else:
                logger.warning("未配置 PostgreSQL 连接，跳过摄入管道初始化")
                return

        # 1. ES Client
        from spma.retrieval.es_client import ESClient
        es_hosts = raw.get("spma", {}).get("connections", {}).get("elasticsearch", {}).get("hosts")
        es = ESClient(hosts=es_hosts if es_hosts else None)
        set_es_client(es)

        # 2. PGVector
        from spma.retrieval.vector_store import PGVectorStore
        vector_store = PGVectorStore(dsn=pg_cfg.get("vector_db", ""))
        set_vector_store(vector_store)

        # 3. Embedder
        from spma.retrieval.embedder import BGEM3Embedder
        embedder = await BGEM3Embedder.create()
        set_embedder(embedder)

        # 4. Run Store (moved up — needed by source handlers)
        from spma.ingestion.run_store import PipelineRunStore
        run_store = PipelineRunStore(db_pool)

        # 5. Doc Pipeline (with source handlers)
        from spma.ingestion.doc_pipeline import DocIngestionPipeline
        from spma.ingestion.source_handlers import MarkdownDirSourceHandler, OneswikiSourceHandler

        source_handlers = {
            "markdown_dir": MarkdownDirSourceHandler(run_store, ingestion_cfg),
            "ones_wiki": OneswikiSourceHandler(run_store, ingestion_cfg),
        }
        doc_pipeline = DocIngestionPipeline(
            es, vector_store, embedder, source_handlers=source_handlers,
        )

        # 6. Code Pipeline
        from spma.ingestion.code.git_manager import GitManager
        from spma.ingestion.code.file_path_cache import FilePathCache
        from spma.ingestion.code.ast_parser import ASTParser
        from spma.ingestion.code_pipeline import CodeIngestionPipeline
        from spma.api.dependencies import get_file_path_cache as _get_fpc, get_ast_parser as _get_ast

        try:
            fpc = _get_fpc()
        except RuntimeError:
            fpc = FilePathCache(db_pool)
        try:
            ast_parser = _get_ast()
        except RuntimeError:
            ast_parser = ASTParser()

        repo_base = os.environ.get(
            "SPMA_REPO_BASE",
            pg_cfg.get("repo_base", "./data/repos"),
        )
        git_manager = GitManager(base_dir=repo_base)
        repo_urls = ingestion_cfg.get("code", {}).get("repo_urls", {})
        code_pipeline = CodeIngestionPipeline(git_manager, fpc, ast_parser, repo_urls)

        # 7. SQL Pipeline
        sql_dsn = pg_cfg.get("readonly_replica", "")
        from spma.ingestion.sql_pipeline import SqlIngestionPipeline
        sql_pipeline = SqlIngestionPipeline(sql_dsn, vector_store, embedder)

        # 8. Synonym Map
        from spma.ingestion.synonym_map import SynonymMap
        synonym_map = SynonymMap(db_pool, ingestion_cfg.get("synonym_map", {}))

        # 9. Freshness Service
        from spma.ingestion.freshness import FreshnessService
        freshness_service = FreshnessService(db_pool, slo_config=ingestion_cfg.get("freshness_slo", {}))

        # 10. Controller
        from spma.ingestion.controller import IngestionController
        controller = IngestionController(
            doc_pipeline=doc_pipeline, code_pipeline=code_pipeline, sql_pipeline=sql_pipeline,
            run_store=run_store, synonym_map=synonym_map, freshness_service=freshness_service,
            config=ingestion_cfg,
        )
        set_ingestion_controller(controller)
        logger.info("摄入管道初始化完成")

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
            postgres_cfg.get("repo_base", "./data/repos"),
        )

        # 2. 创建 db_pool
        try:
            import asyncpg
            db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)
        except Exception as e:
            logger.warning("db_pool 创建失败，跳过 Code Agent 依赖初始化: %s", e)
            return

        # 2.5 启动期注入 RepoRegistry 单例（design-13 §3.2 / Task 17）
        try:
            from spma.ingestion.code.repo_registry import RepoRegistry
            repo_registry = RepoRegistry(db_pool)
            set_repo_registry(repo_registry)
            logger.info("RepoRegistry 单例已注入")
        except Exception as e:
            logger.warning("RepoRegistry 初始化失败（route_repos 将降级到 file_path_cache）: %s", e)

        # 3. 调用 init_code_agent_deps（内部已做全量或全无 + 失败清理）
        try:
            from spma.bootstrap import init_code_agent_deps
            await init_code_agent_deps(db_pool, repo_base=repo_base)
        except Exception as e:
            logger.warning("Code Agent 依赖初始化失败: %s", e)

    # 启动时初始化 Checkpointer + QueryOrchestrator Graph
    @app.on_event("startup")
    async def startup_checkpointer_and_graph():
        """初始化 AsyncPostgresSaver + 编译 QueryOrchestrator graph 单例。"""
        try:
            yaml_path = _resolve_config_path()
            with open(yaml_path) as f:
                raw = yaml.safe_load(f) or {}
        except Exception as e:
            logger.warning("无法读取配置，跳过 checkpointer 初始化: %s", e)
            return

        postgres_cfg = raw.get("spma", {}).get("connections", {}).get("postgres", {})
        dsn = postgres_cfg.get("readonly_replica", "")
        if not dsn:
            logger.warning("PostgreSQL DSN 未配置，跳过 checkpointer 初始化")
            return

        try:
            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from spma.api.dependencies import set_checkpointer, set_query_graph
            from spma.api.query_graph import build_query_orchestrator_graph

            checkpointer_cm = AsyncPostgresSaver.from_conn_string(dsn)
            checkpointer = await checkpointer_cm.__aenter__()
            await checkpointer.setup()
            set_checkpointer(checkpointer)
            # Keep reference for shutdown cleanup
            setattr(app.state, '_checkpointer_cm', checkpointer_cm)

            graph = build_query_orchestrator_graph().compile(checkpointer=checkpointer)
            set_query_graph(graph)

            logger.info("AsyncPostgresSaver + QueryOrchestrator graph 初始化完成")
        except Exception as e:
            logger.warning("Checkpointer/Graph 初始化失败: %s", e)

    # 启动时初始化 Redis 客户端（用于进度流推送）
    @app.on_event("startup")
    async def startup_redis():
        """初始化 Redis 客户端用于进度事件推送。"""
        from spma.api.dependencies import set_redis_client

        redis_url = os.environ.get("SPMA_REDIS_URL", "redis://localhost:6379")
        try:
            import redis.asyncio as aioredis
            redis_client = aioredis.from_url(redis_url, decode_responses=False)
            await redis_client.ping()
            set_redis_client(redis_client)
            logger.info("Redis client initialized for progress streaming")
        except Exception:
            logger.warning("Redis not available — progress streaming disabled")
            set_redis_client(None)

    @app.on_event("shutdown")
    async def shutdown_checkpointer():
        """关闭 AsyncPostgresSaver 连接池。"""
        cm = getattr(app.state, '_checkpointer_cm', None)
        if cm is not None:
            try:
                await cm.__aexit__(None, None, None)
                logger.info("AsyncPostgresSaver 已关闭")
            except Exception as e:
                logger.warning("关闭 AsyncPostgresSaver 失败: %s", e)

    return app


def main():
    """uvicorn 入口: uv run spma-api"""
    import uvicorn
    uvicorn.run("spma.api.app:create_app", host="0.0.0.0", port=8000, factory=True)

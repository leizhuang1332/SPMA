"""应用启动引导——初始化降级系统、熔断器、Feature Flags、审计日志。

在 FastAPI app startup 事件中调用 init_infrastructure()。
"""

import logging
from spma.infrastructure.degradation.manager import DegradationManager
from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
from spma.infrastructure.degradation.actions.l5_static import L5StaticFallback
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import CacheService, get_cache_service
from spma.infrastructure.audit import AuditLogger, get_audit_logger, AuditEvent
from spma.infrastructure.circuit_breaker import set_default_state_change_callback
from spma.infrastructure.metrics import degradation_metrics
from spma.api.dependencies import (
    set_degradation_manager,
    set_feature_flag_service,
)

logger = logging.getLogger(__name__)


async def init_infrastructure(
    llm_client=None,
    retrieval_router=None,
    redis_client=None,
    db_pool=None,
) -> DegradationManager:
    """初始化基础设施层：降级系统 + Feature Flags + 缓存 + 审计。

    返回 DegradationManager 供 app 生命周期管理。
    """

    # 1. Feature Flag 服务
    import os
    config_path = os.environ.get(
        "SPMA_FEATURE_FLAGS_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "feature_flags.yaml"),
    )
    ff_service = FeatureFlagService.from_yaml(config_path)
    set_feature_flag_service(ff_service)

    # 2. 缓存服务
    cache_service = CacheService(redis_client=redis_client)

    # 3. 审计日志
    audit_logger = AuditLogger(db_pool=db_pool)
    await audit_logger.start()

    # 3a. 熔断器状态变更 → 审计日志
    async def on_circuit_breaker_state_change(name, old_state, new_state):
        await audit_logger.log(AuditEvent(
            event_type=f"circuit_breaker.{new_state.value}",
            details={
                "breaker_name": name,
                "old_state": old_state.value,
                "new_state": new_state.value,
            },
        ))

    set_default_state_change_callback(on_circuit_breaker_state_change)

    # 4. 降级动作
    actions = []
    actions.append(L1LLMDegradation())
    actions.append(L2AgentDegradation(ff_service))
    if retrieval_router:
        actions.append(L3RetrievalDegradation(retrieval_router))
    actions.append(L4CacheDegradation(cache_service, min_cached_qa=50))
    actions.append(L5StaticFallback())

    # 5. 降级管理器
    manager = DegradationManager(actions, auto_recovery_enabled=True)

    # 6. 事件 → 审计日志 + metrics
    async def on_degradation_event(event):
        from spma.infrastructure.audit import AuditEvent
        from spma.infrastructure.degradation.events import RecoveryEvent

        # 统一提取 event_type、level 和 details
        if isinstance(event, RecoveryEvent):
            event_type = "degradation.recovered"
            level = event.to_level
            details = {
                "reason": event.reason,
                "from_level": event.from_level,
                "to_level": event.to_level,
                "checks_passed": event.checks_passed,
            }
        else:
            event_type = getattr(event, 'event_type', 'degradation.triggered')
            level = getattr(event, 'level', None)
            details = {
                "reason": getattr(event, "reason", ""),
                "previous_level": getattr(event, "previous_level", None),
                "triggered_by": getattr(event, "triggered_by", "auto"),
                "operator": getattr(event, "operator", None),
            }

        await audit_logger.log(AuditEvent(
            event_type=event_type,
            level=level,
            details=details,
        ))

        # 更新 metrics
        if level:
            if isinstance(event, RecoveryEvent):
                degradation_metrics.record_recovery(level)
            elif hasattr(event, 'event_type') and event.level == "L0":
                degradation_metrics.record_recovery("L0")
            else:
                degradation_metrics.record_degradation(level)

    manager.on_event = on_degradation_event
    set_degradation_manager(manager)

    await manager.start()
    logger.info("基础设施层初始化完成")
    return manager


async def shutdown_infrastructure(manager: DegradationManager) -> None:
    """优雅关闭基础设施。"""
    await manager.stop()
    audit = get_audit_logger()
    await audit.stop()
    logger.info("基础设施层已关闭")


async def init_code_agent_deps(db_pool, repo_base: str = "/repos") -> None:
    """初始化 Code Agent 基础设施依赖并注入到全局单例。

    从 file_path_cache 表推导 repo_paths 映射（约定: {repo_base}/{repo_name}），
    然后创建 RipgrepExecutor 和 ASTParser，注入到 dependencies.py。

    全量或全无：任一步骤失败时回滚所有已设置的全局单例并关闭 db_pool。
    """
    from spma.api.dependencies import (
        set_db_pool,
        set_file_path_cache,
        set_ripgrep_executor,
        set_ast_parser,
    )
    from spma.ingestion.code.file_path_cache import FilePathCache
    from spma.agents.code.searcher import RipgrepExecutor
    from spma.ingestion.code.ast_parser import ASTParser

    # 1. DB Pool（第一步，失败让调用方处理）
    set_db_pool(db_pool)

    try:
        # 2. FilePathCache
        file_path_cache = FilePathCache(db_pool)
        set_file_path_cache(file_path_cache)

        # 3. 从 file_path_cache 表获取已注册仓库列表
        try:
            repos = await file_path_cache.list_repos()
        except Exception:
            logger.warning("file_path_cache.list_repos() 失败，repo_paths 为空", exc_info=True)
            repos = []

        # 4. 推导 repo_paths + 创建 RipgrepExecutor
        repo_paths = {name: f"{repo_base.rstrip('/')}/{name}" for name in repos}
        ripgrep_executor = RipgrepExecutor(repo_paths)
        set_ripgrep_executor(ripgrep_executor)

        # 5. ASTParser（零外部依赖）
        ast_parser = ASTParser()
        set_ast_parser(ast_parser)

    except Exception:
        # 回滚：任一步骤失败，重置所有已设置的全局单例
        set_db_pool(None)
        set_file_path_cache(None)
        set_ripgrep_executor(None)
        set_ast_parser(None)
        await db_pool.close()
        raise

    logger.info(
        "Code Agent 依赖初始化完成: db_pool=%s, repos=%d, repo_paths=%s",
        db_pool is not None, len(repos), repo_paths,
    )

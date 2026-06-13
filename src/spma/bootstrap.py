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
from spma.infrastructure.audit import AuditLogger, get_audit_logger
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
    ff_service = FeatureFlagService.from_yaml("config/feature_flags.yaml")
    set_feature_flag_service(ff_service)

    # 2. 缓存服务
    cache_service = CacheService(redis_client=redis_client)

    # 3. 审计日志
    audit_logger = AuditLogger(db_pool=db_pool)
    await audit_logger.start()

    # 4. 降级动作
    actions = []
    if llm_client:
        actions.append(L1LLMDegradation(llm_client))
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
        event_type = getattr(event, 'event_type', 'degradation.recovered')
        await audit_logger.log(AuditEvent(
            event_type=event_type,
            level=getattr(event, 'level', None),
            details={
                "reason": getattr(event, "reason", ""),
                "previous_level": getattr(event, "previous_level", None),
                "triggered_by": getattr(event, "triggered_by", "auto"),
                "operator": getattr(event, "operator", None),
            },
        ))
        # 更新 metrics
        level = getattr(event, 'level', None)
        if level:
            if hasattr(event, 'event_type') and 'recover' in str(event.event_type):
                degradation_metrics.record_recovery(level)
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

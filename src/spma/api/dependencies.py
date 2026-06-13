"""FastAPI 依赖注入。

通过 Depends() 注入: 降级管理器、熔断器注册表、Feature Flag 服务、缓存等。
"""

from spma.infrastructure.degradation import DegradationManager
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import get_cache_service

# 全局实例（app 启动时初始化）
_degradation_manager: DegradationManager | None = None
_feature_flag_service: FeatureFlagService | None = None


def get_degradation_manager() -> DegradationManager:
    global _degradation_manager
    if _degradation_manager is None:
        raise RuntimeError("DegradationManager not initialized")
    return _degradation_manager


def get_feature_flag_service() -> FeatureFlagService:
    global _feature_flag_service
    if _feature_flag_service is None:
        raise RuntimeError("FeatureFlagService not initialized")
    return _feature_flag_service


def set_degradation_manager(manager: DegradationManager) -> None:
    global _degradation_manager
    _degradation_manager = manager


def set_feature_flag_service(service: FeatureFlagService) -> None:
    global _feature_flag_service
    _feature_flag_service = service

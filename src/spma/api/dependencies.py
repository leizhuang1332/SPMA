"""FastAPI 依赖注入。

通过 Depends() 注入: 降级管理器、熔断器注册表、Feature Flag 服务、缓存等。
同时管理 Code Agent 基础设施单例。
"""

from spma.infrastructure.degradation import DegradationManager
from spma.infrastructure.circuit_breaker import get_all_stats, get_circuit_breaker
from spma.infrastructure.feature_flags import FeatureFlagService
from spma.infrastructure.cache import get_cache_service

# ---- 全局单例 ----

_degradation_manager: DegradationManager | None = None
_feature_flag_service: FeatureFlagService | None = None

# Code Agent 基础设施单例
_db_pool: "asyncpg.Pool | None" = None
_file_path_cache: "FilePathCache | None" = None
_ripgrep_executor: "RipgrepExecutor | None" = None
_ast_parser: "ASTParser | None" = None


# ---- Degradation Manager ----

def get_degradation_manager() -> DegradationManager:
    global _degradation_manager
    if _degradation_manager is None:
        raise RuntimeError("DegradationManager not initialized")
    return _degradation_manager


def set_degradation_manager(manager: DegradationManager) -> None:
    global _degradation_manager
    _degradation_manager = manager


# ---- Feature Flag Service ----

def get_feature_flag_service() -> FeatureFlagService:
    global _feature_flag_service
    if _feature_flag_service is None:
        raise RuntimeError("FeatureFlagService not initialized")
    return _feature_flag_service


def set_feature_flag_service(service: FeatureFlagService) -> None:
    global _feature_flag_service
    _feature_flag_service = service


# ---- DB Pool ----

def get_db_pool() -> "asyncpg.Pool":
    global _db_pool
    if _db_pool is None:
        raise RuntimeError("db_pool not initialized")
    return _db_pool


def set_db_pool(pool: "asyncpg.Pool") -> None:
    global _db_pool
    _db_pool = pool


# ---- FilePathCache ----

def get_file_path_cache() -> "FilePathCache":
    global _file_path_cache
    if _file_path_cache is None:
        raise RuntimeError("FilePathCache not initialized")
    return _file_path_cache


def set_file_path_cache(cache: "FilePathCache") -> None:
    global _file_path_cache
    _file_path_cache = cache


# ---- RipgrepExecutor ----

def get_ripgrep_executor() -> "RipgrepExecutor":
    global _ripgrep_executor
    if _ripgrep_executor is None:
        raise RuntimeError("RipgrepExecutor not initialized")
    return _ripgrep_executor


def set_ripgrep_executor(executor: "RipgrepExecutor") -> None:
    global _ripgrep_executor
    _ripgrep_executor = executor


# ---- ASTParser ----

def get_ast_parser() -> "ASTParser":
    global _ast_parser
    if _ast_parser is None:
        raise RuntimeError("ASTParser not initialized")
    return _ast_parser


def set_ast_parser(parser: "ASTParser") -> None:
    global _ast_parser
    _ast_parser = parser


# ---- IngestionController ----

_ingestion_controller: "IngestionController | None" = None


def get_ingestion_controller() -> "IngestionController":
    global _ingestion_controller
    if _ingestion_controller is None:
        raise RuntimeError("IngestionController not initialized")
    return _ingestion_controller


def set_ingestion_controller(controller: "IngestionController") -> None:
    global _ingestion_controller
    _ingestion_controller = controller


# ---- SessionStore ----

_session_store: "SessionStore | None" = None


def get_session_store() -> "SessionStore":
    """获取 SessionStore 全局单例（懒初始化，无 db_pool 时内存降级）。"""
    global _session_store
    if _session_store is None:
        global _db_pool
        from spma.api.session_store import SessionStore
        if _db_pool is not None:
            _session_store = SessionStore(_db_pool)
        else:
            _session_store = SessionStore()  # 内存降级，重启后数据丢失
    return _session_store


def set_session_store(store: "SessionStore") -> None:
    """显式设置 SessionStore 单例（用于测试或自定义实现）。"""
    global _session_store
    _session_store = store

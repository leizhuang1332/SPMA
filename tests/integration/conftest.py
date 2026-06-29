"""集成测试 fixtures——testcontainers 提供的真实依赖。"""

import pytest
import asyncpg
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pgvector_container():
    """testcontainers PGVector 实例——session 级复用。"""
    raise NotImplementedError


@pytest.fixture(scope="session")
def redis_container():
    """testcontainers Redis 实例——session 级复用。"""
    raise NotImplementedError


def _build_pool(container: PostgresContainer) -> asyncpg.Pool:
    """从 PostgresContainer 派生 asyncpg 连接池。"""
    url = container.get_connection_url()
    dsn = url.replace("postgresql+psycopg", "postgres")
    return asyncpg.create_pool(dsn=dsn)


@pytest.fixture(scope="session")
def pg_with_pgvector():
    """PG 16 + pgvector 容器(session 级),对外暴露 asyncpg pool 语义
    (`acquire()` 上下文管理器),便于测试以 `async with pg_with_pgvector.acquire() as conn`
    写法使用。
    """
    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    try:
        yield _build_pool(container)
    finally:
        container.stop()


@pytest.fixture
async def pg_pool(pg_with_pgvector):
    """基于 pg_with_pgvector 容器的 asyncpg 连接池。"""
    yield pg_with_pgvector
    # pool 在 session 级 fixture 中管理,无需在此关闭
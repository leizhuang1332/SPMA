"""集成测试 fixtures——testcontainers 提供的真实依赖。"""

from pathlib import Path

import pytest
import asyncpg
from testcontainers.postgres import PostgresContainer

MIGRATIONS_DIR = (
    Path(__file__).resolve().parents[2]
    / "deployments/docker/migrations"
)


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


async def _apply_migrations(pool: asyncpg.Pool) -> None:
    """依次执行 deployments/docker/migrations/ 下编号 SQL 文件。"""
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))
    async with pool.acquire() as conn:
        for f in files:
            await conn.execute(f.read_text())


@pytest.fixture(scope="session")
def pg_with_pgvector():
    """PG 16 + pgvector 容器(session 级),对外暴露 asyncpg pool 语义
    (`acquire()` 上下文管理器),便于测试以 `async with pg_with_pgvector.acquire() as conn`
    写法使用。
    """
    container = PostgresContainer("pgvector/pgvector:pg16")
    container.start()
    try:
        pool = _build_pool(container)
        # 同步入口:在这里跑 migrations(应在首次使用前完成)
        # asyncpg pool 不能在 sync fixture 里 await,因此我们提供
        # 一个独立的 async 钩子 pg_with_pgvector_init 供首次使用时驱动;
        # 为简化,使用 loop 跑 sync:
        import asyncio
        asyncio.run(_apply_migrations(pool))
        yield pool
    finally:
        container.stop()


@pytest.fixture
async def pg_pool(pg_with_pgvector):
    """基于 pg_with_pgvector 容器的 asyncpg 连接池。"""
    yield pg_with_pgvector
    # pool 在 session 级 fixture 中管理,无需在此关闭
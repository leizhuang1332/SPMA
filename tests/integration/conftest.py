"""集成测试 fixtures——testcontainers 提供的真实依赖。"""

import pytest


@pytest.fixture(scope="session")
def pgvector_container():
    """testcontainers PGVector 实例——session 级复用。"""
    raise NotImplementedError


@pytest.fixture(scope="session")
def redis_container():
    """testcontainers Redis 实例——session 级复用。"""
    raise NotImplementedError

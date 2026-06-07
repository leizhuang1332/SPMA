"""全局测试 fixtures。

提供的 fixtures:
- mock_llm: MockLLM 客户端（按预编排序列逐轮返回）
- test_redis: testcontainers Redis 实例
- test_pgvector: testcontainers PGVector 实例
- test_client: FastAPI TestClient
"""

import pytest


@pytest.fixture
def mock_llm():
    """MockLLM fixture——按预编排响应序列逐轮返回。"""
    raise NotImplementedError


@pytest.fixture
def test_redis():
    """testcontainers Redis 实例——集成测试用。"""
    raise NotImplementedError


@pytest.fixture
def test_pgvector():
    """testcontainers PGVector 实例——集成测试用。"""
    raise NotImplementedError

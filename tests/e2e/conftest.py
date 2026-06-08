"""E2E 测试 fixtures——真实 LLM + 完整测试环境。"""

import pytest
from spma.retrieval.es_client import ESClient


@pytest.fixture(scope="module")
def live_llm_client():
    """真实 LLM 客户端——E2E 测试使用。"""
    raise NotImplementedError


@pytest.fixture(scope="module")
def full_stack():
    """完整 SPMA 系统——API + 所有 Agent + 存储。"""
    raise NotImplementedError


@pytest.fixture
async def test_es_client():
    """测试用 ES 客户端——自动创建和清理测试索引。"""
    client = ESClient(hosts=["http://localhost:9200"], index_name="spma_docs_test")
    await client.create_index()
    yield client
    await client.delete_index()
    await client.close()

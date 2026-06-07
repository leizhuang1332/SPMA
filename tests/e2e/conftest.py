"""E2E 测试 fixtures——真实 LLM + 完整测试环境。"""

import pytest


@pytest.fixture(scope="module")
def live_llm_client():
    """真实 LLM 客户端——E2E 测试使用。"""
    raise NotImplementedError


@pytest.fixture(scope="module")
def full_stack():
    """完整 SPMA 系统——API + 所有 Agent + 存储。"""
    raise NotImplementedError

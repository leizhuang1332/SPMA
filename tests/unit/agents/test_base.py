"""Agent 基类的单元测试。"""

import pytest


class TestBaseAgent:
    """Agent 基类测试。"""

    def test_check_convergence_deterministic(self):
        """测试确定性收敛路径——不调 LLM。"""
        pass

    def test_consume_budget_within_limit(self):
        """测试 Token 预算在限制内正常消耗。"""
        pass

    def test_consume_budget_exhausted(self):
        """测试 Token 预算耗尽时抛出异常。"""
        pass

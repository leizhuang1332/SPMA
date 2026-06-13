"""降级动作策略测试。"""
import pytest
from spma.infrastructure.degradation.actions.base import DegradationAction
from spma.infrastructure.degradation.actions.l5_static import L5StaticFallback
from spma.infrastructure.degradation.events import DegradationLevel


class TestDegradationActionBase:
    """测试基类契约。"""

    def test_subclass_must_define_level(self):
        """子类必须定义 level 类属性。"""
        class Incomplete(DegradationAction):
            pass
        with pytest.raises(TypeError):
            Incomplete()  # 抽象类不可实例化

    def test_concrete_action_has_level(self):
        """具体子类可实例化且 level 正确。"""
        action = L5StaticFallback(faq_json={"questions": []})
        assert action.level == "L5"


class TestL5StaticFallback:
    """测试 L5 静态 FAQ 兜底。"""

    def test_level_is_l5(self):
        action = L5StaticFallback(faq_json={"faq": []})
        assert action.level == "L5"

    @pytest.mark.asyncio
    async def test_execute_sets_active(self):
        action = L5StaticFallback(faq_json={"faq": [{"q": "test", "a": "answer"}]})
        assert action.is_active is False
        await action.execute("all services down")
        assert action.is_active is True

    @pytest.mark.asyncio
    async def test_execute_is_idempotent(self):
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("first")
        await action.execute("second")
        assert action.is_active is True

    @pytest.mark.asyncio
    async def test_recover_deactivates(self):
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("down")
        result = await action.recover()
        assert result is True
        assert action.is_active is False

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_active(self):
        """L5 激活时 health_check 返回 False（系统仍不可用）。"""
        action = L5StaticFallback(faq_json={"faq": []})
        await action.execute("all down")
        assert await action.health_check() is False

    def test_recovery_conditions_met_when_inactive(self):
        action = L5StaticFallback(faq_json={"faq": []})
        assert action.recovery_conditions_met() is True

    def test_recovery_check_interval(self):
        action = L5StaticFallback(faq_json={"faq": []})
        assert action.recovery_check_interval_seconds == 60

    def test_get_faq_returns_faq_data(self):
        faq = {"faq": [{"q": "What is SPMA?", "a": "A RAG system."}]}
        action = L5StaticFallback(faq_json=faq)
        assert action.get_faq() == faq

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


class MockLLMClient:
    """Mock LLM 客户端，用于 L1 测试。"""
    def __init__(self, healthy=True):
        self.healthy = healthy
        self.model = "claude-sonnet"
        self.ping_count = 0
        self.consecutive_pings = 0

    async def ping(self) -> bool:
        self.ping_count += 1
        if self.healthy:
            self.consecutive_pings += 1
        else:
            self.consecutive_pings = 0
        return self.healthy

    def set_model(self, model: str) -> None:
        self.model = model


class MockFeatureFlagService:
    """Mock FeatureFlag 服务，用于 L2 测试。"""
    def __init__(self, flags=None):
        self._flags = dict(flags or {})
        self.updates = []

    def is_enabled(self, name, context=None):
        return self._flags.get(name, False)

    async def update_flag(self, name, value, reason, updated_by):
        self._flags[name] = value
        self.updates.append((name, value, reason))


class MockRetrievalRouter:
    """Mock 检索路由，用于 L3 测试。"""
    def __init__(self):
        self.vector_enabled = True
        self.es_client = MockESClient()


class MockESClient:
    def __init__(self, healthy=True):
        self.healthy = healthy

    async def health_check(self):
        return self.healthy


class MockCacheService:
    """Mock 缓存服务，用于 L4 测试。"""
    def __init__(self):
        self.fallback_enabled = False
        self.cached_qa = [{"q": "test", "a": "cached answer"}]

    def enable_fallback(self):
        self.fallback_enabled = True

    def disable_fallback(self):
        self.fallback_enabled = False

    def get_cached_qa_count(self):
        return len(self.cached_qa)


class TestL1LLMDegradation:
    """L1: LLM 切换 Sonnet→Qwen3-8B。"""

    def test_level_is_l1(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        action = L1LLMDegradation(MockLLMClient())
        assert action.level == "L1"

    @pytest.mark.asyncio
    async def test_health_check_returns_false_when_unhealthy(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient(healthy=False)
        action = L1LLMDegradation(client)
        assert await action.health_check() is False

    @pytest.mark.asyncio
    async def test_execute_switches_model(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("LLM timeout > 10%")
        assert client.model == "qwen3-8b-local"
        assert action.is_active is True

    @pytest.mark.asyncio
    async def test_execute_is_idempotent(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("first")
        await action.execute("second")
        assert client.model == "qwen3-8b-local"

    @pytest.mark.asyncio
    async def test_recover_switches_back(self):
        from spma.infrastructure.degradation.actions.l1_llm import L1LLMDegradation
        client = MockLLMClient()
        action = L1LLMDegradation(client)
        await action.execute("timeout")
        result = await action.recover()
        assert result is True
        assert client.model == "claude-sonnet"


class TestL2AgentDegradation:
    """L2: Agent→pipeline 模式。"""

    def test_level_is_l2(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        action = L2AgentDegradation(MockFeatureFlagService())
        assert action.level == "L2"

    @pytest.mark.asyncio
    async def test_execute_rolls_back_all_agents(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        ff = MockFeatureFlagService({
            "doc_agentic": True, "code_agentic": True,
            "sql_agentic": True, "supervisor_agentic": True,
            "synth_agentic": True,
        })
        action = L2AgentDegradation(ff)
        await action.execute("P99 latency spike")
        assert len(ff.updates) == 5
        for name, value, _ in ff.updates:
            assert value is False

    @pytest.mark.asyncio
    async def test_recover_restores_all(self):
        from spma.infrastructure.degradation.actions.l2_agent import L2AgentDegradation
        ff = MockFeatureFlagService({
            "doc_agentic": True, "code_agentic": True,
            "sql_agentic": True, "supervisor_agentic": True,
            "synth_agentic": True,
        })
        action = L2AgentDegradation(ff)
        await action.execute("degraded")
        await action.recover()
        restored = [u for u in ff.updates if u[1] is True]
        assert len(restored) == 5


class TestL3RetrievalDegradation:
    """L3: 向量检索→纯BM25。"""

    def test_level_is_l3(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        action = L3RetrievalDegradation(MockRetrievalRouter())
        assert action.level == "L3"

    @pytest.mark.asyncio
    async def test_execute_disables_vector_search(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        router = MockRetrievalRouter()
        action = L3RetrievalDegradation(router)
        await action.execute("PGVector down")
        assert router.vector_enabled is False

    @pytest.mark.asyncio
    async def test_recover_enables_vector_search(self):
        from spma.infrastructure.degradation.actions.l3_retrieval import L3RetrievalDegradation
        router = MockRetrievalRouter()
        action = L3RetrievalDegradation(router)
        await action.execute("down")
        result = await action.recover()
        assert result is True
        assert router.vector_enabled is True


class TestL4CacheDegradation:
    """L4: Redis 缓存热点问答兜底。"""

    def test_level_is_l4(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        action = L4CacheDegradation(MockCacheService())
        assert action.level == "L4"

    @pytest.mark.asyncio
    async def test_execute_enables_fallback(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache, min_cached_qa=1)
        await action.execute("retrieval failure")
        assert cache.fallback_enabled is True

    @pytest.mark.asyncio
    async def test_requires_minimum_cached_qa(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache, min_cached_qa=50)
        assert action._has_sufficient_cache() is False

    @pytest.mark.asyncio
    async def test_recover_disables_fallback(self):
        from spma.infrastructure.degradation.actions.l4_cache import L4CacheDegradation
        cache = MockCacheService()
        action = L4CacheDegradation(cache, min_cached_qa=1)
        await action.execute("failure")
        result = await action.recover()
        assert result is True
        assert cache.fallback_enabled is False

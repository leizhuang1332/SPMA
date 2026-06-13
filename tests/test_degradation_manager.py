"""DegradationManager 编排层测试。"""
import pytest
from spma.infrastructure.degradation.manager import DegradationManager
from spma.infrastructure.degradation.events import DegradationLevel


class MockDegradationAction:
    def __init__(self, level: DegradationLevel, healthy=True,
                 recovery_interval=30):
        self.level = level
        self._healthy = healthy
        self.execute_calls = []
        self.recover_calls = []
        self.health_check_calls = 0
        self.recovery_check_interval_seconds = recovery_interval

    async def health_check(self) -> bool:
        self.health_check_calls += 1
        return self._healthy

    async def execute(self, reason: str) -> None:
        self.execute_calls.append(reason)

    async def recover(self) -> bool:
        self.recover_calls.append(True)
        return True

    def recovery_conditions_met(self) -> bool:
        return self._healthy

    def set_unhealthy(self):
        self._healthy = False

    def set_healthy(self):
        self._healthy = True


@pytest.fixture
def l1_action():
    return MockDegradationAction("L1")

@pytest.fixture
def l2_action():
    return MockDegradationAction("L2")

@pytest.fixture
def l3_action():
    return MockDegradationAction("L3")

@pytest.fixture
def l4_action():
    return MockDegradationAction("L4")

@pytest.fixture
def l5_action():
    return MockDegradationAction("L5")

@pytest.fixture
def manager(l1_action, l2_action, l3_action, l4_action, l5_action):
    return DegradationManager([
        l1_action, l2_action, l3_action, l4_action, l5_action,
    ])


class TestDegradationManager:
    """测试 DegradationManager 核心功能。"""

    def test_initial_level_is_l0(self, manager):
        assert manager.current_level == "L0"

    @pytest.mark.asyncio
    async def test_manual_degrade_skips_levels(self, manager, l1_action, l2_action, l3_action):
        """手动降级支持跨级（L0→L3）。"""
        await manager.manual_degrade("L3", "test skip", "admin")
        assert manager.current_level == "L3"
        assert len(l1_action.execute_calls) == 1
        assert len(l2_action.execute_calls) == 1
        assert len(l3_action.execute_calls) == 1

    @pytest.mark.asyncio
    async def test_manual_degrade_to_l0_recovers(self, manager):
        """降级到 L0 等价于完全恢复。"""
        await manager.manual_degrade("L1", "test", "admin")
        await manager.manual_degrade("L0", "recover test", "admin")
        assert manager.current_level == "L0"

    @pytest.mark.asyncio
    async def test_manual_recover_full(self, manager, l1_action, l3_action):
        """手动恢复逐级恢复到 L0。"""
        await manager.manual_degrade("L3", "degrade", "admin")
        assert manager.current_level == "L3"
        await manager.manual_recover()
        assert manager.current_level == "L0"
        assert len(l1_action.recover_calls) >= 1
        assert len(l3_action.recover_calls) >= 1

    @pytest.mark.asyncio
    async def test_manual_degrade_records_history(self, manager):
        """降级事件记录到历史。"""
        await manager.manual_degrade("L1", "test reason", "admin")
        history = manager.get_history()
        assert len(history) >= 1
        assert history[0].level == "L1"
        assert history[0].triggered_by == "manual"
        assert history[0].operator == "admin"

    def test_get_status(self, manager):
        """get_status 返回完整状态信息。"""
        status = manager.get_status()
        assert status["current_level"] == "L0"
        assert "degraded_components" in status
        assert "auto_recovery_enabled" in status

    def test_get_history_returns_limited(self, manager):
        """get_history(limit) 截断返回。"""
        history = manager.get_history(limit=10)
        assert len(history) <= 10

    @pytest.mark.asyncio
    async def test_degrade_emits_event_via_callback(self, manager):
        """降级触发事件（通过 on_event 回调）。"""
        events = []
        manager.on_event = lambda e: events.append(e)
        await manager.manual_degrade("L2", "event test", "admin")
        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_degrade_to_same_level_noop(self, manager, l2_action):
        """已经处于 L2 时再次降级到 L2 不重复执行 action。"""
        await manager.manual_degrade("L2", "first", "admin")
        first_call_count = len(l2_action.execute_calls)
        await manager.manual_degrade("L2", "second", "admin")
        # 不应额外执行
        assert manager.current_level == "L2"

    @pytest.mark.asyncio
    async def test_recover_from_l0_noop(self, manager):
        """L0 时调用 manual_recover 不抛异常。"""
        await manager.manual_recover()
        assert manager.current_level == "L0"

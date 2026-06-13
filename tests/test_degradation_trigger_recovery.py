"""降级触发器 + 自动恢复测试。"""
import asyncio
import pytest
from spma.infrastructure.degradation.trigger import DegradationTrigger
from spma.infrastructure.degradation.recovery import DegradationRecovery
from spma.infrastructure.degradation.events import DegradationLevel


class MockDegradationAction:
    """Mock 降级动作，可控制 health_check 返回值。"""
    def __init__(self, level: DegradationLevel, healthy: bool = True,
                 recovery_interval: int = 30):
        self.level = level
        self._healthy = healthy
        self._executed = False
        self._recovered = False
        self.recovery_check_interval_seconds = recovery_interval
        self.health_check_call_count = 0

    def set_unhealthy(self):
        self._healthy = False

    def set_healthy(self):
        self._healthy = True

    async def health_check(self) -> bool:
        self.health_check_call_count += 1
        return self._healthy

    async def execute(self, reason: str) -> None:
        self._executed = True

    async def recover(self) -> bool:
        self._recovered = True
        return True

    def recovery_conditions_met(self) -> bool:
        return self._healthy


class TestDegradationTrigger:
    """测试降级触发器。"""

    @pytest.mark.asyncio
    async def test_trigger_calls_back_on_unhealthy(self):
        action = MockDegradationAction("L1", healthy=False)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        await trigger._check_once()
        assert len(triggered) == 1
        assert triggered[0][0] == "L1"

    @pytest.mark.asyncio
    async def test_trigger_skips_healthy(self):
        action = MockDegradationAction("L1", healthy=True)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        await trigger._check_once()
        assert len(triggered) == 0

    @pytest.mark.asyncio
    async def test_handle_webhook_parses_alert(self):
        action = MockDegradationAction("L1", healthy=False)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        alert = {
            "alerts": [{
                "labels": {"degradation_level": "L1", "severity": "critical"},
                "annotations": {"summary": "LLM timeout rate > 10%"},
            }]
        }
        await trigger.handle_webhook(alert)
        assert len(triggered) >= 1

    @pytest.mark.asyncio
    async def test_start_stop_loop(self):
        action = MockDegradationAction("L1", healthy=True)
        triggered = []
        async def callback(level, reason):
            triggered.append((level, reason))

        trigger = DegradationTrigger([action], callback)
        task = asyncio.create_task(trigger.run_loop())
        await asyncio.sleep(0.1)
        await trigger.stop()
        await task
        assert len(triggered) == 0


class TestDegradationRecovery:
    """测试自动恢复检测。"""

    @pytest.mark.asyncio
    async def test_recovery_calls_back_when_conditions_met(self):
        action = MockDegradationAction("L1", healthy=True)
        recovered = []
        async def callback(from_level, to_level, reason):
            recovered.append((from_level, to_level))

        recovery = DegradationRecovery([action], callback)
        await recovery._check_once(current_level="L1")
        assert len(recovered) == 1
        assert recovered[0][0] == "L1"

    @pytest.mark.asyncio
    async def test_skip_if_l0(self):
        action = MockDegradationAction("L1", healthy=True)
        recovered = []
        async def callback(from_level, to_level, reason):
            recovered.append((from_level, to_level))

        recovery = DegradationRecovery([action], callback)
        await recovery._check_once(current_level="L0")
        assert len(recovered) == 0

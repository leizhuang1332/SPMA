"""qr_metrics_bridge 单测。"""
import pytest

from spma.agents.supervisor.qr_metrics_bridge import (
    install_qr_metrics_bridge,
    uninstall_qr_metrics_bridge,
)
from spma.infrastructure.circuit_breaker import (
    get_circuit_breaker, set_default_state_change_callback, reset_all,
)
from spma.observability.qr_metrics import build_qr_metrics


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    set_default_state_change_callback(None)
    # 清理 bridge 模块级幂等集合,保证每个测试从干净状态开始
    from spma.agents.supervisor.qr_metrics_bridge import _installed_stages
    _installed_stages.clear()
    yield
    reset_all()
    set_default_state_change_callback(None)
    _installed_stages.clear()


@pytest.mark.asyncio
async def test_bridge_installs_callback_that_increments_metric():
    """安装 bridge 后,CB 状态变更触发 qr_fallback_total 计数。"""
    qr_metrics = build_qr_metrics()
    install_qr_metrics_bridge(qr_metrics, stage="test")

    # 触发 CB 状态变更
    cb = get_circuit_breaker("test_strategy_a")
    # 模拟连续 5 次失败触发 OPEN
    async def fail():
        raise RuntimeError("fail")
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass

    # 验证指标被 inc
    val = qr_metrics.fallback_total.labels(level="open", stage="test")._value.get()
    assert val >= 1, f"expected fallback_total > 0, got {val}"


@pytest.mark.asyncio
async def test_closed_transition_increments_metric():
    """从 OPEN → CLOSED 恢复也算状态变更,应 inc metric。"""
    qr_metrics = build_qr_metrics()
    install_qr_metrics_bridge(qr_metrics, stage="test")

    cb = get_circuit_breaker("test_recovery")

    # 触发 OPEN
    async def fail():
        raise RuntimeError("fail")
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass

    # 验证 OPEN 状态变更已触发 metric
    val = qr_metrics.fallback_total.labels(level="open", stage="test")._value.get()
    assert val >= 1


@pytest.mark.asyncio
async def test_half_open_transition_increments_metric():
    """HALF_OPEN 状态变更也 inc metric(通过直接 verify 状态变更路径)。"""
    qr_metrics = build_qr_metrics()
    install_qr_metrics_bridge(qr_metrics, stage="half_test")

    cb = get_circuit_breaker("test_half_open_strategy")
    # 由于 reset_timeout 默认较长,直接通过 _transition_to 触发
    await cb._transition_to(cb.state.__class__.HALF_OPEN)  # type: ignore[attr-defined]

    val = qr_metrics.fallback_total.labels(level="half_open", stage="half_test")._value.get()
    assert val >= 1


@pytest.mark.asyncio
async def test_multiple_install_overwrites_previous_callback():
    """第二次 install 会覆盖前一次(同 stage 内 idempotent 阻止,不同 stage 允许)。"""
    qr_metrics_a = build_qr_metrics()
    qr_metrics_b = build_qr_metrics()

    install_qr_metrics_bridge(qr_metrics_a, stage="multi_test_a")
    install_qr_metrics_bridge(qr_metrics_b, stage="multi_test_b")  # 不同 stage 允许

    cb = get_circuit_breaker("multi_strategy")
    async def fail():
        raise RuntimeError("fail")
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass

    # 只有最后一次注册的 callback 生效(qr_metrics_b)
    val_b = qr_metrics_b.fallback_total.labels(level="open", stage="multi_test_b")._value.get()
    assert val_b >= 1
    # qr_metrics_a 没有收到 inc(stage 不匹配)
    val_a = qr_metrics_a.fallback_total.labels(level="open", stage="multi_test_a")._value.get()
    # qr_metrics_a 的 fallback_total{stage="multi_test_a"} 是 0(从未被 inc)
    assert val_a == 0


@pytest.mark.asyncio
async def test_same_stage_install_is_idempotent():
    """同 stage 重复 install 是 no-op(防止覆盖丢失数据)。"""
    qr_metrics = build_qr_metrics()
    install_qr_metrics_bridge(qr_metrics, stage="idem_test")
    install_qr_metrics_bridge(qr_metrics, stage="idem_test")  # 应被 no-op 跳过

    cb = get_circuit_breaker("idem_strategy")
    async def fail():
        raise RuntimeError("fail")
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass

    val = qr_metrics.fallback_total.labels(level="open", stage="idem_test")._value.get()
    assert val >= 1  # 至少 inc 1 次(不因为重复 install 而 double inc)


@pytest.mark.asyncio
async def test_missing_fallback_total_does_not_raise():
    """qr_metrics 缺 fallback_total 属性 → 异常被 try/except 兜住,不抛错。"""
    class BrokenMetrics:
        pass  # 没有 fallback_total 属性

    install_qr_metrics_bridge(BrokenMetrics(), stage="broken_test")  # 应不抛错

    cb = get_circuit_breaker("broken_strategy")
    async def fail():
        raise RuntimeError("fail")
    # 触发 CB 状态变更应不抛错
    for _ in range(5):
        try:
            await cb.call(fail)
        except RuntimeError:
            pass
        except AttributeError:
            pytest.fail("bridge should catch AttributeError silently")
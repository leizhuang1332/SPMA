"""桥接 CB 状态变更 → Prometheus 指标(主文件 ADR-009)。

设计意图:
- 所有 CB 状态变更(包括 CLOSED → CLOSED no-op 和 OPEN → CLOSED 恢复)
  都计入 fallback_total{level=new_state.value}。
- 语义上,metric 反映"CB 状态机活跃度",不仅限于"降级事件"。
- 如果未来需要分离"降级事件"vs"状态变更",可以新增专用 counter
  (如 qr_fallback_event_total) 而非修改本指标语义。

WARNING: set_default_state_change_callback 是全局函数。本函数应在
应用启动时调用一次(主进程),后续调用会覆盖前一次注册的回调。
如需在测试或子进程重置时清理,显式调用 set_default_state_change_callback(None)。
"""
import logging

from spma.infrastructure.circuit_breaker import (
    set_default_state_change_callback,
)

logger = logging.getLogger(__name__)


# 模块级标志:防止多次 install(进程内)
_installed_stages: set[str] = set()


def install_qr_metrics_bridge(qr_metrics, stage: str = "qr"):
    """把 qr_metrics 接入 CB 全局回调。

    调用一次,所有 CB 状态变更自动触发 qr_fallback_total{level=state} +1。

    Idempotent in-process:同一 stage 重复 install 是 no-op(防止回调覆盖丢失数据)。
    不同 stage 的多次 install 仍会覆盖(后注册者胜出)。
    """
    if stage in _installed_stages:
        logger.warning(
            "qr_metrics_bridge already installed for stage=%s, skipping re-install "
            "(current callback may have different qr_metrics instance)",
            stage,
        )
        return

    _installed_stages.add(stage)

    async def on_state_change(name: str, old_state, new_state):
        try:
            qr_metrics.fallback_total.labels(
                level=new_state.value, stage=stage,
            ).inc()
            logger.info("CB %s: %s → %s", name, old_state.value, new_state.value)
        except Exception as e:
            logger.exception(
                "CB→metrics bridge failed: %s",
                type(e).__name__,
                exc_info=True,
            )

    set_default_state_change_callback(on_state_change)
    logger.info("CB→metrics bridge installed (stage=%s)", stage)


def uninstall_qr_metrics_bridge(stage: str = "qr"):
    """显式清理(测试用):从已安装集合移除并清空全局回调。"""
    _installed_stages.discard(stage)
    if not _installed_stages:
        set_default_state_change_callback(None)
    logger.info("CB→metrics bridge uninstalled (stage=%s)", stage)
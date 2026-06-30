"""人工审核闭环(主文件 ADR-006:完整工单流程 + 超时自动拒绝)。"""
import asyncio
import json
import logging
import time
import uuid

from spma.agents.supervisor.qr_state import bump_weights_version, write_weights_snapshot

logger = logging.getLogger(__name__)

# 模块级常量
DEFAULT_TIMEOUT_SECONDS = 86400  # 24h
DEFAULT_SCAN_INTERVAL_SECONDS = 60  # 1min
REVIEW_DELTA_THRESHOLD = 0.1


class HumanInTheLoopValidator:
    """权重变更工单化。

    流程:should_review → submit_for_review → 工单 → approve/reject
    默认拒绝策略:超时 = 不应用新权重(24h 后自动 reject)。

    Known limitation(P6 Task 4 review):`_pending` 仍是内存 dict,
    进程重启后会丢失。重启后已超时的 ticket 最多等 scan_interval(60s)
    被下次周期性扫描捕获,但尚未超时的 ticket 完全丢失。
    完整持久化方案需要 DB schema + migration,标记为 P7 改进项。
    """

    def __init__(
        self,
        db_pool,
        ticket_client=None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL_SECONDS,
    ):
        self._pool = db_pool
        self._tickets = ticket_client
        self._timeout = timeout_seconds
        self._scan_interval = scan_interval_seconds
        self._pending: dict[str, dict] = {}
        self._scan_task: asyncio.Task | None = None

    def should_review(self, weight_diffs: dict) -> bool:
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        return total_delta > REVIEW_DELTA_THRESHOLD

    async def submit_for_review(self, weight_diffs: dict, evaluation: dict) -> str:
        ticket_id = f"qr-review-{uuid.uuid4().hex[:8]}"
        self._pending[ticket_id] = {
            "created_at": time.time(),
            "diffs": weight_diffs,
            "evaluation": evaluation,
            "status": "pending",
        }
        if self._tickets:
            try:
                await self._tickets.create(
                    title=f"[QR Strategy] 权重变更待审核 ({ticket_id})",
                    body=self._format_report(weight_diffs, evaluation),
                    labels=["query-rewriter", "weight-review"],
                )
            except Exception as e:
                # PII 安全:不打印异常内容,仅记录类型
                logger.warning(
                    "Ticket creation failed: %s",
                    type(e).__name__,
                    exc_info=True,
                )
        # 不再 fire-and-forget per-ticket task,改用周期性扫描
        if self._scan_task is None or self._scan_task.done():
            await self._start_periodic_scan()
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        logger.info(
            "Review %s submitted, total_delta=%.4f",
            ticket_id, total_delta,
        )
        return ticket_id

    async def approve(self, ticket_id: str, approver: str) -> bool:
        review = self._pending.get(ticket_id)
        if not review or review["status"] != "pending":
            return False
        new_weights = {n: d["new"] for n, d in review["diffs"].items()}
        # 用 write_weights_snapshot 写新快照(自动取消旧 active)
        await write_weights_snapshot(
            self._pool,
            payload={"weights": new_weights},
            source="manual",
            approver=approver,
        )
        await bump_weights_version(self._pool)
        review["status"] = "approved"
        review["approver"] = approver
        logger.info("Review %s approved by %s", ticket_id, approver)
        return True

    async def reject(self, ticket_id: str, approver: str, reason: str) -> bool:
        review = self._pending.get(ticket_id)
        if not review or review["status"] != "pending":
            return False
        review["status"] = "rejected"
        review["approver"] = approver
        review["reason"] = reason
        logger.info(
            "Review %s rejected by %s: %s",
            ticket_id, approver, reason,
        )
        return True

    async def _expire_review(self, ticket_id: str):
        """保留单 ticket 立即 expire 能力(测试可用)。"""
        review = self._pending.get(ticket_id)
        if review and review["status"] == "pending":
            await self.reject(ticket_id, "system:timeout", "auto-rejected after timeout")
            logger.warning("Review %s auto-rejected after timeout", ticket_id)

    async def _start_periodic_scan(self):
        """启动周期性过期扫描(每 scan_interval 秒扫描 _pending 中超时 ticket)。"""
        if self._scan_task and not self._scan_task.done():
            return
        self._scan_task = asyncio.create_task(self._periodic_scan_loop())
        logger.info(
            "HumanInTheLoopValidator: periodic scan started (interval=%ds)",
            self._scan_interval,
        )

    async def _periodic_scan_loop(self):
        """周期性扫描后台循环。"""
        while True:
            try:
                await asyncio.sleep(self._scan_interval)
                await self._scan_expired_reviews()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(
                    "periodic scan failed: %s",
                    type(e).__name__,
                    exc_info=True,
                )

    async def _scan_expired_reviews(self):
        """扫描 _pending,自动 reject 超时 ticket。"""
        now = time.time()
        expired = [
            tid for tid, r in self._pending.items()
            if r["status"] == "pending" and (now - r["created_at"]) > self._timeout
        ]
        for tid in expired:
            await self.reject(tid, "system:timeout", "auto-rejected after timeout")
            logger.warning("Review %s auto-rejected after timeout", tid)

    async def stop(self):
        """显式停止扫描(应用关闭时)。"""
        if self._scan_task:
            self._scan_task.cancel()
            try:
                await self._scan_task
            except asyncio.CancelledError:
                pass
            self._scan_task = None

    @staticmethod
    def _format_report(weight_diffs, evaluation) -> str:
        total_delta = sum(abs(d["delta"]) for d in weight_diffs.values())
        lines = [f"=== 策略权重调整审核报告 (total_delta={total_delta:.4f}) ==="]
        lines.append("")
        lines.append("## 权重变化")
        for n, d in weight_diffs.items():
            lines.append(f"  {n}: {d['old']:.3f} → {d['new']:.3f} (Δ={d['delta']:+.3f})")
        if evaluation:
            lines.append("")
            lines.append("## 评测结果")
            for n, e in evaluation.items():
                lines.append(
                    f"  {n}: avg_score={e.get('avg_score', 0):.3f}, "
                    f"count={e.get('count', 0)}"
                )
        return "\n".join(lines)
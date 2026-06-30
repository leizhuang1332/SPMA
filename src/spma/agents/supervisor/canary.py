"""冷启动 5 阶段流程编排(主文件 ADR-010:Shadow → 1% → 10% → 50% → 100%)。"""
import logging

logger = logging.getLogger(__name__)


CANARY_STAGES = [
    ("shadow", 0),
    ("one_percent", 1),
    ("ten_percent", 10),
    ("fifty_percent", 50),
    ("hundred_percent", 100),
]


class CanaryRelease:
    """灰度放量编排。手动驱动阶段切换,自动记录每次切换。"""

    def __init__(self, feature_flag, audit_logger):
        self._flag = feature_flag
        self._audit = audit_logger

    async def advance(self, strategy_name: str, stage: str, *, operator: str):
        stages_dict = dict(CANARY_STAGES)
        if stage not in stages_dict:
            raise ValueError(f"Unknown stage: {stage}")
        rollout_pct = stages_dict[stage]
        await self._flag.set_rollout(strategy_name, rollout_pct=rollout_pct, enabled=True)
        await self._audit.log(
            request_id=f"canary-{strategy_name}-{stage}",
            original_query="<canary-advance>",
            rewritten=None,
            strategies_hit=[f"canary:{stage}"],
            weights_snapshot={"strategy": strategy_name, "rollout_pct": rollout_pct},
            latency_ms=0,
        )
        logger.info(
            "Canary %s → %s (%s%%) by %s",
            strategy_name, stage, rollout_pct, operator,
        )

    async def halt(self, strategy_name: str, *, operator: str, reason: str):
        """KILL SWITCH:5 秒内全网关闭。"""
        await self._flag.set_rollout(strategy_name, rollout_pct=0, enabled=False)
        await self._audit.log(
            request_id=f"canary-halt-{strategy_name}",
            original_query=f"<canary-halt:{reason}>",
            rewritten=None,
            strategies_hit=["canary:halt"],
            weights_snapshot={"strategy": strategy_name, "rollout_pct": 0},
            latency_ms=0,
        )
        logger.warning(
            "Canary %s HALTED by %s: %s",
            strategy_name, operator, reason,
        )

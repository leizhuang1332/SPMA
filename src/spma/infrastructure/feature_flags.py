"""Feature Flag 服务——每个 Agent 独立开关，秒级生效。

存储: 内存 dict + 可选 Redis 同步
回滚触发: 虚假信心率>15% OR P99延迟恶化>30% OR Token成本恶化>50%

设计依据: SPMA-design-06 §5 Agent回滚机制 + API-06 §3 Feature Flags
"""

from dataclasses import dataclass, field
from typing import Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class FeatureFlagUpdate:
    flag_name: str
    value: bool
    reason: str
    updated_by: str


class FeatureFlagService:
    """Feature Flag 服务——秒级生效 + 变更审计。"""

    def __init__(
        self,
        defaults: dict[str, bool] | None = None,
        redis_client=None,
    ):
        self._flags: dict[str, bool] = dict(defaults or {})
        self._redis = redis_client
        self._change_log: list[FeatureFlagUpdate] = []

    def is_enabled(self, flag_name: str, context: dict | None = None) -> bool:
        """O(1) 读取，无 I/O。"""
        return self._flags.get(flag_name, False)

    def get_all_flags(self) -> dict[str, bool]:
        """获取所有 flag 的当前状态（返回副本）。"""
        return dict(self._flags)

    async def update_flag(
        self,
        flag_name: str,
        value: bool,
        reason: str,
        updated_by: str,
    ) -> bool:
        """写内存立即生效 → 异步写 Redis → 记录变更日志。"""
        old = self._flags.get(flag_name)
        self._flags[flag_name] = value
        update = FeatureFlagUpdate(
            flag_name=flag_name,
            value=value,
            reason=reason,
            updated_by=updated_by,
        )
        self._change_log.append(update)
        logger.info(
            f"Feature flag '{flag_name}': {old} → {value} "
            f"({reason}, by {updated_by})"
        )
        if self._redis:
            try:
                await self._redis.set(
                    f"ff:{flag_name}", "true" if value else "false"
                )
            except Exception:
                pass  # Redis 写失败不影响主路径
        return True

    def get_change_history(self, limit: int = 50) -> list[FeatureFlagUpdate]:
        """返回最近的 flag 变更记录。"""
        return self._change_log[-limit:]

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "FeatureFlagService":
        """从 YAML 配置文件加载默认值。

        YAML 格式:
            agents:
              doc_agentic: false
              sql_agentic: true
              ...
        """
        import yaml

        with open(yaml_path) as f:
            config = yaml.safe_load(f)

        defaults: dict[str, bool] = {}
        if config and "agents" in config:
            for key, value in config["agents"].items():
                if isinstance(value, bool):
                    defaults[key] = value

        logger.info(
            f"Loaded {len(defaults)} feature flags from {yaml_path}"
        )
        return cls(defaults=defaults)

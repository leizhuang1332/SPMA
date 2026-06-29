# Design: Query Rewriter Phase 7 — 生产加固(5 个未实现子系统)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G11 / G12 / G13 / G14(及已实现的 P7 4 子系统)
>
> **本文档角色**:8 份子 spec 中的第 7 份(Phase 7),gap-driven 结构。
> **上下游依赖**:**上游** [P2 (编排)](SPMA-design-11-phase2-strategy-orchestration.md) + [P3-P5 (策略)](SPMA-design-11-phase3-multi-strategy-resolution.md);**下游** P8 KILL SWITCH。
> **预估工时**:2 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 + 🔴 P0(G13/G14 合规/安全) |
| 关联缺陷 | G11 / G12 / G13 / G14 |
| 关联文件 | 5 个新文件 |
| 预估工时 | 2 周 |
| 相关 ADR | ADR-008(分级模型) |

---

## 1. 现状核查(实际代码)

### 1.1 P7 子系统实现情况

| 子系统 | 主文件描述 | 实际状态 | 关联缺陷 |
|--------|-----------|---------|---------|
| 查询缓存(L1+L2) | 待实现 | ✅ **已实现**(`query_cache.py` 10500 行,L1 Redis + L2 pgvector + lookup_or_compute) | 无 |
| 审计 | 待实现 | ✅ **已实现**(`qr_audit.py`,5s 异步 flush + PG 持久化) | 无 |
| 状态管理(权重快照) | 待实现 | ✅ **已实现**(`qr_state.py`,`write_weights_snapshot` + `bump_weights_version`) | 无 |
| Prometheus 指标 | 待实现 | ✅ **已实现**(`observability/qr_metrics.py`,8 项指标) | 无 |
| OTel trace | 待实现 | ✅ **已实现**(commit `b9c983aa`,`qr.cache.lookup` span) | 无 |
| **CostController**(分级模型 + 预算) | 待实现 | ❌ **未实现** | **G11** |
| **QPSLimiter** | 待实现 | ❌ **未实现** | **G12** |
| **PIIDetector** | 待实现 | ❌ **未实现** | **G13 🔴** |
| **PromptInjectionGuard** | 待实现 | ❌ **未实现** | **G14 🔴** |
| **AuditLogger**(独立类,不是 QrAuditBuffer) | 待实现 | ❌ **未实现**(注意:已实现的 `QrAuditBuffer` 仅做 buffer/flush,不存 PII/敏感字段) | (与 G11 同类) |

**关键洞察**:P7 实际上 **4 个子系统已实现**(40% 完成),**5 个待实现**。本 spec 只设计**待实现的 5 个**,不重复已实现部分。

### 1.2 已实现的关键 API(本 Phase 复用)

- `qr_audit.QrAuditBuffer.enqueue(payload)` — 异步审计日志入队
- `qr_state.write_weights_snapshot(pool, payload, source, ...)` — 权重快照
- `qr_state.bump_weights_version(pool)` / `bump_synonym_version(pool)` — 版本号自增
- `qr_metrics.build_qr_metrics()` — 8 项指标工厂

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| LLM 分级模型路由(haiku/sonnet/opus) | 单一模型 | **新增 `CostController`** |
| 月度预算 + 软硬阈值 | 无 | **包含在 `CostController`** |
| 用户级 QPS 限流 | 无 | **新增 `QPSLimiter`** |
| PII 检测与脱敏 | 无 | **新增 `PIIDetector`** |
| Prompt 注入防护 | 无 | **新增 `PromptInjectionGuard`** |
| 审计日志(PII hash 化) | QrAuditBuffer 不区分敏感字段 | **新增 `AuditLogger` 包装 QrAuditBuffer** |

---

## 3. 详细设计

### 3.1 `CostController`(G11)

`src/spma/agents/supervisor/cost_controller.py` (新建):

```python
"""LLM 成本控制:分级模型路由 + 月度预算(主文件 ADR-008)。"""
import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ModelTier(str, Enum):
    HAIKU = "haiku"
    SONNET = "sonnet"
    OPUS = "opus"


# 复杂度 → 模型档位(主文件 §3.9)
_COMPLEXITY_TIER = {
    "easy": ModelTier.HAIKU,      # 指代消解(llm_semantic)
    "medium": ModelTier.SONNET,    # 扩展(context_aware)
    "hard": ModelTier.OPUS,        # 分解(llm_based)
}


class BudgetExhaustedError(Exception):
    """月度预算耗尽时抛出,调用方降级到规则路径。"""


class CostController:
    """分级 LLM 调用路由 + 预算控制。"""

    def __init__(self, llm_router, budget_tracker, *,
                 monthly_budget_usd: float = 5000.0,
                 soft_threshold: float = 0.8,
                 hard_threshold: float = 0.95):
        self._llm_router = llm_router
        self._budget = budget_tracker
        self._monthly = monthly_budget_usd
        self._soft = soft_threshold
        self._hard = hard_threshold

    async def call_llm(self, prompt: str, *, complexity: str, **kwargs):
        used = await self._budget.get_month_usage_ratio()
        if used > self._hard:
            raise BudgetExhaustedError(
                f"Monthly LLM budget at {used:.1%} (>hard={self._hard:.0%}); "
                "fallback to rule-based path"
            )
        if used > self._soft:
            logger.warning(f"Monthly LLM budget at {used:.1%}; reduce non-essential calls")

        tier = _COMPLEXITY_TIER.get(complexity, ModelTier.SONNET)
        result = await self._llm_router.call(tier, prompt, **kwargs)
        await self._budget.record_call(tier, len(prompt), len(result or ""))
        return result
```

### 3.2 `QPSLimiter`(G12)

`src/spma/agents/supervisor/qps_limiter.py` (新建):

```python
"""基于 tenant_id + user_id 的 QPS 限流(滑动窗口)。"""
import logging
import time

logger = logging.getLogger(__name__)


class QPSLimiter:
    """Redis 滑动窗口(1 秒)。"""

    def __init__(self, redis_client, default_qps: int = 10, vip_tenants: set[str] | None = None):
        self._redis = redis_client
        self._default = default_qps
        self._vip = vip_tenants or {"vip_internal", "vip_partner"}
        self._vip_qps = 50

    async def check(self, tenant_id: str, user_id: str) -> bool:
        limit = self._vip_qps if tenant_id in self._vip else self._default
        key = f"qps:qr:{tenant_id}:{user_id}"
        now = time.time()
        window_start = now - 1.0
        # 清理窗口外
        await self._redis.zremrangebyscore(key, 0, window_start)
        # 计数
        count = await self._redis.zcard(key)
        if count >= limit:
            return False
        # 记录 + 2s 过期(防止 key 永久存活)
        await self._redis.zadd(key, {f"{now}": now})
        await self._redis.expire(key, 2)
        return True
```

### 3.3 `PIIDetector`(G13 🔴)

`src/spma/agents/supervisor/pii_detector.py` (新建):

```python
"""个人敏感信息检测 + 脱敏(主文件 §3.10)。"""
import re
import logging

logger = logging.getLogger(__name__)


class PIIDetector:
    """PII 检测 + 脱敏。检测到 PII 时建议绕过 LLM 路径(主文件 §3.10 should_bypass_llm)。"""

    PII_PATTERNS = {
        "phone_cn": re.compile(r"\b1[3-9]\d{9}\b"),
        "id_card_cn": re.compile(r"\b\d{17}[\dXx]\b"),
        "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
        "ip_v4": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    }

    def __init__(self, mask_token: str = "[REDACTED]"):
        self._mask = mask_token

    def detect_and_mask(self, text: str) -> tuple[str, list[str]]:
        detected: list[str] = []
        masked = text
        for pii_type, pattern in self.PII_PATTERNS.items():
            if pattern.search(masked):
                detected.append(pii_type)
                masked = pattern.sub(self._mask, masked)
        return masked, detected

    def should_bypass_llm(self, text: str) -> bool:
        _, detected = self.detect_and_mask(text)
        return len(detected) > 0
```

### 3.4 `PromptInjectionGuard`(G14 🔴)

`src/spma/agents/supervisor/prompt_guard.py` (新建):

```python
"""Prompt 注入检测 + 清洗(主文件 §3.10)。"""
import re
import logging

logger = logging.getLogger(__name__)


class PromptInjectionGuard:
    """检测常见注入模式,可疑时 sanitize 而非 reject(避免误拦正常 query)。"""

    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(previous|above|all)\s+instructions?", re.I),
        re.compile(r"you\s+are\s+now\s+", re.I),
        re.compile(r"system\s*:\s*", re.I),
        re.compile(r"<\s*\|.*\|\s*>", re.I),
        re.compile(r"\{\{.*\}\}", re.I),
    ]

    def is_suspicious(self, text: str) -> bool:
        return any(p.search(text) for p in self.INJECTION_PATTERNS)

    def sanitize(self, text: str) -> str:
        sanitized = text
        for p in self.INJECTION_PATTERNS:
            sanitized = p.sub("[FILTERED]", sanitized)
        return sanitized
```

### 3.5 `AuditLogger`(G11 配套,PII hash 化)

`src/spma/agents/supervisor/audit_logger.py` (新建,**包装已有 QrAuditBuffer**):

```python
"""审计日志:在 QrAuditBuffer 之上加 PII hash 化(主文件 §3.10 最小特权)。"""
import hashlib
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


class AuditLogger:
    """所有 query 记录:原始 + 重写 + 命中策略 + 权重快照 + PII 检测。

    实现要点(主文件 §3.10 合规表):
    - query/result 用 SHA256 截断(不存原文,避免敏感信息泄露)
    - 落盘走已有 QrAuditBuffer(5s 异步 flush)
    - 审计日志查询需要 `audit:read` 权限(应用层控制)
    """

    def __init__(self, qr_audit_buffer, pii_detector):
        self._buffer = qr_audit_buffer
        self._pii = pii_detector

    async def log(
        self,
        request_id: str,
        original_query: str,
        rewritten: str | None,
        strategies_hit: list[str],
        weights_snapshot: dict,
        latency_ms: float,
    ):
        _, pii_types = self._pii.detect_and_mask(original_query)
        record = {
            "ts": datetime.utcnow().isoformat(),
            "request_id": request_id,
            "query_hash": hashlib.sha256(original_query.encode()).hexdigest()[:16],
            "query_length": len(original_query),
            "rewritten_hash": hashlib.sha256(rewritten.encode()).hexdigest()[:16] if rewritten else None,
            "strategies_hit": strategies_hit,
            "weights_snapshot": weights_snapshot,
            "latency_ms": latency_ms,
            "pii_types_detected": pii_types,
        }
        await self._buffer.enqueue(record)
```

### 3.6 集成到 `graph.py`

```python
# graph.py 启动时
_cost_controller = CostController(llm_router, budget_tracker)
_qps_limiter = QPSLimiter(redis_client)
_pii_detector = PIIDetector()
_prompt_guard = PromptInjectionGuard()
_audit = AuditLogger(qr_audit_buffer, _pii_detector)

# rewrite_node 入口处
async def rewrite_node(state):
    # 1. QPS 限流
    if not await _qps_limiter.check(tenant_id, user_id):
        raise HTTPException(429, "QPS exceeded")

    # 2. PII 检测:含 PII → 走规则路径,不调用 LLM
    if _pii_detector.should_bypass_llm(state["original_query"]):
        return {"rewritten_queries": {"original": state["original_query"]}, "pii_bypassed": True}

    # 3. Prompt 注入清洗
    sanitized = _prompt_guard.sanitize(state["original_query"])

    # 4. ... 现有 rewrite_queries 调用(sanitized 替代 original_query)
    # 5. 审计
    await _audit.log(request_id, state["original_query"], result, ..., latency_ms)
```

### 3.7 LLM 调用替换

`CostController.call_llm` 替换 P3-P5 中的 `llm.ainvoke(prompt)`:

```python
# P3 llm_semantic
async def llm_semantic(query, history, llm, *, cost_controller=None, **_) -> str | None:
    if not history: return None
    if cost_controller:
        try:
            result = await cost_controller.call_llm(prompt, complexity="easy")
        except BudgetExhaustedError:
            return None
    else:
        result = await llm.ainvoke(prompt)
    ...
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增文件

| 文件 | 改动 |
|------|------|
| `src/spma/agents/supervisor/cost_controller.py` | `CostController` + `BudgetExhaustedError` |
| `src/spma/agents/supervisor/qps_limiter.py` | `QPSLimiter` |
| `src/spma/agents/supervisor/pii_detector.py` | `PIIDetector` |
| `src/spma/agents/supervisor/prompt_guard.py` | `PromptInjectionGuard` |
| `src/spma/agents/supervisor/audit_logger.py` | `AuditLogger` |

### 4.2 不需要做的事

- **不**重新实现 `QueryCache` / `QrAuditBuffer` / `qr_state` / `qr_metrics`(已存在)
- **不**改 OTel trace(已存在,后续按需补 `qr.strategy.*` spans)

### 4.3 下游契约

[P8](SPMA-design-11-phase8-rollout-and-rollback.md) KILL SWITCH 应能在 `CostController` / `QPSLimiter` 出问题时快速禁用。

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_BUDGET_MONTHLY` | 5000 | 月度预算(USD) |
| `QR_BUDGET_SOFT` | 0.8 | 软告警阈值 |
| `QR_BUDGET_HARD` | 0.95 | 硬限流阈值 |
| `QR_QPS_DEFAULT` | 10 | 默认 QPS |
| `QR_QPS_VIP` | 50 | VIP QPS |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 5 个新文件存在 | ❌ | ✅ | `ls` |
| V2 | 13 原单测无回归 | 13/13 | 13/13 | pytest |
| V3 | 新增 ≥ 25 单测(5 类 × 平均 5 case) | 0 | ≥ 25 | pytest |
| V4 | PII 拦截率 | 0% | 100% (标准模式) | 注入测试集(手机/身份证/邮箱) |
| V5 | Prompt 注入拦截率 | 0% | ≥ 95% | 注入测试集 |
| V6 | QPS 限流生效 | 0% | 100% (超限返 429) | 注入测试 |
| V7 | 预算耗尽降级 | 0% | 100% (抛 BudgetExhaustedError) | 注入 95%+ 测试 |
| V8 | 审计日志 hash 化 | 0% | 100% (无原文) | grep 日志确认 |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:CostController 异常 | router 挂 | 整链路失败 | try/except + FallbackManager L3 兜底 |
| **R2**:Redis 限流挂 | 网络故障 | 限流失效 | 降级到"放行" |
| **R3**:PII 漏判 | 新模式 | 敏感数据外泄 | 定期更新 pattern + 灰度 |
| **R4**:PII 误判 | 普通文本被拦 | 正常 query 走规则 | 限规则:仅当 ≥ 2 种 PII 时 bypass |
| **R5**:Prompt 漏判 | 攻击者绕过 | LLM 被劫持 | 输出合法性校验(P3-P5 已加) |
| **R6**:审计 buffer 满 | flush 慢 | 日志丢失 | 已实现 fallback 持久化(`6249bf96`) |

---

## 7. 实施步骤

### 7.1 PR 切分(5 个 PR,与缺陷对齐)

**PR #1**:`CostController` + 单测
**PR #2**:`QPSLimiter` + 单测
**PR #3**:`PIIDetector` + 单测(🔴 P0 优先)
**PR #4**:`PromptInjectionGuard` + 单测(🔴 P0 优先)
**PR #5**:`AuditLogger` + 集成到 `graph.py`

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1-D2 | `CostController` | PR #1 ready |
| D3 | Review PR #1 + 合并 | - |
| D3 | `QPSLimiter` | PR #2 ready |
| D4 | Review PR #2 + 合并 | - |
| D4-D5 | `PIIDetector` | PR #3 ready |
| D6 | Review PR #3 + 合并 | - |
| D6-D7 | `PromptInjectionGuard` | PR #4 ready |
| D8 | Review PR #4 + 合并 | - |
| D8-D9 | `AuditLogger` + 集成 | PR #5 ready |
| D10 | Review PR #5 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-5 合并
- [ ] 13 原单测无回归
- [ ] 25+ 新单测全过
- [ ] PII / Prompt 注入测试集全过
- [ ] QPS 限流演练通过

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:5 个新文件(成本/限流/PII/注入/审计),不复用已实现 |
| 2026-06-29 | 0.9 | (回退)初次拆分(已回退) |

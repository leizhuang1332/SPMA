# Design: Router/Intent 前置网关设计（Entry Gateway）

> 在 `classify_node` 之前增加 1 层最简 L0 规则，把"不需要 RAG 增强"的 query 在入口短路。
> **前置约束**：未获得真实流量分布数据 + stakeholder 产品形态表态前，不进入 L1/L2。

---

## 〇、TL;DR

| 维度 | 内容 |
|---|---|
| 设计目标 | `classify` 之前加 1 层 L0 规则引擎，短路闲聊/越权/越界/空 query |
| 路由层级 | 仅 L0（正则 + 枚举匹配） |
| 决策字段 | 单选 + 1.0 置信度 |
| P0 模式 | **Shadow**：跑规则 + 写 trace，命中也不真短路（采数据） |
| P1 模式 | 真短路 → `router_short_circuit` SSE 事件 → END |
| Feature flag | 走 `FeatureFlagService`（YAML + 动态回滚），**不新增 env flag** |
| SSE 契约 | 新增 `router_short_circuit` 事件（最小 payload：`{layer, rule_id, response_text, query_id}`） |
| Trace 集成 | `routes/query.py` 从 `graph.ainvoke` 的 `final_state.router_decision` 合并到 `log_query` |
| PII 合规 | PII 命中时 `trace_logger` 对 `original_query` 做 hash 化（前 8 字符 + sha256[:8]） |
| JAILBREAK | 收紧 regex（避免误伤 "system prompt 在哪里" 类技术提问） |
| Worker 契约 | `router_decision` 是 entry_router 独享字段，worker 节点禁止写入（PR review checklist） |
| 默认推荐 | 先 ship P0 shadow 跑 7 天真实 trace，根据数据决定 P1 |
| 扩展前置 | shadow 潜在命中率 ≥3% + stakeholder 表态 |
| 时间 | P0 = 1.5d 实现 + 1d 测试/联调；P1 = 1d |
| 回退 | `ff.update_flag("entry_router_enabled", False, ...)`（秒级生效） |
| 测试 | L0 规则 ≥95% 覆盖 + shadow/passthrough 集成测试 + SSE 事件契约测试 |

---

## 一、问题与可达性分析

所有 query 强制走：`classify(LLM) → rewrite(LLM ×3) → dispatch → synthesis → quality`。对闲聊/越权/空 query 是浪费。

**核心问题**：入口流量中"不该走完整链路"的 query 占比是否可观？**未验证假设前不上 L1/L2**。

### 可达性分析（18 条打磨输入）

| # | 问题 | 来源 | 处理 |
|---|---|---|---|
| 1 | Feature flag 用 `_env_flag` 与项目 `FeatureFlagService` 双轨制 | `infrastructure/feature_flags.py` | 走 `FeatureFlagService` |
| 2 | `trace_logger.log_query` 的 INSERT 没包含 `router_decision` | `trace_logger.py:91-107` | 同步改造 |
| 3 | P0 阶段 `router_enabled=false` 默认 bypass，L0 命中率 0% | 设计 §六 | P0 改 shadow 模式 |
| 4 | `QueryOrchestratorState` 是 TypedDict 继承，router_decision schema 不明确 | `state.py` | TypedDict + total=False |
| 5 | SSE `_map_node_to_event` 没映射 entry_router 节点 | `routes/query.py:735` | 新增 `router_short_circuit` 事件 |
| 6 | `JAILBREAK/PII_SELF` 硬拦截可能 UX 死循环 | 设计 §3.2 | 硬拦截合理；PII 加 hash 化 |
| 7 | `worker_outputs` 用 `operator.add` reducer，但 router_decision 单值 | `state.py:24` | 代码注释 + PR review |
| 8 | `query_id` 在 `routes/query.py:54` 才生成，SSE `input_state` 里没有 | `query.py:598` | SSE input_state 提前生成 query_id |
| 9 | 数据采集 SQL 用 `LENGTH() <= 8`、regex 不会走 GIN 索引 | 设计 §二 | 接受；7 天数据量小 |
| 10 | Cost 表数字无依据，时间估算激进 | 设计 §九/§六 | 改为相对值；时间 1.5+1 / 1d |
| 11 | `RouterDecision` 字段没明确不被 reducer 影响 | `state.py` | state.py 注释明确 |
| 12 | 缺 `_router_enabled()` 实现细节 | 设计 §3.4 | 接 FeatureFlagService |
| 13 | 缺 `record_decision` 指标暴露方式 | 设计 §5.1 | 对接 prometheus_client，按 Counter+labels |
| 14 | LangGraph conditional edge 是否支持 END | 设计 §3.5 | LangGraph 0.2+ 支持 `True: END` |
| 15 | query_id 在 entry_router 阶段还没生成 | `query.py:598` | SSE 路径提前生成 |
| 16 | Cost 模型假设缺依据 | 设计 §九 | 改为相对值（节省 X% LLM 调用） |
| 17 | P0/P1 时间估算 0.5 天激进 | 设计 §六 | P0=1.5d+1d / P1=1d |
| 18 | LangGraph checkpoint 持久化 router_decision | `query_graph.py` | router_decision 不参与 reducer |

---

## 二、P0 数据采集（Shadow 模式）

### 2.1 采集目标

跑 7 天真实 trace，回答 3 个问题：
1. **潜在命中率** = L0 shadow 命中数 / 总 query 数
2. **rule_id 分布** = 各规则命中占比
3. **误伤率** = 人工抽查命中率中"应该走 RAG 的"占比

### 2.2 采集 SQL

```sql
-- P0 上线后 7 天内的新 row（老 row router_decision 为 NULL 不计入）
SELECT
    router_decision->>'layer' AS layer,
    router_decision->>'rule_id' AS rule_id,
    router_decision->>'category' AS category,
    COUNT(*) AS cnt,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM agent_traces
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND router_decision IS NOT NULL  -- 仅 P0 上线后新 row
GROUP BY layer, rule_id, category
ORDER BY cnt DESC;
```

**潜在命中率 SQL**：
```sql
SELECT
    ROUND(100.0 * COUNT(*) FILTER (
        WHERE router_decision->>'shadow_hit' = 'true'
    ) / COUNT(*), 2) AS shadow_hit_pct,
    COUNT(*) AS total_queries
FROM agent_traces
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND router_decision IS NOT NULL;
```

### 2.3 判定门槛

| shadow 潜在命中率 | 决策 |
|---|---|
| <3% | 项目到此为止，删除 entry_router 代码（或保留为安全网） |
| 3-10% | 启用 P1 短路 |
| >10% | 评估扩展 L1（LLM 分类） |

---

## 三、设计

### 3.1 节点结构

```
QueryStreamRequest → entry_router (L0 rules)
                         │
                         ├─ P0 shadow / bypass → AIMessage写入trace → classify
                         ├─ P1 命中 → AIMessage → END（router_short_circuit 事件）
                         └─ P1 未命中 → classify
```

### 3.2 L0 规则

```python
# src/spma/agents/router/l0_rules.py
import re
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class L0Rule:
    rule_id: str          # "GREETING" — 机器用
    category: Literal["chitchat", "pii", "jailbreak", "empty", "oversize"]
    pattern: re.Pattern
    response_id: str      # 对应 L0_RESPONSES key
    reason: str
    confidence: float = 1.0


L0_RULES: list[L0Rule] = [
    L0Rule("EMPTY", "empty",
           re.compile(r"^\s*$|^[\s\.\,\!\?]+$"),
           "empty", "empty_query"),

    L0Rule("TOO_LONG", "oversize",
           re.compile(r"^.{2000,}$"),
           "oversize", "exceeds_2000_chars"),

    L0Rule("GREETING", "chitchat",
           re.compile(r"^(你好|hi|hello|hey|嗨|哈喽|早上好|下午好|晚上好|在吗|在么)\s*[!！。.～\?]*\s*$", re.IGNORECASE),
           "greeting", "greeting_keyword"),

    L0Rule("FAREWELL", "chitchat",
           re.compile(r"^(再见|bye|goodbye|拜拜|886|回见|回头见)\s*[!！。.～]*\s*$", re.IGNORECASE),
           "farewell", "farewell_keyword"),

    L0Rule("THANKS", "chitchat",
           re.compile(r"^(谢谢|thanks|thank you|thx|辛苦|多谢|感激)\s*[!！。.～]*\s*$", re.IGNORECASE),
           "thanks", "thanks_keyword"),

    L0Rule("EMOJI_ONLY", "chitchat",
           re.compile(r"^[\U0001F300-\U0001FAFF\s\.\,\!\?]+$"),
           "emoji_only", "emoji_only"),

    # PII_SELF：包含中文叙述 + 身份证号 + 手机号形态（合规拦截）
    L0Rule("PII_SELF", "pii",
           re.compile(r"我的身份证是|社保号是|手机号是|卡号是|"
                      r"\b\d{17}[\dXx]\b|\b1[3-9]\d{9}\b"),
           "pii_self", "pii_self_disclosure"),

    # JAILBREAK：收紧后只匹配明确越狱意图（避免误伤"system prompt 在哪里"类技术提问）
    L0Rule("JAILBREAK", "jailbreak",
           re.compile(r"忽略.{0,20}(指令|提示|约束).{0,40}(之前|上文|以上)|"
                      r"ignore.{0,20}(previous|all).{0,20}(instruction|prompt|rule)",
                      re.IGNORECASE | re.DOTALL),
           "jailbreak", "jailbreak_attempt"),
]


def l0_match(query: str) -> L0Rule | None:
    """遍历 L0 规则，命中返回第一个匹配项。"""
    for rule in L0_RULES:
        if rule.pattern.search(query):
            return rule
    return None
```

### 3.3 响应模板

```python
# src/spma/agents/router/responses.py
L0_RESPONSES: dict[str, str] = {
    "empty":      "看起来是空的，你具体想问什么？",
    "oversize":   "问题有点长（>2000 字符），能精简到关键信息吗？",
    "greeting":   "你好！我是 SPMA，可以帮你查 PRD、代码或业务数据。有什么想问的？",
    "farewell":   "再见，有需要随时回来！",
    "thanks":     "不客气！",
    "emoji_only": "😄 我识别不到具体问题，描述一下你想查什么？",
    "pii_self":   "请不要把身份证/手机号/卡号等敏感信息发到这里，需要查的东西请直接描述。",
    "jailbreak":  "抱歉，我只能帮你查 PRD / 代码 / 业务数据。请换个问题。",
}
```

### 3.4 入口节点

```python
# src/spma/agents/router/entry_router.py
import logging
import time
from typing import Literal

from langchain_core.messages import AIMessage

from spma.agents.router import l0_rules, responses
from spma.agents.router.metrics import record_decision
from spma.infrastructure.feature_flags import FeatureFlagService, get_feature_flag_service

logger = logging.getLogger(__name__)


async def entry_router_node(state: dict) -> dict:
    """前置路由器节点：在 classify 之前用 L0 规则判定。

    P0 shadow 模式：永远 passthrough 到 classify，但 trace 记录 router_decision。
    P1 真短路模式：命中时返回 AIMessage + router_short_circuit=True，should_short_circuit → END。
    Bypass 模式（debug 用）：跳过 L0 规则，直接 passthrough。
    """
    started_at = time.monotonic()
    ff: FeatureFlagService = get_feature_flag_service()
    query = state.get("original_query", "").strip()

    # 1. 决定运行模式
    if not ff.is_enabled("entry_router_enabled"):
        mode: Literal["bypass", "shadow"] = "bypass"
    else:
        mode = "shadow"  # P1 改为 "real_short_circuit"

    # 2. 跑 L0 规则（不论 mode 都跑——P0 需要采集潜在命中率）
    matched_rule = None
    try:
        matched_rule = l0_rules.l0_match(query)
    except Exception as e:
        logger.warning("L0 rule exception: %s", e)

    shadow_hit = matched_rule is not None

    # 3. 决策
    if matched_rule is not None:
        response_text = responses.L0_RESPONSES[matched_rule.response_id]
        short_circuit = (mode != "bypass" and mode != "shadow")  # 仅 P1 真短路
    else:
        response_text = None
        short_circuit = False

    latency_ms = int((time.monotonic() - started_at) * 1000)
    record_decision(
        layer="L0" if matched_rule else "passthrough",
        rule_id=matched_rule.rule_id if matched_rule else "NEEDS_RAG",
        category=matched_rule.category if matched_rule else "rag",
        confidence=matched_rule.confidence if matched_rule else 0.0,
        mode=mode,
        shadow_hit=shadow_hit,
        latency_ms=latency_ms,
    )

    router_decision = {
        "layer": "L0" if matched_rule else "passthrough",
        "rule_id": matched_rule.rule_id if matched_rule else "NEEDS_RAG",
        "category": matched_rule.category if matched_rule else "rag",
        "confidence": matched_rule.confidence if matched_rule else 0.0,
        "reason": matched_rule.reason if matched_rule else "no_rule_matched",
        "latency_ms": latency_ms,
        "mode": mode,
        "shadow_hit": shadow_hit,
    }

    passthrough_reason = (
        "router_disabled" if mode == "bypass"
        else "shadow_mode" if mode == "shadow"
        else "no_rule_matched"
    )

    result: dict = {
        "router_decision": router_decision,
        "router_short_circuit": short_circuit,
        "router_passthrough_reason": passthrough_reason,
    }

    # P1 真短路模式：返回 AIMessage（trigger END）
    if short_circuit and response_text:
        result["messages"] = [AIMessage(content=response_text)]
        result["final_answer"] = response_text

    return result


def should_short_circuit(state: dict) -> Literal["__end__", "classify"]:
    """条件边函数：True → END；False → classify。"""
    return "__end__" if state.get("router_short_circuit", False) else "classify"
```

> 注：P1 切换通过修改 `entry_router_node` 第 `mode = "shadow"` → `mode = "real_short_circuit"`，**不**通过 FeatureFlagService 内嵌 mode 逻辑（保持代码可读）。如需更激进切换，可将 `mode` 也做成 ff flag。

### 3.5 图改动

`src/spma/api/query_graph.py` 具体改动：

```python
# 第 24 行附近新增 import
from spma.agents.router.entry_router import entry_router_node, should_short_circuit
from langgraph.graph import END

# 第 447 行附近：替换 entry_point + 加节点 + 加边
def build_query_orchestrator_graph() -> StateGraph:
    graph = StateGraph(QueryOrchestratorState)

    # ... 现有节点注册不动 ...

    # 注册 entry_router（新增）
    graph.add_node("entry_router", entry_router_node)

    # 改 entry_point
    graph.set_entry_point("entry_router")

    # entry_router → classify（passthrough）
    graph.add_edge("entry_router", "classify")

    # entry_router → END（短路，P1 启用）
    graph.add_conditional_edges(
        "entry_router",
        should_short_circuit,
        {"__end__": END, "classify": "classify"},
    )

    # ... 其它边完全不动 ...
    return graph
```

### 3.6 state 字段

```python
# src/spma/agents/supervisor/state.py
class QueryOrchestratorState(SupervisorState, total=False):
    """..."""
    # ... 现有字段不动 ...

    # ====== Router 字段（entry_router 独享，worker 节点禁止写入）======
    # 重要契约：以下 3 个字段是 entry_router 节点的独占输出，
    # 不参与任何 reducer。doc_worker / code_worker / sql_worker 节点的返回 dict
    # 不得包含 router_ 前缀的字段，否则会覆盖 entry_router 写入的值。
    # PR review checklist 加一条："worker 节点返回 dict 不含 router_ 字段"。
    router_decision: dict              # RouterDecision TypedDict 形态
    router_short_circuit: bool         # True 时 should_short_circuit 走 END
    router_passthrough_reason: str     # "no_rule_matched" | "router_disabled" | "shadow_mode"


class RouterDecision(TypedDict, total=False):  # total=False 保证缺字段不抛
    """entry_router 节点的决策结果。"""
    layer: Literal["L0", "passthrough", "bypass"]
    rule_id: str           # "GREETING" / "EMPTY" / "NEEDS_RAG" / ...
    category: str          # "chitchat" / "pii" / "jailbreak" / "empty" / "oversize" / "rag"
    confidence: float      # L0 = 1.0；passthrough/bypass = 0.0
    reason: str
    latency_ms: int
    mode: Literal["real_short_circuit", "shadow", "bypass"]
    shadow_hit: bool       # P0 shadow 模式下命中但未拦截的标记
```

### 3.7 worker 契约约束

```python
# src/spma/agents/doc/graph.py / code/graph.py / sql/graph.py
# 节点返回 dict 中禁止包含以下字段：
_FORBIDDEN_ROUTER_KEYS = frozenset({
    "router_decision", "router_short_circuit", "router_passthrough_reason",
})

# PR review checklist：
# - [ ] worker 节点返回 dict 不含 router_ 前缀字段
# - [ ] entry_router_node 修改后回归测试 test_query_graph_with_router.py
```

### 3.8 feature flag 配置

```yaml
# config/feature_flags.yaml
agents:
  doc_agentic: true
  sql_agentic: false
  code_agentic: true
  supervisor_agentic: false
  synth_agentic: false
  entry_router_enabled: false  # P0 默认关；P1 翻为 true
```

**P0 → P1 切换步骤**：
```bash
# 后端 lead 在 feature flag admin 端点操作
ff.update_flag("entry_router_enabled", True, reason="7天shadow数据命中率X%", updated_by="backend_lead")
# 同时修改 src/spma/agents/router/entry_router.py 中 mode = "shadow" → "real_short_circuit"
# PR review + merge
```

---

## 四、可观测性

### 4.1 Prometheus 指标

```python
# src/spma/agents/router/metrics.py
from prometheus_client import CollectorRegistry, Counter, Histogram

COUNTER_ROUTER_DECISIONS = "router_decisions_total"
HISTOGRAM_ROUTER_LATENCY = "router_decision_latency_seconds"

# labels: {layer, rule_id, mode}
# buckets: (0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0)


def build_router_metrics() -> RouterMetrics:
    """每次调用返回独立 CollectorRegistry（仿 qr_metrics 模式）。"""
    registry = CollectorRegistry()
    return RouterMetrics(
        registry=registry,
        decisions=Counter(
            COUNTER_ROUTER_DECISIONS,
            "Router decisions by layer/rule_id/mode",
            labelnames=("layer", "rule_id", "mode"),
            registry=registry,
        ),
        latency=Histogram(
            HISTOGRAM_ROUTER_LATENCY,
            "Router decision latency seconds",
            labelnames=("layer", "mode"),
            buckets=(0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=registry,
        ),
    )


_router_metrics: RouterMetrics | None = None

def get_router_metrics() -> RouterMetrics:
    global _router_metrics
    if _router_metrics is None:
        _router_metrics = build_router_metrics()
    return _router_metrics


def record_decision(*, layer: str, rule_id: str, category: str,
                    confidence: float, mode: str, shadow_hit: bool,
                    latency_ms: int) -> None:
    m = get_router_metrics()
    m.decisions.labels(layer=layer, rule_id=rule_id, mode=mode).inc()
    m.latency.labels(layer=layer, mode=mode).observe(latency_ms / 1000.0)
```

### 4.2 数据库迁移（`005_router_decision.sql`）

```sql
-- Migration 005: agent_traces 加 router_decision JSONB 列
ALTER TABLE agent_traces ADD COLUMN IF NOT EXISTS router_decision JSONB;
CREATE INDEX IF NOT EXISTS idx_agent_traces_router_decision
    ON agent_traces USING gin (router_decision);

-- 兼容说明：老 row 的 router_decision 为 NULL，采集 SQL 用
-- "WHERE router_decision IS NOT NULL" 排除。
```

### 4.3 trace_logger 集成

**改动 1**：`log_query` 接受新字段（`src/spma/observability/trace_logger.py:29-46`）：

```python
async def log_query(self, query_id: str, state: dict):
    entry = {
        "table": "agent_traces",
        "query_id": query_id,
        # ... 现有字段不动 ...
        "router_decision": state.get("router_decision", {}),
        "router_short_circuit": state.get("router_short_circuit", False),
        "router_passthrough_reason": state.get("router_passthrough_reason", ""),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
```

**改动 2**：PII 命中时 hash 化原 query（合规）：

```python
# trace_logger.py 文件顶部
import hashlib


def _pii_safe(query: str) -> str:
    """PII 命中时 hash 化原 query：保留前 8 字符 + sha256[:8] 后缀。
    原文不进入数据库，合规可追溯。
    """
    if not query:
        return query
    prefix = query[:8]
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]
    return f"{prefix}***[{digest}]"


# _write_to_db 内（line 83-107）
async def _write_to_db(self, entry: dict):
    if self._db_pool is None:
        # ... 内存模式也走 PII safe ...
        if entry.get("router_decision", {}).get("category") == "pii":
            entry["original_query"] = _pii_safe(entry.get("original_query", ""))
        logger.debug(f"TRACE_LOG: {json.dumps(entry, ensure_ascii=False, default=str)[:500]}")
        return
    try:
        # PII 合规：先判断再写
        if entry.get("router_decision", {}).get("category") == "pii":
            entry["original_query"] = _pii_safe(entry.get("original_query", ""))

        async with self._db_pool.acquire() as conn:
            table = entry.pop("table")
            if table == "agent_traces":
                await conn.execute(
                    """INSERT INTO agent_traces (query_id, session_id, original_query, answer,
                       classification, entities, worker_outputs, quality_scores,
                       reschedule_count, total_llm_calls, total_tokens,
                       convergence_reason, latency_ms, router_decision)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14)
                       ON CONFLICT (query_id) DO UPDATE SET
                       answer=$4, worker_outputs=$7, quality_scores=$8,
                       reschedule_count=$9, latency_ms=$13, router_decision=$14""",
                    entry["query_id"], entry["session_id"], entry["original_query"],
                    entry["answer"],
                    json.dumps(entry["classification"]), json.dumps(entry["entities"]),
                    json.dumps(entry["worker_outputs"]), json.dumps(entry["quality_scores"]),
                    entry["reschedule_count"], entry["total_llm_calls"],
                    entry["total_tokens"], entry["convergence_reason"],
                    entry["latency_ms"], json.dumps(entry.get("router_decision", {})),
                )
            # ... agent_rounds 分支不动 ...
```

### 4.4 数据分析 SQL（详见 §2.2）

### 4.5 SSE 事件契约

`src/spma/api/routes/query.py:735` `_map_node_to_event` 新增映射：

```python
def _map_node_to_event(node_name: str, payload: dict, query_id: str) -> dict | None:
    mapping = {
        "classify": "classification",
        "doc_worker": "worker_result",
        "code_worker": "worker_result",
        "sql_worker": "worker_result",
        "entry_router": "router_short_circuit",  # 新增
    }
    event_type = mapping.get(node_name)
    if event_type is None:
        return None

    event_data = {"node": node_name, "query_id": query_id}

    if event_type == "router_short_circuit":
        # 最小 payload：layer, rule_id, response_text, query_id
        router_decision = payload.get("router_decision", {})
        response_text = payload.get("final_answer", "")
        event_data.update({
            "layer": router_decision.get("layer", "L0"),
            "rule_id": router_decision.get("rule_id", "NEEDS_RAG"),
            "response_text": response_text,
        })
        return {"event": "router_short_circuit", "data": json.dumps(event_data, ensure_ascii=False, default=str)}

    # ... 现有分支不动 ...
```

**前端契约**（待前端联调确认）：
- 事件名：`router_short_circuit`
- payload：`{node, query_id, layer, rule_id, response_text}`
- 前端渲染：`response_text` 作为 AIMessage 显示
- 后续事件不再发送（graph END）

**SSE 输入路径改动**：`routes/query.py:598` input_state 提前生成 query_id：

```python
# routes/query.py:553 query_stream 函数内
query_id = str(uuid.uuid4())  # 已有

# session/lazy create ...

input_state = {
    "messages": [HumanMessage(content=req.query)],
    "original_query": req.query,
    "session_id": req.session_id,
    "query_id": query_id,  # 新增（让 entry_router 能拿到 query_id）
    "sources_hint": req.sources_hint,
}
```

**SSE 输出路径改动**：从 graph 返回的 final_state 收 router_decision：

```python
# routes/query.py:316 log_query 调用前
final_state = await graph.ainvoke(input_state, config)  # 注意：StreamMerger 改造后返回 final_state
combined_state = {
    "session_id": session_id,
    "original_query": req.query,
    # ... 现有字段 ...
    "router_decision": final_state.get("router_decision", {}),
    "router_short_circuit": final_state.get("router_short_circuit", False),
    "router_passthrough_reason": final_state.get("router_passthrough_reason", ""),
}
await trace_logger.log_query(query_id, combined_state)
```

> StreamMerger 改造细节：从 generator 改成 await 返回 (events, final_state)，不在本 spec 范围（单独 PR）。

---

## 五、迁移路径（2 阶段）

### 5.1 P0（Shadow 模式 + 采数据）

| 维度 | 详情 |
|---|---|
| Feature flag | `entry_router_enabled=false`（默认关闭短路，仅采数据） |
| 代码 mode | `mode = "shadow"`（永远 passthrough 到 classify，但跑规则 + 写 trace） |
| 数据库 | `005_router_decision.sql` 上线，agent_traces 加 JSONB 列 + GIN 索引 |
| SSE 契约 | entry_router 节点不触发短路，事件不会发出 |
| Trace | routes/query.py 从 final_state.router_decision 合并到 log_query |
| 时间 | 1.5 天实现 + 1 天集成测试与联调 |
| 验证 | 7 天内 SQL 统计 shadow_hit_pct ≥3% |
| 回退 | `ff.update_flag("entry_router_enabled", False, ...)` 或代码改 `mode = "bypass"` |

### 5.2 P1（启用真短路）

| 维度 | 详情 |
|---|---|
| Feature flag | `entry_router_enabled=true` |
| 代码 mode | `mode = "real_short_circuit"`（PR 改 1 行 + review） |
| 数据库 | 同 P0 |
| SSE 契约 | 新增 `router_short_circuit` 事件，前端联调通过 |
| Trace | 同 P0 |
| 时间 | 1 天（翻 flag + 监控 + 联调） |
| 验证 | P1 后 24h 无误伤投诉；shadow 命中的人工抽查 ≥50 条 |
| 回退 | `ff.update_flag("entry_router_enabled", False, ...)`（秒级） |

---

## 六、文件改动清单（11 个）

| 文件 | 改动 | 性质 | 阶段 |
|---|---|---|---|
| `src/spma/agents/router/__init__.py` | 新建（包初始化） | 新增 | P0 |
| `src/spma/agents/router/l0_rules.py` | L0Rule dataclass + 8 条规则 + `l0_match()` | 新增 | P0 |
| `src/spma/agents/router/responses.py` | `L0_RESPONSES` 字典 | 新增 | P0 |
| `src/spma/agents/router/metrics.py` | Prometheus Counter+Histogram | 新增 | P0 |
| `src/spma/agents/router/entry_router.py` | `entry_router_node` + `should_short_circuit` | 新增 | P0 |
| `src/spma/agents/supervisor/state.py` | +3 字段 + RouterDecision TypedDict + 注释 | 改 | P0 |
| `src/spma/api/query_graph.py` | +1 节点 + 改 entry_point + 加 conditional_edges | 改 | P0 |
| `src/spma/observability/trace_logger.py` | log_query 接受 router_decision + PII hash + INSERT/ON CONFLICT 加列 | 改 | P0 |
| `src/spma/api/routes/query.py` | (a) SSE input_state 加 query_id；(b) `_map_node_to_event` 加 entry_router 映射；(c) log_query 合并 router_decision | 改 | P0 |
| `config/feature_flags.yaml` | +`entry_router_enabled: false` | 改 | P0 |
| `deployments/docker/migrations/005_router_decision.sql` | ALTER TABLE + GIN 索引 | 新增 | P0 |

---

## 七、决策门槛（Blocking）

| # | 门槛 | Owner | 时机 |
|---|---|---|---|
| 1 | P0 shadow 7 天数据，潜在命中率 ≥3% | 后端 lead | P1 启用前 |
| 2 | stakeholder 对 PII_SELF / JAILBREAK 文案批准 | PM + 法务 | P1 启用前 |
| 3 | L0 规则单测覆盖率 ≥95% | 后端 | P1 启用前 |
| 4 | shadow 命中人工抽查 ≥50 条（chitchat/pii/jailbreak 各 ≥10） | 后端 | P1 启用前 |
| 5 | SSE `router_short_circuit` 事件前端联调通过 | 前端 | P1 启用前 |
| 6 | PII hash 算法 PM/法务 sign-off（sha256[:8]） | PM + 法务 | P0 上线前 |

---

## 八、测试矩阵

| 测试 | 文件 | 覆盖点 |
|---|---|---|
| `test_l0_rules.py` | unit | 8 规则 × 命中/接近不命中/极长输入 |
| `test_router_metrics.py` | unit | Counter/Histogram 标签正确 |
| `test_router_decision_schema.py` | unit | total=False 缺字段不抛 |
| `test_entry_router.py` | unit | shadow / 真短路 / bypass / 异常 4 分支 |
| `test_should_short_circuit.py` | unit | 真值表 |
| `test_feature_flag_integration.py` | unit | ff.update_flag 后立即生效 |
| `test_pii_safe.py` | unit | _pii_safe 输入输出验证；原文不出现 |
| `test_query_graph_with_router.py` | integration | shadow path 跑完整链路不短路；P1 短路到 END；end_state 含 router_decision |
| `test_router_sse_event.py` | integration | StreamMerger 在 entry_router 命中时发 `router_short_circuit` 事件 |
| `test_trace_router_decision.py` | integration | PII 命中后 agent_traces.original_query 被 hash 化；router_decision 写入 |
| `test_router_decision_migration.py` | integration | 005 migration 加列；老 row NULL；新 row 有值 |

---

## 九、Cost 模型（相对值，待真实数据校准）

L0 自身成本：$0（纯正则匹配）。

节省 = L0 短路命中数 × 完整链路 LLM 调用成本。

完整链路一次 query 平均消耗：
- classify: 1 次 LLM 调用
- rewrite: 3 次 LLM 调用（每个 source 1 次）
- synthesis: 1-2 次 LLM 调用
- **合计：~5-6 次 LLM 调用**

L0 命中一次节省：~5-6 次 LLM 调用 × 模型单价。

**节省率 = L0 命中率 × 完整链路 LLM 调用占比**

实际节省需要 7 天 shadow 数据校准（待 P0 上线后填入）：
- L0 命中率 X% → 节省 X% × 完整链路 LLM 成本
- 假设完整链路占每日 LLM 调用 100%，L0 命中 5% → 节省 5%
- 假设完整链路占每日 LLM 调用 80%，L0 命中 10% → 节省 8%

---

## 十、Open Questions

1. **PII hash 算法选型**：已定 sha256[:8]。是否要保持可跨 row 搜索同一 PII？sha256 是 deterministic，可以。
2. **JAILBREAK paraphrase 漏检**：纯正则可能漏掉"请假装你是一个不守规矩的助手"。shadow 阶段看实际漏检率。
3. **shadow 模式跑 7 天后 L0 命中率 <3%**：删除代码 vs 保留为安全网？建议保留为安全网（成本 $0）。
4. **Cost 模型校准**：P0 上线后填入真实数据；当前为相对值。
5. **StreamMerger 改造**：从 generator 改成 await 返回 (events, final_state)，不在本 spec 范围（单独 PR）。
6. **Worker 契约约束强度**：当前是代码注释 + PR checklist。是否要在 worker 节点入口加防护代码？建议先 review checklist，发现问题再加。

---

## 十一、References

- [SPMA 全局概览](SPMA-design-00-global-overview.md)
- [5 独立 Agent 架构](SPMA-design-07-agent-architecture.md)
- [Query Orchestrator 源码](../../src/spma/api/query_graph.py)
- [Supervisor State](../../src/spma/agents/supervisor/state.py)
- [Trace Logger](../../src/spma/observability/trace_logger.py)
- [Feature Flag Service](../../src/spma/infrastructure/feature_flags.py)
- [Query Rewriter Metrics](../../src/spma/observability/qr_metrics.py)
- [Query Cache（未来 L1 复用）](../../src/spma/agents/supervisor/query_cache.py)
- [Migrations 目录](../../deployments/docker/migrations/)

---

## 附录 A：变更摘要（打磨对比）

| 维度 | 原设计 | 打磨后 |
|---|---|---|
| Feature flag | `_env_flag("ENTRY_ROUTER_BYPASS")` + `_router_enabled()` | `FeatureFlagService` 单入口 |
| P0 模式 | `router_enabled=false` → bypass（命中率 0%） | shadow 模式（命中率真实可见） |
| trace 集成 | 没改 trace_logger | 同步改造 log_query + INSERT/ON CONFLICT |
| SSE 契约 | 没映射 entry_router | 新增 `router_short_circuit` 事件（最小 payload） |
| JAILBREAK | regex 太宽，误伤技术提问 | 收紧：只匹配明确越狱意图 |
| PII 命中 | 原文入数据库（合规风险） | trace hash 化（前 8 字符 + sha256[:8]） |
| Worker 契约 | 未明确 | 代码注释 + PR review checklist |
| 数据采集边界 | 未指定 | P0 上线后 7 天（仅新 row） |
| 时间估算 | P0/P1 各 0.5 天 | P0=1.5d+1d / P1=1d |
| 文件改动 | 4 个新文件 | 5 个新文件 + 6 个修改 + 1 个 migration |
| 测试矩阵 | 4 个测试 | 11 个测试（unit + integration） |
| spec 路径 | 默认 superpowers/specs | 覆盖 docs/designs/SPMA-design-12（in-place 打磨） |
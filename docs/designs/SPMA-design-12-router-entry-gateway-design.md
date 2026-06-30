# Design: Router/Intent 前置网关设计（Entry Gateway）— KISS 重写版

> 在 `classify_node` 之前增加 1 层最简 L0 规则，把"不需要 RAG 增强"的 query 在入口短路。
>
> **本版本**：2026-07-01 KISS 重写版，详见 [2026-07-01-router-gateway-design-review.md](../superpowers/specs/2026-07-01-router-gateway-design-review.md)。
>
> **前置约束**：未获得真实流量分布数据 + stakeholder 产品形态表态前，不进入 L1/L2。

---

## 〇、TL;DR

| 维度 | 内容 |
|---|---|
| 设计目标 | `classify` 之前加 1 层 L0 规则引擎，短路闲聊/越权/越界/空 query |
| 路由层级 | 仅 L0（正则匹配） |
| 代码位置 | 单文件 `src/spma/agents/supervisor/entry_router.py` + `src/spma/observability/entry_router_metrics.py` |
| 数据结构 | `RouterDecision` = dict（4 字段：rule_id/category/mode/latency_ms） |
| state 字段 | 2 个：`router_decision` + `router_short_circuit`（删 `router_passthrough_reason`，从 `router_decision.mode` 推） |
| P0 模式 | **Shadow hardcode**（不依赖 FF，hardcode `mode="shadow"`，跑规则 + 写 trace） |
| P1 模式 | FF 控制真短路 `mode="real_short_circuit"` |
| Feature flag | 仅 P1 引入 `entry_router_enabled`：P0 hardcode、P1 ff 控制 |
| SSE 契约 | 新增 `router_short_circuit` 事件（最小 payload：`{layer, rule_id, response_text, query_id}`） |
| Trace 集成 | `routes/query.py` 从 `graph.ainvoke` 的 `final_state.router_decision` 合并到 `log_query` |
| PII 合规 | PII 命中时 `trace_logger` 替换为 `***[pii-redacted:<sha256[:16]>]`（无 prefix） |
| Worker 契约 | `agents/base.py` 加 `guard_no_router_keys()`（runtime assert）替代 PR review checklist |
| 默认推荐 | 先 ship P0 shadow 跑 7 天真实 trace |
| 时间 | StreamMerger 改造 1d → entry_router P0 2.5d → P1 1d = **总计 4.5d** |
| 回退 | P1：`ff.update_flag("entry_router_enabled", False, ...)` |
| 测试 | 8 个（4 unit + 4 integration） |
| 文件改动 | **5 个**（vs 原 11 个） |

---

## 一、问题与可达性分析（修正版）

所有 query 强制走：`classify(LLM) → rewrite(LLM ×3) → dispatch → synthesis → quality`。对闲聊/越权/空 query 是浪费。

**核心问题**：入口流量中"不该走完整链路"的 query 占比是否可观？**未验证假设前不上 L1/L2**。

### 可达性分析（精简为 7 条打磨输入）

| # | 问题 | 来源 | 处理 |
|---|---|---|---|
| 1 | Feature flag 走 `_env_flag` 与项目 `FeatureFlagService` 双轨制 | `infrastructure/feature_flags.py` | P0 hardcode 不引 FF，P1 用 `FeatureFlagService` |
| 2 | `trace_logger.log_query` 的 INSERT 没包含 `router_decision` | `trace_logger.py:91-107` | 同步改造 |
| 3 | P0 阶段 `entry_router_enabled=false` 默认 bypass，潜在命中率 0% | 原 §六 | P0 hardcode shadow，FF 仅 P1 引入 |
| 4 | `QueryOrchestratorState` 是 TypedDict 继承，router_decision schema 不明确 | `state.py` | dict + 4 字段，runtime 校验靠 `guard_no_router_keys` |
| 5 | SSE `_map_node_to_event` 没映射 entry_router 节点 | `routes/query.py:735` | 新增 `router_short_circuit` 事件 |
| 6 | `query_id` 已在 `routes/query.py:54` 生成 + input_state 含 `query_id` | `query.py:54, 142, 181` | **不需要任何改动**（消除原 §4.5 SSE 输入路径改动） |
| 7 | LangGraph conditional edge 是否支持 `True: END` | LangGraph 0.2+ 文档 | 支持 |

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
    router_decision->>'rule_id' AS rule_id,
    router_decision->>'category' AS category,
    router_decision->>'mode' AS mode,
    COUNT(*) AS cnt,
    ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct
FROM agent_traces
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND router_decision IS NOT NULL
GROUP BY rule_id, category, mode
ORDER BY cnt DESC;
```

**潜在命中率 SQL**（仅看 P0 shadow 不真短路）：

```sql
SELECT
    ROUND(100.0 * COUNT(*) FILTER (
        WHERE router_decision->>'mode' = 'shadow'
          AND router_decision->>'rule_id' != 'NEEDS_RAG'
    ) / COUNT(*), 2) AS shadow_hit_pct,
    COUNT(*) AS total_queries
FROM agent_traces
WHERE created_at >= NOW() - INTERVAL '7 days'
  AND router_decision IS NOT NULL;
```

### 2.3 判定门槛

| shadow 潜在命中率 | 决策 |
|---|---|
| <3% | 项目到此为止，删除 entry_router 代码 |
| 3-10% | 启用 P1 短路 |
| >10% | 评估扩展 L1（LLM 分类） |

---

## 三、设计

### 3.1 节点结构

```
QueryStreamRequest → entry_router (L0 rules)
                         │
                         ├─ shadow（hardcode）→ AIMessage 写入 trace → classify
                         ├─ real_short_circuit（FF）→ AIMessage → END（router_short_circuit SSE 事件）
                         └─ real_short_circuit 但 pii/jailbreak → 仅 trace，不短路 → classify
```

### 3.2 单文件实现（KISS 核心）

```python
# src/spma/agents/supervisor/entry_router.py
"""Entry Router: L0 规则短路节点。

设计依据: SPMA-design-12 (KISS 重写版, 2026-07-01)
"""
import logging
import re
import time
from dataclasses import dataclass

from langchain_core.messages import AIMessage

from spma.agents.router_metrics import record_decision
from spma.infrastructure.feature_flags import get_feature_flag_service

logger = logging.getLogger(__name__)


# ====== L0 规则（list[tuple] 直接表达，无需 dataclass）======
@dataclass(frozen=True)
class _Rule:
    rule_id: str
    category: str   # "chitchat" | "pii" | "jailbreak" | "empty" | "oversize"
    response_id: str
    pattern: re.Pattern


_RULES: list[_Rule] = [
    _Rule("EMPTY", "empty", "empty",
          re.compile(r"^\s*$|^[\s\.\,\!\?]+$")),

    _Rule("TOO_LONG", "oversize", "oversize",
          re.compile(r"^.{2000,}$")),

    _Rule("GREETING", "chitchat", "greeting",
          re.compile(r"^(你好|hi|hello|hey|嗨|哈喽|早上好|下午好|晚上好|在吗|在么)\s*[!！。.～\?]*\s*$",
                     re.IGNORECASE)),

    _Rule("FAREWELL", "chitchat", "farewell",
          re.compile(r"^(再见|bye|goodbye|拜拜|886|回见|回头见)\s*[!！。.～]*\s*$",
                     re.IGNORECASE)),

    _Rule("THANKS", "chitchat", "thanks",
          re.compile(r"^(谢谢|thanks|thank you|thx|辛苦|多谢|感激)\s*[!！。.～]*\s*$",
                     re.IGNORECASE)),

    _Rule("PII_SELF", "pii", "pii_self",
          # 注意：P0 阶段仅 trace，不真短路（防误伤）
          re.compile(r"我的身份证是|社保号是|手机号是|卡号是|"
                     r"\b\d{17}[\dXx]\b|\b1[3-9]\d{9}\b")),

    _Rule("JAILBREAK", "jailbreak", "jailbreak",
          # 收紧：只匹配明确越狱意图
          re.compile(r"忽略.{0,20}(指令|提示|约束).{0,40}(之前|上文|以上)|"
                     r"ignore.{0,20}(previous|all).{0,20}(instruction|prompt|rule)",
                     re.IGNORECASE | re.DOTALL)),
]


_RESPONSES: dict[str, str] = {
    "empty":      "看起来是空的，你具体想问什么？",
    "oversize":   "问题有点长（>2000 字符），能精简到关键信息吗？",
    "greeting":   "你好！我是 SPMA，可以帮你查 PRD、代码或业务数据。有什么想问的？",
    "farewell":   "再见，有需要随时回来！",
    "thanks":     "不客气！",
    "pii_self":   "请不要把身份证/手机号/卡号等敏感信息发到这里，需要查的东西请直接描述。",
    "jailbreak":  "抱歉，我只能帮你查 PRD / 代码 / 业务数据。请换个问题。",
}

# P0 不真短路的硬拦截类（防误伤：人工看 shadow 数据后再放开）
_P0_SOFT_CATEGORIES = frozenset({"pii", "jailbreak"})


def _l0_match(query: str) -> _Rule | None:
    for rule in _RULES:
        if rule.pattern.search(query):
            return rule
    return None


async def entry_router_node(state: dict) -> dict:
    """P0 hardcode shadow；P1 mode 由 FF 控制。"""
    started_at = time.monotonic()
    query = state.get("original_query", "").strip()

    # 决定 mode
    ff = get_feature_flag_service()
    if not ff.is_enabled("entry_router_enabled"):
        mode = "shadow"  # P0 默认（含 P1 关 FF 时也是 shadow）
    else:
        mode = "real_short_circuit"

    # 跑 L0 规则（不论 mode 都跑——P0 需要采集潜在命中率）
    matched = None
    try:
        matched = _l0_match(query)
    except Exception as e:
        logger.warning("L0 rule exception: %s", e)

    latency_ms = int((time.monotonic() - started_at) * 1000)

    router_decision = {
        "layer": "L0" if matched else "passthrough",
        "rule_id": matched.rule_id if matched else "NEEDS_RAG",
        "category": matched.category if matched else "rag",
        "mode": mode,
        "latency_ms": latency_ms,
    }

    record_decision(**router_decision)

    # 决策：何时真短路
    short_circuit = False
    response_text = None

    if matched is not None:
        response_text = _RESPONSES[matched.response_id]
        # P0 阶段：硬拦截类（pii/jailbreak）即使 mode=real_short_circuit 也不短路
        if mode == "real_short_circuit" and matched.category not in _P0_SOFT_CATEGORIES:
            short_circuit = True

    result: dict = {"router_decision": router_decision,
                    "router_short_circuit": short_circuit}

    if short_circuit and response_text:
        result["messages"] = [AIMessage(content=response_text)]
        result["final_answer"] = response_text

    return result


def should_short_circuit(state: dict) -> str:
    """True → END；False → classify。"""
    return "__end__" if state.get("router_short_circuit", False) else "classify"
```

**关键变化 vs 原设计**：
1. **P0 hardcode `shadow`**：原设计 §3.4 中 `entry_router_enabled=false` 进入 `bypass` 跳过规则——修后**永远跑规则**，只是不真短路
2. **pii/jailbreak 不在 P0 真短路**（即使 P1 FF 打开）：`_P0_SOFT_CATEGORIES` 锁死，shadow 跑完后由 PM + 后端共同决策
3. 单文件 ≤100 行，无需 5 个新文件
4. 不写 `router_passthrough_reason` 字段——从 `router_decision.mode` 推

### 3.3 图改动

```python
# src/spma/api/query_graph.py
from spma.agents.supervisor.entry_router import entry_router_node, should_short_circuit
from langgraph.graph import END


def build_query_orchestrator_graph() -> StateGraph:
    graph = StateGraph(QueryOrchestratorState)

    # ... 现有节点注册不动 ...

    # 注册 entry_router
    graph.add_node("entry_router", entry_router_node)

    # 改 entry_point
    graph.set_entry_point("entry_router")

    # entry_router → classify（passthrough / shadow）
    graph.add_edge("entry_router", "classify")

    # entry_router → END（短路，P1 启用）
    graph.add_conditional_edges(
        "entry_router",
        should_short_circuit,
        {"__end__": END, "classify": "classify"},
    )

    return graph
```

### 3.4 state 字段（KISS 收敛）

```python
# src/spma/agents/supervisor/state.py
"""... 现有字段不动 ..."""

class SupervisorState(AgentState, total=False):
    """Supervisor Agent 专属状态字段。

    ====== Router 字段契约 ======
    以下 2 个字段由 entry_router 节点独占写入。
    任何 worker 节点（doc_worker / code_worker / sql_worker）禁止返回
    包含 router_ 前缀字段的 dict，runtime 由 spma.agents.base.guard_no_router_keys
    强制。PR review 不再需要 checklist——assert 已经会 fail-fast。
    """
    router_decision: dict       # 4 字段：layer/rule_id/category/mode/latency_ms
    router_short_circuit: bool  # True 时 should_short_circuit 走 END
```

### 3.5 worker 契约：runtime assert（替代 PR review checklist）

```python
# src/spma/agents/base.py 顶部
_FORBIDDEN_ROUTER_KEYS = frozenset({
    "router_decision", "router_short_circuit", "router_passthrough_reason",
})


def guard_no_router_keys(node_name: str, output: dict) -> dict:
    """Worker 节点返回 dict 前调用——fail-fast 防覆盖 entry_router 写入。

    用法（doc_worker / code_worker / sql_worker 节点首行）：
        output = guard_no_router_keys("doc_worker", output)
    """
    leaked = _FORBIDDEN_ROUTER_KEYS & set(output.keys())
    if leaked:
        raise RuntimeError(
            f"[{node_name}] worker returned router-prefixed keys {leaked}; "
            "router_* is entry_router exclusive"
        )
    return output
```

> 这就是 KISS：4 行 assert 把不变量推到 runtime，**无需 PR review 守门**。

### 3.6 feature flag 配置

```yaml
# config/feature_flags.yaml
agents:
  doc_agentic: true
  sql_agentic: false
  code_agentic: true
  supervisor_agentic: false
  synth_agentic: false
  # entry_router_enabled: P0 不存在（hardcode shadow），P1 加：
  # entry_router_enabled: false  # 默认 false；shadow 跑完后开启真短路
```

**P0 → P1 切换步骤**：

```bash
# 1. PR 修改 entry_router.py：_P0_SOFT_CATEGORIES 改 set() 或删除
# 2. PR 修改 feature_flags.yaml：+ entry_router_enabled: false
# 3. shadow 数据 ≥3% 之后，ff.update_flag("entry_router_enabled", True, ...)
#    （秒级生效，不需要重启）
# 4. 同时 frontend 确认 router_short_circuit SSE 事件联调通过
```

---

## 四、可观测性

### 4.1 Prometheus 指标

```python
# src/spma/observability/entry_router_metrics.py
"""参照 spma/observability/qr_metrics.py 现有模式（避免重复造轮子）。
"""
from prometheus_client import CollectorRegistry, Counter, Histogram


def build_metrics() -> "EntryRouterMetrics":
    registry = CollectorRegistry()
    return EntryRouterMetrics(
        registry=registry,
        decisions=Counter(
            "entry_router_decisions_total",
            "Decisions by rule_id/category/mode",
            labelnames=("rule_id", "category", "mode"),
            registry=registry,
        ),
        latency=Histogram(
            "entry_router_decision_latency_seconds",
            "Decision latency",
            labelnames=("category", "mode"),
            buckets=(0.0001, 0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=registry,
        ),
    )


_metrics: "EntryRouterMetrics | None" = None


def get_metrics() -> "EntryRouterMetrics":
    global _metrics
    if _metrics is None:
        _metrics = build_metrics()
    return _metrics


def record_decision(*, layer: str, rule_id: str, category: str,
                    mode: str, latency_ms: int) -> None:
    m = get_metrics()
    m.decisions.labels(rule_id=rule_id, category=category, mode=mode).inc()
    m.latency.labels(category=category, mode=mode).observe(latency_ms / 1000.0)
```

### 4.2 数据库迁移（`005_router_decision.sql`）

```sql
-- Migration 005: agent_traces 加 router_decision JSONB 列
ALTER TABLE agent_traces ADD COLUMN IF NOT EXISTS router_decision JSONB;
-- 7 天数据量 < 10K，全表扫 < 50ms；不建 GIN（KISS）
```

### 4.3 trace_logger 集成

**改动 1**：`log_query` 接受新字段：

```python
# trace_logger.py 内
async def log_query(self, query_id: str, state: dict):
    entry = {
        "table": "agent_traces",
        "query_id": query_id,
        # ... 现有字段不动 ...
        "router_decision": state.get("router_decision", {}),
        "router_short_circuit": state.get("router_short_circuit", False),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await self._write_to_db(entry)
```

**改动 2**：PII 命中时 hash 化（**无 prefix，安全**：sha256[:16] = 64-bit 熵，避免短 PII 反推）：

```python
# trace_logger.py 文件顶部
import hashlib


def _pii_safe(query: str) -> str:
    """PII 命中时 hash 化：sha256[:16] 无 prefix（前缀可能本身是 PII）。"""
    if not query:
        return ""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"***[pii-redacted:{digest}]"


# _write_to_db 内
async def _write_to_db(self, entry: dict):
    if entry.get("router_decision", {}).get("category") == "pii":
        entry["original_query"] = _pii_safe(entry.get("original_query", ""))

    if self._db_pool is None:
        logger.debug(f"TRACE_LOG: {json.dumps(entry, ensure_ascii=False, default=str)[:500]}")
        return

    async with self._db_pool.acquire() as conn:
        table = entry.pop("table")
        if table == "agent_traces":
            await conn.execute(
                """INSERT INTO agent_traces (query_id, session_id, original_query,
                   answer, classification, entities, worker_outputs,
                   quality_scores, reschedule_count, total_llm_calls,
                   total_tokens, convergence_reason, latency_ms, router_decision)
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
```

### 4.4 SSE 事件契约

`routes/query.py:735` `_map_node_to_event` 新增映射：

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
        rd = payload.get("router_decision", {})
        event_data.update({
            "layer": rd.get("layer", "L0"),
            "rule_id": rd.get("rule_id", "NEEDS_RAG"),
            "response_text": payload.get("final_answer", ""),
        })
        return {"event": "router_short_circuit",
                "data": json.dumps(event_data, ensure_ascii=False, default=str)}

    # ... 现有分支不动 ...
```

**注意**：原设计 §4.5 SSE 输入路径改动说"routes/query.py:598 input_state 提前生成 query_id"——这一段**整段删除**。核实 query_id 在 #54 已经生成并在 input_state 中。不需要任何改动。

**前端契约**：
- 事件名：`router_short_circuit`
- payload：`{node, query_id, layer, rule_id, response_text}`
- 前端渲染：`response_text` 作为 AIMessage 显示
- 后续事件不再发送（graph END）

### 4.5 SSE 输出路径改动（依赖 StreamMerger 改造）

```python
# routes/query.py:log_query 调用前（依赖 StreamMerger 改造 PR #1）
final_state = await graph.ainvoke(input_state, config)
combined_state = {
    "session_id": session_id,
    "original_query": req.query,
    # ... 现有字段 ...
    "router_decision": final_state.get("router_decision", {}),
    "router_short_circuit": final_state.get("router_short_circuit", False),
}
await trace_logger.log_query(query_id, combined_state)
```

> **StreamMerger 改造 PR 必须先合**。当前 routes/query.py 调 `g.ainvoke()` 收不到 final_state。改造细节不在本 spec 范围（单独 PR）。

---

## 五、迁移路径（3 阶段）

### 5.1 PR #1：StreamMerger 改造（前置）

| 维度 | 详情 |
|---|---|
| 目标 | `routes/query.py` 调用 `graph.ainvoke` 能拿到 final_state |
| 时间 | 1 天 |
| 依赖 | 无 |
| 验证 | 现有 query_stream 测试通过 |

### 5.2 PR #2：P0（Shadow 模式 + 采数据）

| 维度 | 详情 |
|---|---|
| 代码 mode | hardcode `mode = "shadow"`（不依赖 FF） |
| 数据库 | `005_router_decision.sql` 上线，agent_traces 加 JSONB 列（无 GIN） |
| SSE 契约 | entry_router P0 不发 `router_short_circuit`（pii/jailbreak 不真短路） |
| Trace | routes/query.py 从 final_state.router_decision 合并到 log_query |
| Worker 契约 | base.py guard_no_router_keys + 3 worker 节点首行加 call |
| 时间 | 2.5 天实现 + 测试联调 |
| 验证 | 7 天内 SQL 统计 shadow_hit_pct ≥3% |
| 回退 | 代码改回 `mode = "bypass"`（删除 entry_router 节点） |

### 5.3 PR #3：P1（启用真短路）

| 维度 | 详情 |
|---|---|
| Feature flag | 新增 `entry_router_enabled: false`（默认 false） |
| 代码 mode | FF 控制 `mode = "real_short_circuit" iff enabled` |
| 数据库 | 同 P0 |
| SSE 契约 | 新增 `router_short_circuit` 事件（前端联调通过） |
| _P0_SOFT_CATEGORIES | 改为空集（视 P0 shadow 评估决定） |
| 时间 | 1 天（翻 flag + 监控 + 联调） |
| 验证 | P1 后 24h 无误伤投诉；shadow 命中的人工抽查 ≥50 条 |
| 回退 | `ff.update_flag("entry_router_enabled", False, ...)`（秒级） |

---

## 六、文件改动清单（5 个）

| 文件 | 改动 | 性质 | 阶段 |
|---|---|---|---|
| `src/spma/agents/supervisor/entry_router.py` | L0 规则 + `_l0_match` + `entry_router_node` + `should_short_circuit` | 新增 | PR#2 |
| `src/spma/agents/supervisor/state.py` | +2 字段 + 注释（取代原 3 字段） | 改 | PR#2 |
| `src/spma/agents/base.py` | + `guard_no_router_keys` | 改 | PR#2 |
| `src/spma/agents/doc/graph.py / code/graph.py / sql/graph.py` | 各节点首行加 `guard_no_router_keys` call | 改 | PR#2 |
| `src/spma/api/query_graph.py` | +1 节点 + 改 entry_point + 加 conditional_edges | 改 | PR#2 |
| `src/spma/observability/entry_router_metrics.py` | Prometheus Counter+Histogram（仿 qr_metrics） | 新增 | PR#2 |
| `src/spma/observability/trace_logger.py` | log_query 接受 router_decision + PII hash + INSERT/ON CONFLICT 加列 | 改 | PR#2 |
| `src/spma/api/routes/query.py` | (a) `_map_node_to_event` 加 entry_router 映射；(b) log_query 合并 router_decision | 改 | PR#2 |
| `src/spma/api/stream_merger.py` | generator → async with final_state | 改 | **PR#1** |
| `config/feature_flags.yaml` | +`entry_router_enabled: false` | 改 | PR#3 |
| `deployments/docker/migrations/005_router_decision.sql` | ALTER TABLE（无 GIN） | 新增 | PR#2 |

**注意**：`routes/query.py` SSE 输入路径**不需要任何改动**——query_id 已在 input_state 中（核实于 query.py:54, 142, 181）。

---

## 七、决策门槛（Blocking）

| # | 门槛 | Owner | 时机 |
|---|---|---|---|
| 1 | P0 shadow 7 天数据，潜在命中率 ≥3% | 后端 lead | P1 启用前 |
| 2 | stakeholder 对 PII_SELF / JAILBREAK 文案批准 | PM + 法务 | P1 启用前 |
| 3 | L0 规则单测覆盖率 ≥95% | 后端 | P1 启用前 |
| 4 | shadow 命中人工抽查 ≥50 条（chitchat/pii/jailbreak 各 ≥10） | 后端 | P1 启用前 |
| 5 | SSE `router_short_circuit` 事件前端联调通过 | 前端 | P1 启用前 |
| 6 | PII hash 算法 PM/法务 sign-off（sha256[:16] 无 prefix） | PM + 法务 | P0 上线前 |
| 7 | StreamMerger 改造 PR#1 已合 | 后端 | PR#2 启动前 |
| 8 | `_pii_safe` 通过 K-anonymity 测试（5+ 不同 query 哈希无碰撞） | 后端 | P0 上线前 |

---

## 八、测试矩阵（8 个）

| 测试 | 文件 | 覆盖点 |
|---|---|---|
| `test_l0_rules.py` | unit | 7 规则 × 命中/极长输入/边界（合并原 8 规则） |
| `test_entry_router.py` | unit | shadow / 真短路 / pii-jailbreak-soft / bypass 4 分支 |
| `test_should_short_circuit.py` | unit | 真值表 |
| `test_guard_no_router_keys.py` | unit | worker 节点 fail-fast（合并 PR review 责任） |
| `test_pii_safe.py` | unit | _pii_safe 输入输出 + K-anonymity 哈希无碰撞 |
| `test_entry_router_metrics.py` | unit | Counter/Histogram 标签正确 |
| `test_query_graph_with_router.py` | integration | shadow path 跑完整链路不短路；P1 短路到 END；end_state 含 router_decision |
| `test_router_sse_event.py` | integration | StreamMerger 在 entry_router 命中时发 `router_short_circuit` 事件 |

**vs 原 11 个测试**：合并 test_pii_safe + test_trace_router_decision + test_router_decision_migration（DB 端到端）→ 整合到 `test_query_graph_with_router.py`。

---

## 九、Cost 模型（一句话）

L0 自身成本 = $0（纯正则匹配）。ROI 完全由 shadow 命中率决定：

- <3% → 删除 entry_router 代码（3.5 天投入未回本）
- ≥3% → 启用 P1，节省 = shadow 命中率 × 完整链路 LLM 成本

> 完整数字待 P0 上线后填入。

---

## 十、Open Questions

1. **PII hash 长度**：sha256[:16]（64-bit）安全？如需更强可改 salt + HMAC。
2. **JAILBREAK paraphrase 漏检**：纯 regex 漏掉"请假装你是一个不守规矩的助手"。shadow 阶段看实际漏检率。
3. **shadow 模式跑 7 天后 L0 命中率 <3%**：删除代码 vs 保留为安全网？建议**直接删除**（成本 $0 但增加代码维护负担）。
4. **StreamMerger 改造 PR 范围**：本 spec 不展开，单独 PR。
5. **_P0_SOFT_CATEGORIES 何时清空**：取决于 shadow 人工抽查结果，由 PM + 后端共同决策。
6. **K-anonymity 验证**：5+ 不同 PII 哈希是否碰撞？sha256[:16] 理论上几乎不碰撞，但需实测。

---

## 十一、References

- [SPMA-design-12 KISS 审核报告](../superpowers/specs/2026-07-01-router-gateway-design-review.md)
- [SPMA 全局概览](SPMA-design-00-global-overview.md)
- [5 独立 Agent 架构](SPMA-design-07-agent-architecture.md)
- [Query Orchestrator 源码](../../src/spma/api/query_graph.py)
- [Stream Merger](../../src/spma/api/stream_merger.py)
- [Routes Query 源码](../../src/spma/api/routes/query.py)
- [Supervisor State](../../src/spma/agents/supervisor/state.py)
- [Worker Base](../../src/spma/agents/base.py)
- [Trace Logger](../../src/spma/observability/trace_logger.py)
- [QR Metrics 模式参照](../../src/spma/observability/qr_metrics.py)
- [Feature Flag Service](../../src/spma/infrastructure/feature_flags.py)

---

## 附录 A：打磨对比（原版 vs KISS 重写版）

| 维度 | 原打磨版（2026-06-30） | KISS 重写版（2026-07-01） |
|---|---|---|
| 代码位置 | `agents/router/` 子包 + 5 文件 | `agents/supervisor/entry_router.py` 1 文件 |
| 规则数据结构 | `L0Rule` dataclass × 8 字段 | `_Rule` dataclass（仅内部） + list tuple 表达 |
| RouterDecision 字段 | TypedDict 7 字段 | dict 4 字段：layer/rule_id/category/mode/latency_ms |
| state 字段 | 3 个（含 router_passthrough_reason） | 2 个（passthrough 从 mode 推） |
| P0 mode 控制 | FF + 代码 mode 双轨 | P0 hardcode shadow（更简单） |
| FF 引入时机 | P0 即引入 | 仅 P1 引入 |
| trace hash 算法 | sha256[:8] + 前 8 字符前缀 | sha256[:16] 无 prefix |
| 数据库索引 | GIN | 无（7 天数据小，全表扫足够） |
| Worker 契约 | 代码注释 + PR review checklist | `guard_no_router_keys` runtime assert |
| PII/JAILBREAK shadow | 也会真短路 | `_P0_SOFT_CATEGORIES` 锁死仅 trace |
| 可达性表 #8 query_id 行号 | `:598`（错） | `:54`（对） |
| §4.5 SSE 输入路径改动 | 修改 input_state | 整段删除（无必要） |
| 时间估算 | P0 1.5+1d / P1 1d | StreamMerger 1d / P0 2.5d / P1 1d = 4.5d |
| 测试数量 | 11 个 | 8 个（合并重叠） |
| 文件改动 | 11 个 | 5 个 |
| 决策门槛 | 6 | 8（含 StreamMerger + K-anon） |
| 复杂度 | 11 文件 × 7 字段 × 3 mode × 11 test | 1 文件 × 4 字段 × 2 mode × 8 test |
| **复杂度降幅** | — | **约 40-50%** |

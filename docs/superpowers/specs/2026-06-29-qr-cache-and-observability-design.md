# Design:Query Rewriter §3.7 缓存 & §3.11 可观测性 补强方案

> 所属设计:[SPMA-design-11-query-rewrite-optimization-v2-final](../../designs/SPMA-design-11-query-rewrite-optimization-v2-final.md) (v3.1 加固版)
>
> 本次范围:**§3.7 查询缓存**(主线)+ **§3.11 可观测性**(同步覆盖)
>
> 不在本次范围:§3.8 冷启动 / §3.9 成本控制 / §3.10 安全与合规(后续轮次)
>
> 脑暴记录:本次脑暴模式为"补强生产加固细节",由 4 段逐步确认:
>
> - §1 架构总览 → ✅
> - §2 数据模型与数据流 → ✅
> - §3 错误处理 + 可观测性 → ✅
> - §4 测试策略 + 迁移计划 → ✅
>
> 选型记录:key = hash(query+history_fingerprint+entities+weights_version+synonym_version)
>
> L2 索引 = pgvector + GUC;失效 = 版本号过期;写入 = L1 同步 + L2 异步;读降级 = 不可用直绕

---

## 一、目标

补强 v3.1 设计稿中骨架已具备但落地仍有缺口的两个生产加固维度(查询缓存、可观测性),产出**与现有 PostgreSQL/Redis 基础设施职责对齐、可在 5 天内落地、有清晰灰度与回滚路径**的细化方案,而不是再新增一个独立的缓存服务或独立的监控系统。

---

## 二、架构总览

```
┌─────────────────────────────────────────────────────────────┐
│               Query Rewriter 缓存层 (改造后)                │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│   request → QueryCache.lookup()                             │
│              │                                              │
│   ┌──────────┴──────────┐                                   │
│   ▼                     ▼                                  │
│   L1 (Redis)            L2 (pgvector)                      │
│   qr:exact:{hash}       qr_cache_entries 表                │
│   O(1) GET              cosine 阈值召回                     │
│   hot path              miss → fallback orchestrator       │
│                                                             │
│   写回:                                                     │
│   L1 SETEX (sync)        L2 INSERT/UPDATE (async fire-and- │
│   无版本号变化           forget,失败仅日志)                  │
│   TTL 1h                  TTL 24h, 定期清理                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│   状态/历史/观测全部落 PG (不依赖 Redis):                    │
│   - qr_weights_history     (§3.5 + §3.8 共用)              │
│   - qr_request_audit       (§3.11 数据源)                   │
│   - qr_cache_entries 中 weight_set_id / synonym_version    │
│     是缓存 key 的"版本号维度"                                │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 职责对照

| 存储 | 职责 | 关键数据 | 写入模式 |
|---|---|---|---|
| Redis | L1 精确缓存 | `qr:exact:{hash}` → JSONB payload | SETEX sync |
| PostgreSQL | L2 语义缓存 + 全部状态 | `qr_cache_entries`、`qr_weights_history`、`qr_request_audit` | INSERT/UPDATE async (batch buffer 5s flush) |
| OTel/Prom | 实时指标 | 在用 spans/gauges | pull/push 模式 |

### 2.2 版本号流程

- **权重版本号自增**:`update_strategy_weights()` 把 EMA 更新写入 `qr_weights_history`(**在 HumanInTheLoopValidator.approve() 之后才落库 + 自增**;否则视为待审核,沿用当前 active 版本号)。即:权重版本号 = 当前已被人工(或自动批准)应用的版本号,而非 EMA 草稿。
- **synonym 版本号自增**:`SynonymMap.upsert()` 持久化新映射后立即 `UPDATE qr_state_meta SET synonym_version = synonym_version + 1`。
- **缓存 key**:`hash(query + history_fingerprint + entities_serialized + weights_version + synonym_version)`,其中 `history_fingerprint = sha256(last_3_turns)[:16]`,`entities_serialized = sorted_json(entities)`。
- 读降级路径:复用 v3.1 `FallbackManager` 的多策略 / 主备 / 规则三级;**本次不改动降级链路**

### 2.3 与 v3.1 差异总览

| 维度 | v3.1 既有 | 本次改造 |
|---|---|---|
| L2 索引 | Annoy (进程内重建) | pgvector (PG 进程共享) |
| 版本号存储 | Redis `qr:strategy_weights` | PG `qr_state_meta` |
| 权重历史 | 无 | `qr_weights_history`(§3.5 / §3.8 共用) |
| audit | 无 | `qr_request_audit` + buffer flush worker |
| metric 数据源 | 内存 deque + Prometheus push | Prometheus scrape + OTel + PG 派生 |
| 回滚 | 单一权重 config 回滚 | 权重 config 回滚 + L2 阈值回滚 + ANN 老路径保活 1 周 |

---

## 三、数据模型与数据流

### 3.1 PostgreSQL 新增 3 张表

```sql
-- ============================================================
-- 1) 权重历史快照(§3.5 + §3.8 共用)
-- ============================================================
CREATE TABLE qr_weights_history (
    weights_set_id  BIGSERIAL PRIMARY KEY,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL CHECK (source IN ('ema','manual','rollback','init')),
    applied_at      TIMESTAMPTZ,
    approver        TEXT,
    payload         JSONB NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT FALSE,
    CONSTRAINT only_one_active EXCLUDE USING btree (is_active WITH =) WHERE (is_active = true)
);

CREATE TABLE qr_state_meta (
    state_id        INT PRIMARY KEY DEFAULT 1,
    weights_version BIGINT NOT NULL DEFAULT 1,
    synonym_version BIGINT NOT NULL DEFAULT 1,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT single_row CHECK (state_id = 1)
);
INSERT INTO qr_state_meta (state_id) VALUES (1);

-- ============================================================
-- 2) L2 语义缓存
-- ============================================================
CREATE TABLE qr_cache_entries (
    cache_id        BIGSERIAL PRIMARY KEY,
    query_hash      TEXT NOT NULL,
    weights_version BIGINT NOT NULL,
    synonym_version BIGINT NOT NULL,
    embedding       vector(1024) NOT NULL,    -- 维度以实际 embedding 模型为准(1024 与 text-embedding-3-large 对齐;换模型时同步迁移+重建索引)
    payload         JSONB NOT NULL,
    ttl_ts          TIMESTAMPTZ NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_preview   TEXT,
    UNIQUE (query_hash, weights_version, synonym_version)
);

CREATE INDEX idx_qr_cache_hnsw ON qr_cache_entries
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX idx_qr_cache_ttl ON qr_cache_entries (ttl_ts);

-- ============================================================
-- 3) 请求审计(§3.11 主数据源)
-- ============================================================
CREATE UNLOGGED TABLE qr_request_audit (
    request_id      UUID NOT NULL,
    ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    query_hash      TEXT NOT NULL,
    rewritten_hash  TEXT,
    pii_types       TEXT[],
    stage           TEXT NOT NULL,
    strategy_weights JSONB,
    weights_version BIGINT,
    synonym_version BIGINT,
    latency_ms      INT,
    cache_hit_l1    BOOLEAN,
    cache_hit_l2    BOOLEAN,
    cache_layer     TEXT,
    error_stage     TEXT,
    fallback_level  TEXT
) PARTITION BY RANGE (ts);
CREATE TABLE qr_request_audit_default PARTITION OF qr_request_audit DEFAULT;

CREATE TABLE qr_audit_buffer (
    audit_id        BIGSERIAL PRIMARY KEY,
    payload         JSONB NOT NULL,
    enqueued_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 3.2 Redis L1 数据结构

```text
qr:exact:{query_hash}   →  JSONB payload, TTL 3600s, sync setex
                         (key = hash(query + history_fingerprint + entities +
                                     weights_version + synonym_version))
```

版本号进 key 后,版本升级时旧 key 自然过期,**无须主动 SCAN+DEL**。

### 3.3 数据流

```mermaid
sequenceDiagram
    autonumber
    participant API as API入口
    participant QC as QueryCache
    participant R as Redis (L1)
    participant PG as PostgreSQL (L2 + state + audit)
    participant ORCH as Orchestrator
    participant LLM as LLM
    participant AUD as AuditBuffer

    API->>QC: lookup(query + ctx)
    QC->>PG: SELECT weights_version, synonym_version FROM qr_state_meta
    QC->>QC: 拼 key = hash(query + ctx + versions)
    QC->>R: GET qr:exact:{key}
    alt L1 hit
        R-->>QC: payload
        QC-->>API: 直接返回
        Note over QC,AUD: 异步落 audit(hit_l1=true)
    else L1 miss
        QC->>PG: SELECT payload FROM qr_cache_entries
                    WHERE (query_hash = $1 OR embedding <=> $2 < 0.08)
                    AND weights_version = $3 AND synonym_version = $4
                    AND ttl_ts > NOW() ORDER BY ... LIMIT 1
        alt L2 hit
            PG-->>QC: payload
            QC->>R: SETEX qr:exact:{key} 3600 payload (sync)
            QC-->>API: 返回
            Note over QC,AUD: 异步落 audit(hit_l2=true)
        else L2 miss
            QC->>ORCH: execute_stage('rewrite', query, ...)
            ORCH->>LLM: 调用(策略并行)
            LLM-->>ORCH: 响应
            ORCH-->>QC: 最佳结果
            par
                QC->>R: SETEX (sync)
            and
                QC->>PG: INSERT INTO qr_cache_entries (async, batch)
            and
                QC->>AUD: append audit record (async batch 5s flush)
            end
            QC-->>API: 返回
        end
    end

    Note over PG: 异步 worker:<br/>清 ttl_ts < NOW() 过期条目<br/>5s flush audit buffer
```

### 3.4 L2 召回 SQL(关键)

```sql
-- 把"精确"和"语义"两条路径折叠为单次 round trip
WITH version AS (
    SELECT weights_version, synonym_version
    FROM qr_state_meta
)
SELECT
    cache_id,
    payload,
    CASE
        WHEN query_hash = $1 THEN 'exact_match'::text
        ELSE 'semantic_match'::text
    END AS match_type,
    (embedding <=> $2) AS cosine_distance
FROM qr_cache_entries, version v
WHERE
    (query_hash = $1
        OR (embedding <=> $2) < 0.08)
    AND weights_version = v.weights_version
    AND synonym_version = v.synonym_version
    AND ttl_ts > NOW()
ORDER BY
    CASE WHEN query_hash = $1 THEN 0 ELSE 1 END,
    embedding <=> $2
LIMIT 1;
```

> 阈值 0.08 对应余弦相似度 0.92,与 v3.1 设计稿语义一致。

---

## 四、错误处理 + 可观测性(§3.11 同步覆盖)

### 4.1 故障矩阵

| 故障 | 检测 | 行为 | P0 | 备注 |
|---|---|---|---|---|
| Redis 不可用 | `redis.exceptions.ConnectionError` | 直接试 L2,L2 正常则正常返回 | 否 | 健康降级,记 `qr_cache_errors_total{stage="l1"}` |
| PG 不可用 | `psycopg.OperationalError` | 跳过 L2 直走 Orchestrator | **是** | L2 召回与 audit 不可用,触发告警 |
| PG 慢查询(L2 P95 > 200ms) | SQL 计时 | 强制 fallback,记 `slow_l2=true` | 否 | 阈值告警 |
| pgvector 索引损坏 | HNSW search 抛异常 | 删除该 cache_id,跳过 | 否 | repair worker 重建 |
| LLM 调用超时 | `asyncio.TimeoutError` | 由 v3.1 CircuitBreaker 接管;**不写 cache** | 否 | 关键:超时/失败结果永不污染 cache |
| LLM 输出超长(防注入) | `len(rewritten) > len(query)*3+100` | 视为注入丢弃,返回原 query | 否 | 复用 v3.1 校验 |
| 权重版本号变更 | `qr_state_meta.weights_version` 改变 | 旧 cache_entry 仍可读,新写入落新 version | 否 | 主动失效无须 |
| audit buffer flush 失败 | flush 任务异常 | 把该批次直接 `INSERT INTO qr_request_audit`(unlogged,但可直接落盘;若 PG 也挂了,记 metric + 留 in-memory 队列,下次启动 flush) | 是 | 关键:PII 数据此时不入 PG,而是仅留 in-memory 等下次启动再决定丢弃/落盘 |

### 4.2 OTel Spans

```
qr.cache.lookup (root span)
├── qr.cache.version.read     — 从 qr_state_meta 取权重版本号
├── qr.cache.l1.get           — Redis GET
│   └── hit/miss attribute
├── qr.cache.l2.query         — 单条 SQL 同时做精确+语义召回
│   ├── attribute: l2.match_type (exact|semantic|none)
│   └── attribute: l2.distance
└── qr.cache.write (fire-and-forget)
    ├── qr.cache.l1.setex (sync)
    └── qr.cache.l2.insert (async)
```

| Span | 关键属性 |
|---|---|
| `qr.cache.lookup` | `cache.layer`, `cache.latency_ms`, `cache.weights_version`, `cache.synonym_version` |
| `qr.cache.l2.query` | `l2.distance`, `l2.sql_plan_ms`, `l2.match_type` |
| `qr.cache.l1.setex` | `l1.size_bytes`, `l1.ttl_s` |
| `qr.cache.write` | `write.async.failures` |
| `qr.audit.flush` | `audit.batch_size`, `audit.flush.duration_ms` |

### 4.3 Prometheus Metrics

| 指标 | 类型 | Labels | 用途 |
|---|---|---|---|
| `qr_cache_requests_total` | Counter | `layer` (l1/l2/miss), `stage` | 每阶段缓存查询总量 |
| `qr_cache_hit_ratio` | Gauge (1m 滚动) | `layer` | 命中率 |
| `qr_cache_latency_seconds` | Histogram | `layer`, `op` (read/write) | 缓存读写延迟分布 |
| `qr_cache_errors_total` | Counter | `layer`, `error_type` | 失败计数 |
| `qr_cache_l2_distance` | Histogram | `match_type` | L2 余弦距离分布,验证 0.08 阈值 |
| `qr_state_weight_version` | Gauge | - | 当前权重版本号 |
| `qr_audit_flush_lag_seconds` | Gauge | - | audit buffer flush 滞后 |
| `qr_fallback_total` | Counter | `level`, `stage` | 降级次数 |

### 4.4 Grafana 4 面板

```yaml
[Row 1] Cache 健康
  - Hit rate by layer (timeseries)
  - L1/L2 latency P50/P95/P99
  - Errors by error_type (stacked area)
  - L2 distance P50 (gauge)

[Row 2] Cache 容量
  - qr_cache_entries 总行数 (single stat)
  - 命中率 vs LLM 调用次数 (timeseries dual axis)
  - 异步 flush 滞后 P95 (timeseries)

[Row 3] 权重/审计
  - qr_state_weight_version 变更 (event stream)
  - audit 写失败次数 (timeseries)

[Row 4] 路由总览
  - 平均单请求 LLM 调用次数(派生指标)
```

### 4.5 告警规则(Alertmanager)

```yaml
groups:
- name: query_rewriter_cache
  rules:
  - alert: QRCacheL2Unavailable
    expr: rate(qr_cache_errors_total{error_type="pgvector_down"}[5m]) > 0
    for: 1m
    labels: {severity: critical}
    annotations:
      summary: "L2 缓存 PG 持续报错,自动降级到编排器(成本↑↑)"

  - alert: QRCacheHitRateDrop
    expr: qr_cache_hit_ratio{layer="l1"} < 0.4
    for: 30m
    labels: {severity: warning}
    annotations:
      summary: "L1 命中率长期走低,可能 cache key 粒度过细"

  - alert: QRCacheL2DistanceShift
    expr: histogram_quantile(0.5, qr_cache_l2_distance_bucket) > 0.15
    for: 1h
    labels: {severity: warning}
    annotations:
      summary: "L2 余弦距离 P50 上涨,阈值 0.08 偏严,可能召回不足"

  - alert: QRAuditFlushLag
    expr: qr_audit_flush_lag_seconds > 30
    for: 5m
    labels: {severity: warning}
    annotations:
      summary: "审计 flush 滞后,可能审计数据不全"

  - alert: QRWeightVersionRolledBack
    expr: changes(qr_state_weight_version[10m]) > 5
    labels: {severity: warning}
    annotations:
      summary: "10 分钟内权重版本号变更 >5,可能是 §3.8 回滚频率过高"
```

### 4.6 安全侧联动(与 v3.1 §3.10 接口预留)

- **cache 内容不带 PII**:写入前 `PIIDetector.detect_and_mask(query)`,若含 PII 仅入 L1(短 TTL 600s)+ 跳过 L2(避免 PII 进 embedding/PG)。audit 表记录 `pii_types`
- **Prompt 注入防护**:LLM 输出做 `len(content) > 3*len(query)+100` 截断,丢弃的输入绝不写 cache

---

## 五、测试策略

### 5.1 测试分层

| 层 | 类型 | 工具 | 关键 case |
|---|---|---|---|
| 单元 | 纯函数 | pytest | key 构建;`PIIDetector` 命中时 L2 skip;LLM 超时时不被污染 |
| 集成 | Testcontainers(`postgres:16-pgvector`) | pytest + testcontainers | L2 单 SQL 双召回;权重升级切换;pgvector 索引损坏恢复 |
| 契约 | OTel Collector fake sink | pytest | spans 属性完整符合 §4.2 表 |
| 性能 | k6 / locust | 独立 bin | L1 hit P95 < 20ms;L2 miss → Orchestrator 全链路 P95 < 800ms;audit flush P95 < 2s |
| 回归 | replay 真机流量 | — | 历史 7 天 query 样本 replay,L2 recall@1 ≥ 0.7 |

### 5.2 关键单测(必含)

```python
def test_cache_key_includes_weights_version():
    """权重版本号变更时,缓存 key 必须变化(不可复用旧结果)"""
    base = build_cache_key(query="X", history="...", entities={},
                            weights_version=1, synonym_version=1)
    bumped = build_cache_key(query="X", history="...", entities={},
                              weights_version=2, synonym_version=1)
    assert base != bumped

def test_pii_query_never_enters_l2():
    """含手机号的 query 必须绕过 L2 写入"""
    cache = QueryCache(...)
    await cache.put("我的手机号是13812345678 怎么改", {"rewrite":"..."})
    rows = await pg.fetch("SELECT 1 FROM qr_cache_entries WHERE query_hash=$1",
                           hash("我的手机号是13812345678 怎么改"))
    assert rows == []

def test_l1_failure_does_not_block_hot_path(caplog):
    """Redis 不可用时,lookup 应 transparent 跳到 L2,不抛异常"""
    cache = QueryCache(redis_client=BrokenRedis(), pg=working_pg, ...)
    result = await cache.lookup("test query", ...)
    assert result is not None
    assert "redis unavailable" in caplog.text

def test_l2_semantic_match_with_correct_versions():
    """精确 hash 未命中但语义近邻命中时,应返回 embedding 最近的结果"""
    insert_payload(weights_version=1, synonym_version=1,
                   query="订单取消如何操作", rewrite="订单取消流程说明")
    result = await cache.lookup("怎么取消订单",
                                  weights_version=1, synonym_version=1)
    assert result["rewrite"] == "订单取消流程说明"
    assert result["match_type"] == "semantic"

def test_llm_timeout_does_not_pollute_cache():
    """LLM 超时时,该次结果绝不能写 cache"""
    cache = QueryCache(...)
    with mock.patch("orchestrator.execute_stage",
                     side_effect=asyncio.TimeoutError):
        try:
            await cache.lookup_and_store("x", context)
        except asyncio.TimeoutError:
            pass
    assert await redis.get(f"qr:exact:{...}") is None
```

---

## 六、迁移计划(灰度 + 回滚)

| 阶段 | 范围 | 启用项 | 验收 | 回滚 |
|---|---|---|---|---|
| **M0** | dev/test | 建表 + 索引 + 异步 worker 骨架 | 单测全绿 | — |
| **M1** | staging | L1 sync + L2 async 全链路接通;audit buffer 5s flush | 集成测试 100% 命中;L1 hit ≥ 30%,L2 hit ≥ 20% | 关 mock 切回 v3.1 ANN 路径 |
| **M2** | 生产 1% 灰度 | 全功能 + feature flag 控制 | L1 hit ≥ 40%,L2 hit ≥ 20%,P95 < 800ms,无 SLO 劣化 | flag OFF 即 5s 切回 |
| **M3** | 生产 10% | 同 M2 | L1 hit ≥ 50% | 同 M2 |
| **M4** | 生产 50% | 同 M2 | L1 hit ≥ 55%,L2 hit ≥ 25% | 同 M2 |
| **M5** | 生产 100% | 全量替换;关闭 v3.1 ANN L2 写入路径 | L1 hit ≥ 60%,LLM call 总数 ↓ 40% | rollback 通过 `qr_weights_history` 切回旧权重 |

**回滚兜底**:
- 任何阶段指标劣化 → 5s 内 `feature_flag.qr.cache_l2 = false`,回到 M1 之前
- L2 召回质量异常 → threshold 调整到 0.05(更严),仅 SQL 一次变更
- 终极回滚:DROP `qr_cache_entries` + 重新启用 ANN 老路径(RollbackManager 留 1 周窗口)

---

## 七、依赖与前置

- **数据库**:PostgreSQL 16 + pgvector 0.7+(supabase 现成)
- **Python**:asyncpg 0.29 已有(db_pool 复用),prometheus-client 0.20+(建议新增)
- **不新增 Python 异步任务队列**:audit buffer flush 由 `asyncio.create_task + 5s timer` 自管
- **需要本次新增的 SPMA-design-06-infrastructure 联动**:若监控体系已使用 OTel collector + Prom scrape,可直接接入,否则需先建 collector

---

## 八、与其它设计稿的关联

| 关联设计 | 关联点 |
|---|---|
| [SPMA-design-10-query-rewrite](../../designs/SPMA-design-10-query-rewrite.md) | 原版 rewrite_queries 入口,本次修改其缓存层 + 可观测层 |
| [SPMA-design-11-query-rewrite-optimization-v2-final](../../designs/SPMA-design-11-query-rewrite-optimization-v2-final.md) | v3.1 加固版基线,本次替换其 §3.7(L2 实现)、§3.11(数据源) |
| [SPMA-design-01-supervisor-agent](../../designs/SPMA-design-01-supervisor-agent.md) | `rewrite_node` 调用点改造(已由 commit `1bf49bad`、`104ee2fc`、`ab21b12d` 部分完成,本次增补缓存 + 观测) |
| [SPMA-design-06-infrastructure](../../designs/SPMA-design-06-infrastructure.md) | PG/Redis/OTel 基建对齐点 |

---

## 九、未决项 / 后续轮次

- **§3.8 冷启动、灰度与回滚**:本次仅给出 feature_flag 与回滚挂钩,详细 shadow/1%/10%/50%/100% 五阶段指标阈值下一轮
- **§3.9 成本控制**:分级模型触发与预算超额动作下一轮
- **§3.10 安全与合规**:PII 检测准确率与审计日志落盘加密细节下一轮

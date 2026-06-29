# Design: Query Rewriter Phase 1 — synonym_map 完全启用(G1+G2 修复)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G1 / G2
>
> **本文档角色**:8 份子 spec 中的第 1 份(Phase 1),基于 gap-driven 结构(现状 → 差距 → 详细设计)。
> **上下游依赖**:无上游;**下游** [P2 编排](SPMA-design-11-phase2-strategy-orchestration.md) 需消费 `synonym_map` 参数。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | **P0**(G1 阻断所有 synonym 相关代码运行;G2 阻断 synonym 启用) |
| 关联缺陷 | G1 / G2 |
| 关联文件 | `synonym_map.py`、`freshness.py`、`graph.py`、migration 004 |
| 预估工时 | 1 周 |
| 相关 ADR | 无 |

---

## 1. 现状核查(实际代码)

### 1.1 `synonym_map` 表缺失

**G1 🔴 P0**:`synonym_map` 表**不存在**,但以下代码已 SELECT 它:

| 文件 | 行 | 代码 | 触发条件 |
|------|---|------|---------|
| `src/spma/ingestion/synonym_map.py` | 63-69 | `SELECT ... FROM synonym_map` | `SynonymMap.query()` 被调用 |
| `src/spma/ingestion/synonym_map.py` | 95-105 | `SELECT canonical_term FROM synonym_map WHERE ...` | `SynonymMap.lookup()` 被调用 |
| `src/spma/ingestion/freshness.py` | 98-110 | `SELECT COUNT(*) FROM synonym_map` | `controller.refresh_synonym_map()` 定时任务 |

**当前 migrations 实际状态**:

| migration | 内容 | 是否含 synonym_map |
|-----------|------|-------------------|
| `001_session_enhancements.sql` | session 表 | ❌ |
| `002_qr_cache_and_state.sql` | qr_weights_history / qr_state_meta / qr_cache_entries | ❌ |
| `003_qr_audit_buffer.sql` | qr_audit_buffer | ❌ |

### 1.2 `rewrite_node` stub 未激活

**G2 🔴 P0**:`src/spma/agents/supervisor/graph.py:47-75`:

```python
async def rewrite_node(state: SupervisorState) -> dict:
    # synonym_map 暂未实现，保持为 None
    # 后续可以通过 spma.api.dependencies 获取
    synonym_map = None   # ← 硬编码 stub

    if qr_state_lookup is not None:
        weights_v, synonym_v = await qr_state_lookup()
    else:
        weights_v, synonym_v = 1, 1

    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
        synonym_map=synonym_map,   # ← 传 None
        ...
    )
    return {"rewritten_queries": rewritten}
```

下游 `query_rewriter._normalize_with_synonyms` 已正确处理 `None`(line 218 `if not synonym_map: return query`),所以**当前不报错**,但 synonym 功能**完全失效**。

### 1.3 `SynonymMap` 类已实现(无需重写)

`src/spma/ingestion/synonym_map.py:17-200` 已实现完整 CRUD:

| 方法 | 签名 | 用途 |
|------|------|------|
| `refresh(sources, auto_apply_threshold)` | 自动从 information_schema / prd / git 抽取 | 冷启动 |
| `query(status="all", limit=100) -> {"total": int, "entries": [dict]}` | 分页查询 | 后台管理 |
| `lookup(user_term) -> str \| None` | 单条查询 + 命中计数 | **热路径(首选)** |
| `apply_entry(entry_id)` | 激活 pending | 后台 |
| `mark_deprecated(entry_id)` | 弃用 | 后台 |

**`query()` 返回的 entries 字段**(实际 9 个):`id, user_term, canonical_term, category, source, confidence, status, hits_30d, last_triggered_at, created_at`。

### 1.4 `dependencies.get_db_pool` 已存在

`src/spma/api/dependencies.py:54`:

```python
def get_db_pool() -> "asyncpg.Pool":
    ...
```

### 1.5 现有测试覆盖

| 测试文件 | 覆盖 |
|---------|------|
| `tests/unit/agents/supervisor/test_query_rewriter.py` | `_normalize_with_synonyms` 24 case |
| `test_query_rewriter_with_cache.py` | cache 集成 |

**未覆盖**:`graph.rewrite_node` 端到端传递 `synonym_map` 不为 None 的路径。

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| `synonym_map` 表存在 | 不存在 | **G1:需新建 migration 004** |
| `graph.rewrite_node` 加载 `synonym_map` | 硬编码 `None` | **G2:需修改 graph.py** |
| 热路径用 `SynonymMap.lookup()` (单 key,快) | 未被调用 | **G2 修复时**顺便接入 |
| `SynonymMap` 类支持 | 已有 | **无差距** |
| 异常时降级到空 dict | 已有(`if not synonym_map: return query`) | **无差距** |
| `api/routes/query.py` 端到端 | 已通过 `rewrite_queries(synonym_map=...)` 接好 | **无差距(只要 graph.py 修复)** |

**关键洞察**:P1 实际只需要 **3 件事**:
1. 新建 migration 004 创建 `synonym_map` 表(修复 G1)
2. 修改 `graph.rewrite_node` 从 `db_pool` 加载 `synonym_map` 字典(修复 G2)
3. 加 1-2 个集成测试验证端到端链路

---

## 3. 详细设计

### 3.1 migration 004:创建 `synonym_map` 表

新建 `deployments/docker/migrations/004_synonym_map.sql`:

```sql
-- Migration 004: synonym_map 表 + 索引
-- 依赖: 002_qr_cache_and_state.sql(已部署)

CREATE TABLE IF NOT EXISTS synonym_map (
    id                  BIGSERIAL PRIMARY KEY,
    user_term           TEXT NOT NULL,
    canonical_term      TEXT NOT NULL,
    category            TEXT,                          -- table/column/code/req/general
    source              TEXT NOT NULL,                 -- information_schema / prd_titles / git_dirs / manual
    confidence          REAL NOT NULL DEFAULT 0.5
                        CHECK (confidence >= 0.0 AND confidence <= 1.0),
    status              TEXT NOT NULL DEFAULT 'pending_review'
                        CHECK (status IN ('active', 'pending_review', 'deprecated')),
    hits_30d            INTEGER NOT NULL DEFAULT 0,
    last_triggered_at   TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_term, canonical_term, source)
);

-- 索引设计
CREATE INDEX IF NOT EXISTS idx_synonym_user_term
    ON synonym_map (user_term) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_synonym_status_confidence
    ON synonym_map (status, confidence DESC, hits_30d DESC);

-- 命中计数自增(给 lookup() 用,避免热路径写两次)
CREATE OR REPLACE FUNCTION synonym_map_touch()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_synonym_map_touch ON synonym_map;
CREATE TRIGGER trg_synonym_map_touch
    BEFORE UPDATE ON synonym_map
    FOR EACH ROW EXECUTE FUNCTION synonym_map_touch();
```

**设计要点**:
- 字段与 `SynonymMap` 类 `query()` 返回的 9 个字段一一对应
- 唯一约束 `(user_term, canonical_term, source)`:同一来源下不重复
- 复合索引 `(status, confidence DESC, hits_30d DESC)`:支撑"按评分和热度取 top N"
- 触发器自动维护 `updated_at`,避免漏改
- **不**创建 `user_term` 上的一般索引(防止基数过大的全索引扫描,只在 `active` 子集建索引)

### 3.2 graph.rewrite_node 改造

修改 `src/spma/agents/supervisor/graph.py:47-75`:

```python
async def rewrite_node(state: SupervisorState) -> dict:
    # P1 修复:从 DB 加载活跃 synonym_map(降级到空 dict)
    try:
        syn_map_obj = SynonymMap(get_db_pool())
        # 复用已有 query(),但只取 active 状态 + 按评分和热度排序
        result = await syn_map_obj.query(status="active", limit=1000)
        # 转为 rewrite_queries 期望的 dict[user_term, list[canonical_term]]
        synonym_map: dict[str, list[str]] = {}
        for entry in result["entries"]:
            synonym_map.setdefault(entry["user_term"], []).append(
                entry["canonical_term"]
            )
    except Exception as e:
        logger.warning(f"Failed to load synonym_map, degrading to empty: {e}")
        synonym_map = {}

    if qr_state_lookup is not None:
        weights_v, synonym_v = await qr_state_lookup()
    else:
        weights_v, synonym_v = 1, 1

    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
        synonym_map=synonym_map,   # 改为实际加载的 dict
        conversation_history=state.get("conversation_history", ""),
        cache=qr_cache,
        audit_buffer=qr_audit_buffer,
        weights_version=weights_v,
        synonym_version=synonym_v,
    )
    return {"rewritten_queries": rewritten}
```

**设计要点**:
- **不**新建 `synonym_loader.py`(原 P1 spec 的多余设计)— 直接复用 `SynonymMap` 类
- **不**在 `api/routes/query.py` 改 — 它已经传 `synonym_map=None` 给 `rewrite_queries`,需同步修复
- try/except 降级到 `{}`:DB 故障时不阻断主链路(等同 v3.0 行为)
- `limit=1000`:业务上 synonym 总数 < 1000(实际 200 条)

### 3.3 api/routes/query.py 同步修复

修改 `src/spma/api/routes/query.py:96-98` 调用点(实际 1 行改动):

```python
# 原:rewritten = await rewrite_queries(... synonym_map=None ...)
# 改:同 graph.rewrite_node,先加载 synonym_map 再传
synonym_map = await _load_synonym_map_cached(get_db_pool())
rewritten = await rewrite_queries(
    ...,
    synonym_map=synonym_map,
    ...
)
```

**注**:此修复**可以延后**做,因为 `api/query.py` 是直接调用(不走 graph),可以单独 PR。但**必须**做,否则 API 路径不会激活 synonym。

### 3.4 推荐/可选优化:`SynonymMap.lookup()` 热路径

当前 `query()` 一次拉 1000 条全表。对**热路径**来说,如果 synonym 总数 < 100,直接按需 `lookup()` 更省内存。但需要权衡:

| 方案 | 优点 | 缺点 |
|------|------|------|
| A. `query()` 一次拉全(当前推荐) | 1 次 DB 调用,无 N+1 | 内存占 ~50KB,可忽略 |
| B. `lookup()` 按需查 | 内存 0 | 每个 user_term 1 次 DB 调用,O(N) RTT |

**当前阶段(200 条 synonym)**用方案 A。**P7 接入缓存后**,后续可平滑切到方案 B + 缓存(本 Phase 不实现)。

### 3.5 数据流(端到端)

```
用户 query
   │
   ▼
[graph.rewrite_node]
   │
   │ 1. get_db_pool() → asyncpg.Pool
   │ 2. SynonymMap(pool).query(status="active", limit=1000)
   │    │
   │    └─► SELECT ... FROM synonym_map WHERE status='active'
   │         │
   │         └─► {"total": N, "entries": [{...}, ...]}
   │
   │ 3. 转为 dict[user_term, list[canonical_term]]
   ▼
[synonym_map dict]
   │
   ▼
[rewrite_queries(synonym_map=...)]
   │
   ▼
[_do_rewrite_pipeline]
   │
   ▼
[_normalize_with_synonyms]  ← 已存在,直接用
   │ if not synonym_map: return query
   │ for user_term, system_terms in sorted(synonym_map.items(), ...):
   │     normalized = normalized.replace(user_term, " ".join(system_terms))
   ▼
[normalized query → 下游]
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增/修改的文件

| 文件 | 类型 | 改动 |
|------|------|------|
| `deployments/docker/migrations/004_synonym_map.sql` | **新增** | 创建 `synonym_map` 表 + 索引 + 触发器 |
| `src/spma/agents/supervisor/graph.py` | 修改 | `rewrite_node` 加载 `synonym_map` 替换 stub |
| `src/spma/api/routes/query.py` | 修改 | `handle_query` 同步加载(可延后) |
| `tests/integration/test_synonym_e2e.py` | **新增** | 端到端测试:migration → 插入 → rewrite → 断言展开 |

### 4.2 不需要做的事(明确)

- **不**新建 `synonym_loader.py` — 复用 `SynonymMap.query()`
- **不**在 `query_rewriter._normalize_with_synonyms` 改任何代码 — 已支持 `dict[user_term, list[str]]` 格式
- **不**在 `SynonymMap` 类加新方法 — 已够用
- **不**修改 `freshness.py` — 它读 `synonym_map` 表,migration 004 部署后自动可用

### 4.3 下游契约

[P2 编排](SPMA-design-11-phase2-strategy-orchestration.md) 在编排器中需消费 `synonym_map` 参数(P4 多路扩展会用)。本 Phase 保证 `graph.rewrite_node` 加载正确。

### 4.4 配置 Key

无新增。

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | migration 004 部署后,`SELECT * FROM synonym_map` 不报错 | ❌ 报错 | ✅ 不报错 | `psql -c "SELECT 1 FROM synonym_map LIMIT 1"` |
| V2 | `graph.rewrite_node` 不再传 `synonym_map=None` | stub | 实际加载 | 代码 review:`grep "synonym_map = None"` 应为 0 处 |
| V3 | 端到端:query 含 user_term → normalized 含 canonical_term | ❌ 不发生 | ✅ 发生 | `test_synonym_e2e.py` |
| V4 | 现有 24 个单测全过(无回归) | 24/24 | 24/24 | pytest |
| V5 | 部署后 24h,`synonym_map` 实际命中次数 > 0 | 0 | > 0 | `SELECT hits_30d FROM synonym_map` |
| V6 | DB 故障时降级到 `synonym_map={}`,不报错 | (无降级路径) | 异常被 try/except 吞,主链路继续 | 注入测试:toxiproxy 切断 DB |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:migration 004 部署失败 | PG 16 + pgvector 版本不匹配 | synonym 仍不可用 | 部署前在 staging 跑一次 + flyway/liquibase 校验 |
| **R2**:`query(limit=1000)` 超时 | synonym 表数据异常增长(>10k) | 整个 rewrite 慢 ~5-20ms | 监控 + `limit` 上限 1000 + P7 引入缓存 |
| **R3**:`SynonymMap.query()` 抛异常 | DB 故障 / SQL 语法错 | 主链路被阻断 | `try/except` 降级到空 dict(已在 §3.2) |
| **R4**:数据脏(误录入) | 业务方手动改表 | 用户 query 被错误展开 | `confidence < 0.5` 自动降级,人工审核 |
| **R5**:缓存一致性 | synonym 改后 `synonym_version` 未 bump | 旧缓存命中率异常 | `bump_synonym_version()` 已在 `qr_state.py:67`,P8 接入审核流时调 |

---

## 7. 实施步骤

### 7.1 PR 切分(2 个 PR)

**PR #1(migration 004,可独立合并)**
- 新增 `deployments/docker/migrations/004_synonym_map.sql`
- 在 staging 部署并验证:`psql -c "SELECT 1 FROM synonym_map LIMIT 1"`
- 验证 `SynonymMap.query()` / `lookup()` 在 staging 不再报"relation does not exist"
- 合并标准:staging 全链路测试通过,`freshness.py` 不再报错

**PR #2(graph.rewrite_node + 端到端测试,需 reviewer 重点 review)**
- 修改 `src/spma/agents/supervisor/graph.py:47-75` 按 §3.2 改造
- 新增 `tests/integration/test_synonym_e2e.py`(1. migration → 2. 插入 1 条 → 3. 调 rewrite_node → 4. 断言 normalized 含 canonical)
- 同步修改 `api/routes/query.py:96`(可分独立 PR,但建议同 PR)
- 合并标准:V2-V6 全部通过 + 现有 24 单测无回归

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1 | 写 migration 004 + 部署到 staging | PR #1 ready |
| D2 | Review PR #1 + 合并 + 验证 | - |
| D3-D4 | graph.py 改造 + 集成测试 | PR #2 ready |
| D5 | Review PR #2 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1 staging 部署成功,`freshness.py` 不再报"relation does not exist"
- [ ] PR #2 合并到 main
- [ ] 集成测试 100% 通过
- [ ] 现有 24 单测无回归
- [ ] 监控:`qr_synonym_lookups_total` 计数器存在(由 P6 引入)
- [ ] 文档:本 spec 文件 commit 到 `docs/designs/`

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:基于实际代码现状(G1 表不存在 + G2 stub 未激活)设计,不再新建 `synonym_loader.py`,复用已有 `SynonymMap` 类 |
| 2026-06-29 | 0.9 | (回退)初次拆分,与实际代码存在多处不符(已回退 commit `42ba24c4`) |

# P1 部署 & 灰度 Runbook

> 适用:Query Rewriter Phase 1 (synonym_map 启用)
> 紧急联系:#supma-deploy Slack 频道
> 回滚命令见末尾

## 部署顺序(必须严格按序)

### Step 1: 部署 migration 004(先于代码)

```bash
# 在生产 DB 上执行(运维操作)
psql "$DATABASE_URL" -f deployments/docker/migrations/004_synonym_map.sql

# 验证
psql "$DATABASE_URL" -c "SELECT to_regclass('synonym_map')"  # 应返回 'synonym_map'
psql "$DATABASE_URL" -c "SELECT column_name FROM information_schema.columns WHERE table_name='synonym_map' ORDER BY ordinal_position"
```

等待 5 分钟,观察:
- 是否有 slow query 告警
- `qr_synonym_lookups_total` 是否开始增长(此时还没激活,应=0)
- 错误日志中是否有 `relation "synonym_map" does not exist`(此时应已修复)

### Step 2: 部署应用代码(可灰度)

应用代码改动:
- `src/spma/agents/supervisor/graph.py` — `_load_synonym_map()` helper
- `src/spma/agents/supervisor/api/routes/query.py` — 复用 helper
- 测试文件:tests/integration/test_synonym_table.py / test_synonym_e2e.py / unit/test_graph_synonym.py

按项目 deploy 流程(若是 k8s / docker / 直接 ssh):
1. 部署到 staging 先
2. 运行 1 小时,验证无 P0 故障
3. 灰度到 10% 流量,观察 4 小时
4. 灰度到 50%,观察 4 小时
5. 100% 全量

## 监控指标(24h 灰度期间盯紧)

| 指标 | 期望 | 不期望 | 告警阈值 |
|------|------|--------|---------|
| `qr_synonym_lookups_total` 增量 | > 0(说明 stub 已被替换) | = 0(说明 stub 未激活) | < 100/小时 |
| `qr_audit_flush_lag_seconds` P99 | < 10s | > 60s | > 30s |
| 日志中 `Failed to load synonym_map` | 偶发(< 1/1000) | 频繁(> 1/100) | > 5/分钟 |
| `SELECT hits_30d FROM synonym_map` | > 0(说明真的被查询) | = 0(说明查询走了降级路径) | = 0 持续 1h |
| 整体 P95 延迟 | 变化 < 5ms(单次 DB 查询开销) | 增加 > 20ms | 增加 > 50ms |
| `qr_cache_*` 指标 | 不受影响 | 异常飙高 | - |

## 人工确认清单(24h 后)

- [ ] 24h 内无 P0/P1 故障
- [ ] `hits_30d` 实际增长(grep 日志确认 synonym_map 实际被用)
- [ ] P95 延迟变化在 ±5ms 内
- [ ] 无 PII 泄露(grep 审计日志确认无原文)
- [ ] 下游召回率定性观察(由用户反馈或离线抽样评估)

## 紧急回滚

### 代码回滚(git revert)

```bash
cd /Users/Ray/TraeProjects/SPMA-p1
git revert --no-commit 9d5fca3f b3863073 c7a156d7
git commit -m "revert(qr): rollback P1 synonym_map activation

Reason: <incident-id>
"
```

部署 revert commit。

### Migration 回滚(谨慎,需 DBA)

```sql
DROP TRIGGER IF EXISTS trg_synonym_map_touch ON synonym_map;
DROP FUNCTION IF EXISTS synonym_map_touch();
DROP TABLE IF EXISTS synonym_map;
```

**警告**:DROP 后 `SynonymMap.query()` 会报"relation does not exist",所有 synonym 功能失效。**先回滚代码再 DROP 表。**

## 已知风险与缓解

1. **首次部署大量空 synonym**:DB 表刚建,可能业务方未录入 synonym → 行为等同 v3.0(无 synonym 扩展)。**风险低**。
2. **`limit=1000` 上限**:业务上 synonym 总数 < 200,但若超出 1000,会被静默截断。监控 `synonym_map` 总行数。
3. **DB 故障降级**:已实现,DB 挂时 synonym_map 降级到 `{}`,行为等同 v3.0。**风险低**。
4. **同义词数据脏**:业务方录入错误映射,query 被错误展开。**由业务方负责**,synonym 录入流程不在 P1 范围。

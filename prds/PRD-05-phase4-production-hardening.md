# PRD: Phase 4 — 生产加固（企业级可靠性）

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **父文档:** [PRD-00 主概述](PRD-00-master-overview.md)
> **前置 Phase:** [Phase 3](PRD-04-phase3-supervisor-code-agent.md)（5 Agent 全部稳定运行）
> **工期:** +1-2 人·月（在 Phase 3 基础上增量）

---

## 一、阶段目标

**将系统从"功能完整"提升到"企业级可靠"。** 实现完整的六级降级体系（L0-L5）、熔断器、K8s 蓝绿部署、混沌工程演练，使系统可用性达到 99.9%。

### 用户故事

| 优先级 | 用户故事 | 验收标准 |
|--------|---------|---------|
| P0 | 作为运维，当 LLM API 不可用时系统应自动切换到本地模型，用户无感知 | L1 降级自动触发+自动恢复 |
| P0 | 作为运维，当 Redis 不可用时 Agent 应降级为单轮 pipeline，系统仍可用 | L2 降级自动触发 |
| P0 | 作为运维，系统可用性需达到 99.9%（月度统计） | 可用性 ≥ 99.9% |
| P1 | 作为运维，当某 Agent 延迟恶化时我能秒级将其回退到 pipeline 模式 | Feature flag 秒级生效 |
| P1 | 作为运维，我能看到系统的降级历史和恢复记录 | 降级历史面板 |
| P1 | 作为开发，新版本部署应支持蓝绿发布，出问题 30 分钟内可回滚 | 蓝绿部署+30min 回滚 |
| P2 | 作为运维，当向量数据库不可用时系统应自动切换到纯 BM25 检索 | L3 降级自动触发 |
| P2 | 作为 SRE，系统应能承受随机 Agent 被杀、网络分区等故障 | 混沌工程月度演练 |

---

## 二、输入与依赖

### 2.1 前置依赖

| 依赖项 | 状态 | 说明 |
|--------|------|------|
| Phase 3 完成 | ❌ 必须 | 5 Agent 全部稳定运行 |
| K8s 集群 | ❌ 需就绪 | 企业内部集群 |
| Helm Chart | ❌ 需编写 | 标准化部署 |
| Chaos Mesh | ❌ 需部署 | 混沌工程工具 |
| 企业 SSO | ❌ 需对接 | OIDC/LDAP |

---

## 三、任务拆解

### Task 4.1: 完整六级降级体系（1.5 周）

**目标：** 实现 L0-L5 的自动触发 + 自动恢复闭环。

**六级降级定义：**

| 级别 | 触发条件 | 降级动作 | 自动恢复条件 |
|------|---------|---------|-------------|
| **L0** | 正常 | 5 Agent 多轮循环 + Sonnet 生成 | — |
| **L1** | 主 LLM 超时率>10% OR 5xx>5% | 切换到 Qwen3-8B；完备度判断降级为确定性条件 | 主 LLM 健康检查连续 3 次通过 + 间隔 ≥ 60s |
| **L2** | Agent P99 延迟恶化>50% OR Token成本恶化>100% | 通过 feature flag 将单个 Agent 回退到单轮 pipeline | Agent 指标恢复正常 |
| **L3** | 向量数据库不可用 OR P99>500ms | 切换纯 BM25 关键词检索 | 向量库恢复可用 |
| **L4** | 后端检索大面积故障 | 返回 Redis 缓存的热点问答 | 后端检索恢复 |
| **L5** | 所有动态服务不可用 | 返回预定义 FAQ + 提示联系管理员 | 系统完全恢复 |

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 4.1.1 | 降级触发器：健康检查探针 + 指标阈值监控（Prometheus AlertManager） | `degradation_trigger.py` | 2天 |
| 4.1.2 | 降级执行器：按级别执行降级动作（切换LLM/切换检索模式/切换feature flag） | `degradation_executor.py` | 2天 |
| 4.1.3 | 自动恢复检测：定时健康检查（30s间隔）→ 条件满足 → 逐级恢复 | `degradation_recovery.py` | 1天 |
| 4.1.4 | 降级状态管理 + 管理 API（查看当前级别、手动触发降级/恢复、降级历史） | `degradation_manager.py` | 1天 |
| 4.1.5 | L4 缓存兜底：热点问答预计算 + Redis 缓存（TTL=1h，降级时延长） | `cache_fallback.py` | 1天 |
| 4.1.6 | L5 静态 FAQ 页面：预定义常见问题 + 联系管理员入口 | `static_faq.html` | 0.5天 |

**验收：**
- [ ] L1-L5 降级在触发条件满足后 < 30s 自动执行
- [ ] 自动恢复条件满足后 < 60s 自动恢复
- [ ] 降级/恢复事件完整记录到审计日志
- [ ] 降级管理 API 正常工作
- [ ] L4 缓存兜底至少有 50 条热点问答

---

### Task 4.2: 熔断器（1 周）

**目标：** 实现标准三态熔断器（CLOSED/OPEN/HALF_OPEN），保护微服务间调用。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 4.2.1 | 熔断器状态机实现：CLOSED→OPEN（连续失败≥5次）→HALF_OPEN（30s后）→CLOSED/HALF_OPEN | `circuit_breaker.py` | 2天 |
| 4.2.2 | LLM API 熔断器集成：Haiku/Sonnet API 调用前检查熔断状态 | LLM 调用路径改造 | 1天 |
| 4.2.3 | PGVector/Redis/ES 熔断器集成：数据库/缓存调用前检查熔断状态 | 数据访问路径改造 | 1天 |
| 4.2.4 | 熔断事件日志 + 监控面板集成 | 可观测性 | 1天 |

**熔断参数：**
```python
class CircuitBreakerConfig:
    failure_threshold: int = 5          # 连续失败次数阈值
    open_duration_seconds: int = 30     # 熔断持续时间
    half_open_probe_count: int = 3      # 半开探测请求数
    half_open_success_threshold: int = 2 # 半开恢复所需成功数
```

**验收：**
- [ ] 连续 5 次 LLM API 失败后熔断器进入 OPEN 状态
- [ ] OPEN 状态 30s 后自动进入 HALF_OPEN
- [ ] HALF_OPEN 下 3 次探测请求 ≥ 2 次成功 → 恢复 CLOSED
- [ ] HALF_OPEN 下 3 次探测请求 < 2 次成功 → 重新 OPEN
- [ ] 熔断不影响其他正常服务的调用

---

### Task 4.3: K8s 蓝绿部署（1 周）

**目标：** 实现标准化 Helm Chart + 蓝绿部署 + 30 分钟回滚。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 4.3.1 | Helm Chart 编写：所有服务（API Gateway, 5 Agent, BGE-M3, Qwen3-8B, Redis, PGVector） | `helm/spma/` | 2天 |
| 4.3.2 | ConfigMap + Secrets 分离：非敏感配置 vs 数据库连接/API Key/证书 | Helm values.yaml | 1天 |
| 4.3.3 | 蓝绿部署流水线：GitHub Actions/Jenkins → 构建镜像 → 部署 Green → 切换 LB → 保留 Blue 30min | CI/CD pipeline | 1天 |
| 4.3.4 | 健康检查探针：readinessProbe + livenessProbe（每个服务） | Helm templates | 0.5天 |
| 4.3.5 | 回滚脚本：一键切回 Blue 环境 | `rollback.sh` | 0.5天 |

**服务部署清单：**
| 服务 | 副本数 | 资源 | HPA |
|------|--------|------|-----|
| API Gateway (APISIX) | 2 | 4C8G | CPU > 70% |
| Supervisor Agent | 2-3 | 2C4G | 请求队列深度 |
| Doc Agent | 2-4 | 4C8G | 请求队列深度 |
| Code Agent | 2-3 | 2C4G | 请求队列深度 |
| SQL Agent | 2-3 | 2C4G | 请求队列深度 |
| Synthesis Agent | 2 | 2C4G | 请求队列深度 |

**验收：**
- [ ] Helm install 一键部署全栈服务
- [ ] 蓝绿部署：新版本部署到 Green → 切换 LB → Blue 保留 30 分钟
- [ ] 回滚 < 5 分钟（切换 LB 回 Blue）
- [ ] readinessProbe 正确阻止未就绪 Pod 接收流量

---

### Task 4.4: 混沌工程（1 周）

**目标：** 通过故障注入验证系统的降级和恢复能力。

**混沌实验清单：**

| 实验 | 注入方式 | 预期行为 | 验证指标 |
|------|---------|---------|---------|
| 杀 Doc Agent Pod | `kubectl delete pod` | 请求路由到其他副本；无 5xx | 错误率 = 0 |
| 杀 Redis Pod | `kubectl delete pod` | L2 降级为单轮 pipeline；< 30s 恢复 | 降级触发时间 < 30s |
| 模拟 LLM API 超时 | 注入 5s 延迟 | L1 降级为 Qwen3-8B | 降级触发时间 < 30s |
| 模拟 PGVector 不可用 | iptables DROP | L3 降级为纯 BM25 | 检索仍可用 |
| 网络分区（Agent 间） | NetworkChaos | Agent 超时 → 部分结果 + 标注 | 不崩溃 |
| 10s 硬上限测试 | CPU stress | 所有 Agent 强制停止 → 部分结果 | 不超 10s |
| Token 预算耗尽 | 模拟超大 query | 强制收敛 + 标注 | 用户收到提示 |

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 4.4.1 | Chaos Mesh 部署 + 实验定义（7 个混沌实验 YAML） | `chaos/experiments/` | 2天 |
| 4.4.2 | 实验自动化脚本：依次执行 → 收集指标 → 生成报告 | `chaos/run_experiments.py` | 1天 |
| 4.4.3 | 首次混沌演练执行 + 问题修复 | 演练报告 | 2天 |

**验收：**
- [ ] 7 个混沌实验全部通过
- [ ] 降级触发时间 < 30s（L1-L3）
- [ ] 10s 硬上限不被突破
- [ ] 无数据丢失、无数据损坏

---

### Task 4.5: 安全加固（1 周）

**目标：** 实现企业 SSO 集成 + mTLS + 审计日志完善。

**子任务：**

| # | 子任务 | 产出 | 工时 |
|---|--------|------|------|
| 4.5.1 | 企业 SSO 集成：OIDC/LDAP → JWT 签发（8h 有效期） | `auth_sso.py` | 2天 |
| 4.5.2 | API Key 管理：项目级只读 Key 的创建/撤销/列表 | `auth_apikey.py` | 1天 |
| 4.5.3 | mTLS 服务间通信：K8s cert-manager 自动签发+轮换 | Helm + cert-manager | 1天 |
| 4.5.4 | 审计日志完善：每次查询完整记录（用户/时间/query/分类/实体/各Agent结果/最终回答/用户反馈） | `audit_log.py` | 1天 |

**验收：**
- [ ] SSO 登录流程正常（飞书/企业微信/AD 至少一种）
- [ ] JWT 过期后正确拒绝请求
- [ ] mTLS 证书自动轮换
- [ ] 审计日志包含所有必要字段

---

### Task 4.6: 性能优化 + SLO 达标（1 周）

**目标：** 确保 P50/P95/P99 延迟达标。

**优化方向：**
1. BGE-Reranker v2 M3 集成：对 RRF Top-20 做精排（~50ms），提升 NDCG@10
2. LLM Prompt Caching：系统级 Prompt 缓存命中率提升
3. 连接池优化：PGVector/Redis 连接池大小调优
4. 批处理优化：BGE-M3 嵌入批处理大小调优（32→64）

**验收：**
- [ ] 单源查询 P50 < 3s, P95 < 6s, P99 < 8s
- [ ] 跨源查询 P50 < 6s, P95 < 12s, P99 < 15s
- [ ] 系统可用性 ≥ 99.9%（月度统计）

---

## 四、阶段输出与交付物

| 交付物 | 路径 | 格式 |
|--------|------|------|
| 降级体系完整代码 | `src/infrastructure/degradation/` | Python |
| 熔断器 | `src/infrastructure/circuit_breaker/` | Python |
| Helm Chart | `helm/spma/` | YAML |
| CI/CD 流水线 | `.github/workflows/deploy.yml` | YAML |
| 混沌实验定义 | `chaos/experiments/` | YAML |
| 混沌演练报告 | `chaos/reports/` | Markdown |
| SSO 集成 | `src/auth/` | Python |
| 审计日志 | `src/infrastructure/audit/` | Python |

---

## 五、验收标准

### 5.1 可靠性验收

- [ ] 六级降级（L0-L5）全部实现 + 自动触发 + 自动恢复
- [ ] 熔断器三态正常（CLOSED/OPEN/HALF_OPEN）
- [ ] 系统可用性 ≥ 99.9%（含降级路径；月度统计）
- [ ] Agent 降级率 < 10%

### 5.2 部署验收

- [ ] Helm install 一键部署
- [ ] 蓝绿部署 + 30 分钟内可回滚
- [ ] 所有服务有 readinessProbe + livenessProbe

### 5.3 混沌验收

- [ ] 7 个混沌实验全部通过
- [ ] 10s 硬上限不被突破
- [ ] 降级触发时间 < 30s
- [ ] 无数据丢失

### 5.4 安全验收

- [ ] SSO 登录正常
- [ ] mTLS 服务间通信加密
- [ ] 审计日志完整

---

## 六、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 降级体系本身引入新故障（降级逻辑 bug） | 中 | 混沌实验覆盖降级触发+恢复全路径 |
| Helm Chart 配置复杂，环境差异导致部署失败 | 中 | 多环境 values 文件分离（dev/staging/prod）；staging 环境先行验证 |
| 混沌工程影响生产环境 | 低 | 先在 staging 环境演练；生产演练选择低峰时段 |
| SSO 对接延迟（企业 IT 审批慢） | 中 | 降级方案：v1 先使用静态用户白名单 + JWT |

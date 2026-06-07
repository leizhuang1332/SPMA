# PRD: SPMA 企业级多源RAG智能问答系统 — 主概述

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **基于设计文档:** SPMA-design-00~07, API-00~06, SPMA-technology-selection
> **目标用户:** 产品经理（需求溯源）、开发工程师（故障定位/代码追溯）

---

## 目录

1. [产品愿景与目标](#一产品愿景与目标)
2. [系统架构概要](#二系统架构概要)
3. [分阶段交付路线图](#三分阶段交付路线图)
4. [阶段依赖关系](#四阶段依赖关系)
5. [全局成功标准](#五全局成功标准)
6. [资源与时间线](#六资源与时间线)
7. [风险管理](#七风险管理)
8. [各阶段 PRD 导航](#八各阶段-prd-导航)

---

## 一、产品愿景与目标

### 1.1 问题陈述

企业内产品经理和开发工程师面临严重的信息碎片化：

| 用户 | 现状 | 代价 |
|------|------|------|
| 产品经理 | Confluence/Wiki 搜索 PRD 文档 | 找不到历史版本、无法对比、人脑关联需求变更 |
| 开发工程师 | IDE grep 搜索代码 + 手动追踪需求 | 故障定位慢、新人上手周期长 |
| 双方共同 | 找 DBA 或手写 SQL 查询数据 | 瓶颈在 DBA 带宽、非技术人员无法自助 |

### 1.2 产品目标

构建一个**企业级多源RAG智能问答系统**，统一查询 PRD 文档、代码仓库和 SQL 数据库，支持自然语言跨源溯源。

### 1.3 核心价值主张

- **三源统一查询**：一个自然语言问题，同时搜文档、代码、数据
- **跨源溯源**：需求→代码→数据库表的影响链追踪
- **自然语言查数据**：PM 和开发无需写 SQL 即可查询业务数据
- **企业级可靠性**：99.9% 可用性，四级降级保障

---

## 二、系统架构概要

### 2.1 5 Agent 架构

```
User → API Gateway → Supervisor Agent → Send API 并行派发
                                          ├─ Doc Agent (检索Agent, ≤3轮, 2s)
                                          ├─ Code Agent (检索Agent, ≤3轮, 2s)
                                          └─ SQL Agent (执行Agent, ≤5轮, 3s)
                                               │
                                          fan-in 收集
                                               │
                                          Synthesis Agent (审计Agent, ≤2轮, 2s)
                                               │
                                          User Response
整体硬上限: 10s
```

### 2.2 Agent 收敛契约

| Agent | 类型 | 最大轮数 | 收敛条件 | 超时 |
|-------|------|---------|---------|------|
| Supervisor | 编排 | ≤5 | 所有Worker评分≥0.6 OR 重调度2次无改善 | 5s |
| Doc Agent | 检索 | ≤3 | 结果≥5条 AND (req_ids精确匹配 OR LLM判断充足) | 2s |
| Code Agent | 检索 | ≤3 | 结果≥3条 AND (调用链深度≤2层 OR 第3轮无新增文件) | 2s |
| SQL Agent | 执行 | ≤5 | SQL执行成功 AND 行数∈[1,10000] AND 语义验证通过 | 3s |
| Synthesis | 审计 | ≤2 | 引用覆盖率≥80% AND 无跨源矛盾 | 2s |

### 2.3 关键设计原则

1. **确定性收敛优先（代码规则）→ LLM 判断兜底**：能用规则判断的不用 LLM
2. **不静默失败**：任何降级/异常必须显式告知用户
3. **实体是检索加速器，不是检索闸门**：实体不足时语义搜索兜底，不拒绝回答
4. **每个 Agent 独立 feature flag**：可秒级回退到 pipeline 模式

---

## 三、分阶段交付路线图

### 阶段总览

| Phase | 名称 | 核心交付 | 用户价值 | 人·月 | 自然月 |
|-------|------|---------|---------|-------|--------|
| **Phase 0** | 收敛判断 Spike | LLM完备度判断验证报告 | 架构基石验证 | 0.5 | 0.5 |
| **Phase 1** | SQL Agent | Text-to-SQL 自然语言查数据 | PM和开发都能用自然语言查数据 | 1.5-2 | 1.5-2 |
| **Phase 2** | Doc Agent + Synthesis | PRD文档检索 + 跨源融合审计 | PM可以检索和对比历史PRD文档 | +1-1.5 | +1-1.5 |
| **Phase 3** | Supervisor + Code Agent | 多轮编排 + 代码检索 | 开发可追溯需求→代码→数据影响链 | +1.5-2 | +1.5-2 |
| **Phase 4** | 生产加固 | 完整降级体系 + K8s蓝绿部署 | 企业级可靠性达标99.9% | +1-2 | +1-2 |
| **Phase 5+** | 认知层 | 主动感知 + 跨源推演 | 主动式知识发现 | TBD | TBD |

**总时间线: 5-7.5 人·月**（2-3 人团队并行约 3-5 自然月完成 Phase 0-4）

### 每个阶段的独立价值

每个 Phase 独立交付、独立回滚、独立验收：

- **Phase 1 交付后**：用户可以通过自然语言查询数据库。即使后续 Phase 未完成，这已经有独立的生产价值
- **Phase 2 交付后**：增加了文档检索能力，PM 可以独立使用
- **Phase 3 交付后**：三源全覆盖 + 跨源溯源，系统核心功能完整
- **Phase 4 交付后**：企业级可靠性达标，可以正式对外承诺 SLA

---

## 四、阶段依赖关系

### 4.1 依赖图

```
Phase 0 (收敛判断Spike)
    │
    │ 验证 LLM 完备度判断精确率 ≥ 80%
    │
    ▼
Phase 1 (SQL Agent)
    │
    │ 依赖: PGVector + BGE-M3 + vLLM + Claude API
    │ 输出: Text-to-SQL 能力 + 进程内存状态
    │
    ▼
Phase 2 (Doc Agent + Synthesis Agent)
    │
    │ 依赖: Phase 1 基础设施 + Redis 热状态
    │ 输出: 文档检索 + RRF融合 + 引用审计
    │
    ▼
Phase 3 (Supervisor Agent + Code Agent)
    │
    │ 依赖: Phase 2 Doc/Synth Agent 稳定 + Postgres 冷trace
    │ 输出: 多轮编排 + 代码检索 + Agent Dashboard
    │
    ▼
Phase 4 (生产加固)
    │
    │ 依赖: 5 Agent 全部稳定运行
    │ 输出: 完整降级体系 + 熔断 + K8s蓝绿 + 混沌工程
    │
    ▼
Phase 5+ (认知层) — TBD
```

### 4.2 基础设施依赖链

```
Phase 1 建立的基础设施:
  ├─ PGVector + HNSW 索引
  ├─ BGE-M3 embedding 服务 (vLLM)
  ├─ Qwen3-8B 降级模型 (vLLM)
  ├─ Claude Haiku/Sonnet API
  ├─ APScheduler + PG Queue (摄入调度)
  ├─ APISIX API Gateway
  └─ Presidio 数据脱敏

Phase 2 新增:
  ├─ Redis (Agent 热状态)
  ├─ PG tsvector (BM25 全文检索)
  └─ Langfuse (Agent 循环追踪)

Phase 3 新增:
  ├─ LangGraph Send API (Supervisor 并行派发)
  ├─ Elasticsearch (BM25 专用引擎)
  ├─ PostgreSQL (冷 trace 存储)
  └─ BGE-Reranker v2 M3 (精排)

Phase 4 新增:
  ├─ 熔断器 (Circuit Breaker)
  ├─ Chaos Mesh
  ├─ mTLS (服务间加密)
  └─ 企业 SSO (OIDC/LDAP)
```

### 4.3 数据依赖

| 数据源 | Phase 1 | Phase 2 | Phase 3 | 新鲜度目标 |
|--------|---------|---------|---------|-----------|
| SQL Schema | ✅ information_schema 轮询 | - | - | < 10min |
| PRD 文档 | - | ✅ Webhook + 每日全量 | - | < 5min |
| 代码仓库 | - | - | ✅ Git Webhook | < 5min |

---

## 五、全局成功标准

### 5.1 检索质量

| 指标 | 目标 | 测量方式 |
|------|------|---------|
| Recall@10（跨三源） | ≥ 0.85 | Ground Truth 50条真实用户问题 |
| Doc Agent Recall@10 | ≥ 0.88 | 文档检索基准 |
| Code Agent Recall@10 | ≥ 0.80 | 代码检索基准 |
| SQL Execution Accuracy | ≥ 80% | 50条真实业务问题 + 对应正确SQL |

### 5.2 Agent 收敛质量

| 指标 | 目标 | 说明 |
|------|------|------|
| 收敛判断 LLM 精确率 | ≥ 80% | Phase 0 Spike 验证 |
| 虚假信心率 | < 15% | Agent 说够了但实际不够 |
| Agent 早停率 | < 30% | 收敛过严 |
| Supervisor 重调度率 | < 30% | 分类/抽取或Worker质量下降信号 |

### 5.3 响应延迟

| 查询类型 | P50 | P95 | P99 |
|---------|-----|-----|-----|
| 单源查询 | < 3s | < 6s | < 8s |
| 跨源查询 | < 6s | < 12s | < 15s |
| 整体硬上限 | — | — | 10s（强制中断）|

### 5.4 可用性与运维

| 指标 | 目标 |
|------|------|
| 系统可用性 | ≥ 99.9%（含降级路径；月度统计）|
| Agent 降级率 | < 10% |
| 知识新鲜度（文档/代码）| < 5 分钟 |
| 知识新鲜度（SQL Schema）| < 10 分钟 |
| 用户满意度（NPS）| ≥ 30（上线后3个月）|

---

## 六、资源与时间线

### 6.1 团队配置

| 角色 | 人数 | 职责 |
|------|------|------|
| 后端工程师 | 1-2 人 | Agent 开发、LangGraph 编排、API 开发 |
| 算法/NLP 工程师 | 1 人 | RAG 调优、嵌入模型、Reranker |
| 前端工程师 | 0.5 人 | Streamlit/Gradio Web UI |
| **合计** | **2-3 人** | |

### 6.2 总时间线

```
Month 1      Month 2      Month 3      Month 4      Month 5
├────────────┼────────────┼────────────┼────────────┼────────────┤
│  Phase 0   │   Phase 1 (SQL Agent)    │  Phase 2 (Doc+Synth)   │
│  (0.5月)   │     (1.5-2 月)           │    (+1-1.5 月)         │
│            │                          │                        │
│            │   Phase 3 可与 Phase 2 部分并行（不同工程师）        │
│            │          Phase 3 (Supervisor+Code) (+1.5-2月)      │
│            │                          │    Phase 4 (生产加固)    │
│            │                          │      (+1-2 月)          │
└────────────┴──────────────────────────┴──────────────────────────┘
```

---

## 七、风险管理

### 7.1 关键风险矩阵

| 风险 | 等级 | 影响 | 缓解措施 | Gating |
|------|------|------|---------|--------|
| 收敛判断 LLM 精确率不达标 | 🔴 最高 | 整个 Agent 循环设计的基础崩塌 | Phase 0 Spike + Plan B（纯确定性收敛）| 精确率 ≥ 80% 通关 |
| Agent 延迟不可预测 | 🟡 高 | 用户体验差，超时频繁 | 收敛契约 + 10s硬上限 + P50目标 | P50 < 5s |
| Token 成本爆炸 | 🟡 高 | 运营成本不可控 | 分级模型 + Token预算 + Haiku分担 | 成本恶化 > 50% 触发回滚 |
| 虚假信心（Agent说够了但不够）| 🟡 高 | 用户得不到完整答案 | Agent Eval Dataset + 指标监控 | > 15% 触发回滚 |
| 5 Agent 调试复杂度 | 🟡 中 | 开发效率低、bug难定位 | Agent Dashboard + 完整trace + per-Agent日志 | — |
| 中文→英文代码标识符映射 | 🟡 中 | Code Agent 搜索词构造质量差 | 同义词映射表 + LLM辅助翻译 | Phase 2 前完成评估 |

### 7.2 Plan B（收敛判断 Spike 未通过）

放弃 LLM 完备度判断，改用**纯确定性收敛 + 轮次上限**：
- Agent 在 max_rounds 内反复搜索直到命中确定性条件
- 仍保留"多轮搜索"的检索增强价值
- 去掉"自主判断"能力，降低 LLM 依赖

---

## 八、各阶段 PRD 导航

| PRD | 内容 | 预计工期 |
|-----|------|---------|
| [PRD-01 Phase 0](PRD-01-phase0-convergence-spike.md) | 收敛判断 Spike — 验证 LLM 完备度判断精确率 | 0.5 月 |
| [PRD-02 Phase 1](PRD-02-phase1-sql-agent.md) | SQL Agent — Text-to-SQL + SQL Guard + 语义验证循环 | 1.5-2 月 |
| [PRD-03 Phase 2](PRD-03-phase2-doc-synthesis-agent.md) | Doc Agent + Synthesis Agent + Redis 热状态 | +1-1.5 月 |
| [PRD-04 Phase 3](PRD-04-phase3-supervisor-code-agent.md) | Supervisor Agent + Code Agent + Postgres 冷 trace | +1.5-2 月 |
| [PRD-05 Phase 4](PRD-05-phase4-production-hardening.md) | 完整降级 L0-L5 + 熔断 + K8s 蓝绿 + 混沌工程 | +1-2 月 |
| [PRD-06 Phase 5+](PRD-06-phase5-cognitive-layer.md) | 认知层 — 主动感知 + 跨源推演 + 用户记忆 | TBD |

---

## 附录 A：设计文档索引

| 设计文档 | 内容 |
|---------|------|
| [SPMA-design-00](../docs/designs/SPMA-design-00-global-overview.md) | 全局概览、架构全景、决策背景 |
| [SPMA-design-01](../docs/designs/SPMA-design-01-supervisor-agent.md) | Supervisor Agent：意图分类、实体抽取、查询改写、多轮编排 |
| [SPMA-design-02](../docs/designs/SPMA-design-02-doc-worker.md) | Doc Agent：BM25+向量混合检索、完备度判断、线索扩展 |
| [SPMA-design-03](../docs/designs/SPMA-design-03-code-worker.md) | Code Agent：ripgrep实时搜索、搜索词构造、渐进式回退 |
| [SPMA-design-04](../docs/designs/SPMA-design-04-sql-worker.md) | SQL Agent：Schema RAG→LLM SQL→Guard→执行→语义验证 |
| [SPMA-design-05](../docs/designs/SPMA-design-05-data-ingestion.md) | 数据摄入管道：三源数据离线/异步摄入 |
| [SPMA-design-06](../docs/designs/SPMA-design-06-infrastructure.md) | 基础设施：技术选型、降级策略、安全、测试、部署 |
| [SPMA-design-07](../docs/designs/SPMA-design-07-agent-architecture.md) | 5独立Agent架构：收敛契约、质量函数、状态管理 |
| [API-00~06](../docs/API-00-overview.md) | API 契约：REST API、Agent通信协议、Worker契约、基础设施契约 |
| [SPMA-technology-selection](../docs/SPMA-technology-selection.md) | 技术选型清单：20个技术维度深度对比 |

## 附录 B：术语表

| 术语 | 说明 |
|------|------|
| Agent 循环 | 单个 Agent 内部的多轮自主推理过程（搜索→评估→不够→重搜） |
| 收敛契约 | 定义每个 Agent 何时停止循环的规则（最大轮数 + 收敛条件 + 超时） |
| 完备度判断 | Agent 对自己的检索结果是否"够了"的判断（确定性优先，LLM兜底） |
| 确定性收敛 | 用纯代码规则判断收敛（如结果数≥5且req_ids命中），不调LLM |
| 虚假信心 | Agent 判断"够了"但实际检索结果不完整（最危险的失败模式） |
| 实体 | 从用户问题中抽取的结构化检索参数（需求ID、表名、文件名等） |
| Send API | LangGraph 的并行派发机制——Supervisor 同时向多个 Worker 下发任务 |
| RRF | Reciprocal Rank Fusion——将 BM25 和向量检索的排名结果融合为统一排序 |
| WorkerOutput | 所有 Worker Agent 返回给 Supervisor 的统一输出格式 |

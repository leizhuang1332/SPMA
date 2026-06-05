# Design: 基础设施与运维设计

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 模块职责：技术选型、降级容灾、安全认证、测试策略、部署发布

---

## 一、关键技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| 向量数据库 | PGVector (HNSW) | 复用现有 PostgreSQL 运维能力；混合标量+向量查询；单表千万级向量可承载 |
| 嵌入模型 | BGE-M3（1024维） | 用于 Doc Worker 和 SQL Worker 的中英文文档/Schema 检索（MTEB Retrieval 领先）；Code Worker 不使用 embedding |
| LLM（主） | Claude Sonnet 4 | 中文能力优秀、响应延迟低（< 2s）、支持 Prompt Caching |
| LLM（备用/本地） | Qwen3-32B（vLLM 部署） | 内网可用、数据不出边界、降级时无缝切换 |
| 数据脱敏 | Presidio + 自定义规则 | LLM 调用前自动脱敏（手机号、邮箱、金额、内部IP等），外网 API 安全合规 |
| 编排框架 | LangGraph | Supervisor-Worker 模式原生支持、checkpoint 持久化、可审计的执行图 |
| 摄入调度 | APScheduler + PG 队列 | 替代 Kafka——2-3 人团队无需维护消息队列；摄入任务量级不需要流处理 |
| 缓存 | Redis | 热点问答缓存 TTL=1h（基于文档新鲜度）、查询结果缓存 TTL=5min |
| 可观测性 | OpenTelemetry → Grafana + Langfuse | 全链路追踪 + LLM 调用专项监控（token 用量、延迟、幻觉率） |
| 部署 | K8s Deployment + Rollout | 对 <100 用户场景，蓝绿部署优于金丝雀（流量太小无统计意义） |

---

## 二、多级降级策略

| 级别 | 触发条件 | 降级动作 | 自动恢复 |
|------|---------|---------|---------|
| L0 全功能 | 正常 | 混合检索 + LLM 生成 | - |
| L1 LLM降级 | 主LLM超时/错误率>阈值 | 切换到备用模型（本地部署） | 主LLM健康检查通过后切回 |
| L2 检索降级 | 向量数据库不可用/P99>500ms | 切换纯BM25关键词检索 | 向量库恢复后切回 |
| L3 缓存兜底 | 后端检索大面积故障 | 返回Redis缓存的热点问答 | 后端恢复后切回 |
| L4 静态兜底 | 所有动态服务不可用 | 返回预定义FAQ+提示联系管理员 | 系统完全恢复后切回 |

### Supervisor 分类降级

- **Haiku API 不可用** → 切到本地 Qwen3-8B（分类质量下降 2-3%，规则层兜底）
- **全部 LLM 不可用** → 纯规则分类（正则匹配 + 关键词），准确率约 85%，但系统仍可用
- **分类置信度极低**（无实体、无关键词）→ 保守策略：三源全查

---

## 三、熔断器设计（v2 启用，v1 用超时+重试）

```python
# v1: 简单超时+指数退避重试（max 3次）
# v2: 当微服务间调用量增大后升级为完整熔断器
class CircuitBreaker:
    """
    - failure_threshold: 连续失败5次触发熔断
    - timeout: 熔断30秒后进入半开状态
    - half_open_max_requests: 半开状态允许3次探测请求
    - 状态: CLOSED → OPEN → HALF_OPEN → CLOSED
    """
```

---

## 四、部分失败处理语义

当一个 Worker 成功、另一个 Worker 失败时，Synthesis Layer 的行为：

| 场景 | 处理方式 |
|------|---------|
| 单源查询，目标 Worker 失败 | 降级到关键词搜索；仍失败则返回"该数据源暂时不可用，请稍后重试" |
| 跨源查询，1/2 Worker 失败 | 用部分结果生成回答，标注"以下信息缺少 [代码/文档/SQL] 维度的结果" |
| 跨源查询，2/3 Worker 失败 | 保留成功 Worker 的结果，降级到 L1/L2；提示用户缩小查询范围 |
| 全部 Worker 失败 | 触发 L3 缓存兜底（v2）；v1 返回友好错误+建议联系管理员 |

---

## 五、安全与认证

### 5.1 认证方案
- **企业 SSO 集成：** OIDC/LDAP 对接企业统一身份认证（如飞书、企业微信、AD）
- **API Key：** 为自动化脚本/CI 管道提供项目级 API Key（只读）
- **JWT Session：** Web UI 使用 HttpOnly Cookie + JWT，过期时间 8h

### 5.2 数据安全
- **LLM 调用脱敏层：** 所有发送到外部 LLM API 的文本经过 Microsoft Presidio + 自定义规则脱敏，自动替换手机号、邮箱、金额、内部 IP、主机名
- **本地 LLM 优先：** 高敏感查询路由到本地部署的 Qwen3-32B（无数据出境）
- **只读副本：** SQL Worker 永远只连接数据库只读副本，物理隔离写操作
- **审计日志：** 每次查询记录——用户、时间、原始问题、检索到的片段、生成的回答、数据来源标注。用于合规审计和 RAG 质量改进
- **RBAC：** v2 引入基于角色的数据访问控制（PM 不可查薪资表、开发不可查客户 PII 等）

---

## 六、测试策略

| 层级 | 内容 | 工具 | 覆盖率目标 |
|------|------|------|-----------|
| 单元测试 | 每个 Worker 的检索逻辑、SQL Guard 校验规则、脱敏规则 | pytest | ≥ 80% |
| 集成测试 | Worker → PGVector 检索、LLM 调用 Mock、只读副本连接 | pytest + testcontainers | 核心路径 100% |
| RAG 质量评估 | Recall@10、MRR、Faithfulness（NLI 事实一致性）、幻觉率 | Ragas + 人工标注 50 条 ground truth | 与 Success Criteria 对齐 |
| E2E 测试 | 完整查询链路（Supervisor → Workers → Synthesis → 响应） | pytest + fixture 数据 | 20+ 核心场景 |
| 混沌工程（v2） | 随机杀 Worker、模拟网络分区、注入延迟 | Chaos Mesh | 月度演练 |

**Ground Truth 构建计划：** 从 Shadowing 观察中收集 50 条真实用户问题 → 人工标注正确答案和引用来源 → 作为 RAG 评估的基准数据集。后续从线上日志持续扩充。

### 分类器评估

- **评估集：** 从 Shadowing 观察和用户测试中收集 100 条真实查询 → 人工标注正确的 `sources`
- **目标：** 分类准确率 ≥ 95%，实体 correctness ≥ 90%，completeness ≥ 85%
- **持续改进：** 每次分类结果记录日志，每周 review 20 条，往规则层加 pattern

---

## 七、部署与发布

### 7.1 部署方式
- **平台：** Kubernetes 集群（企业内部）
- **打包：** Helm Chart 标准化部署
- **CI/CD：** GitHub Actions / Jenkins → 构建镜像 → 推送私有 Registry
- **配置管理：** ConfigMap + Secrets（数据库连接、LLM API Key、OIDC 配置等）

### 7.2 发布策略

v1 蓝绿部署（Blue-Green）——新版本部署到 Green 环境 → 切换 LB 指向 → 保留 Blue 30 分钟用于回滚。金丝雀部署在 <100 用户场景下无统计意义，v2 再评估。

### 7.3 知识新鲜度目标

- 文档/代码变更到可检索 < 5 分钟（通过 Git Webhook / CI 触发增量索引）
- 数据库 Schema 变更 < 10 分钟（定时轮询 + 手动触发刷新）

### 7.4 可用性目标

系统可用性 ≥ 99.9%（含降级路径；月度统计）

---

## 八、可观测性

- **全链路追踪：** OpenTelemetry → Grafana
- **LLM 专项监控：** Langfuse（token 用量、延迟、幻觉率）
- **映射表健康度仪表盘：**
  - 总映射数（目标：100-300 条）
  - 本月新增 / 本月清理
  - 映射命中率
  - Top 10 最高频触发映射

---

## 九、不静默失败原则

当系统用了降级策略（语义搜索兜底、纯规则抽取、上下文继承）时，必须在返回给用户的回答末尾附加透明标注：

> ℹ️ 本次查询未识别到具体需求ID或表名，结果基于语义搜索，可能不够精确。[怎样提高搜索精度？](link-to-tips)

同样，SQL 查询结果的局限也通过 over-communication 暴露给用户：

```
查询结果: ¥847,230.00

⚠️ 数据质量提示:
- 此查询基于 orders 表，包含所有 status='paid' 的订单
- 未排除可能存在的测试订单
- 未进行汇率换算
```

---

## 十、Dependencies

- **数据源接入：** Confluence/Wiki API、Git 仓库访问权限、数据库只读副本
- **LLM 服务：** Claude API（通过数据脱敏层） + 本地 Qwen3-32B（vLLM 部署，高敏感查询和无外网时使用）
- **基础设施：** K8s 集群、PostgreSQL + pgvector、Redis
- **人员：** 后端工程师 1-2 人、算法/NLP 工程师 1 人（RAG 调优）、前端工程师 0.5 人（Streamlit/Gradio 即可）

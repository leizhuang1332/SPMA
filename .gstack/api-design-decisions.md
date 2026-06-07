# SPMA API 设计决策文档

> 配套文件：[api-contract.yaml](api-contract.yaml) — 完整 OpenAPI 3.1 规范

---

## 一、API 设计原则

### 1.1 从设计文档中推导 API，而非凭空创造

每个 endpoint 和字段都有设计文档中的出处。不新增设计文档未描述的交互。

| 设计文档出处 | 对应 API |
|-------------|---------|
| 总体架构图 — API Gateway/LB（限流、鉴权、审计日志） | 所有 endpoint 共用网关层 |
| Supervisor — 意图分类、实体抽取、查询改写 | `POST /query` 的 classification 返回 |
| Supervisor — 反问用户（bare entity + 短查询 + 无历史） | `E1011` 错误码 + `retryable: false` |
| Supervisor — 上下文继承（从上一轮继承 module） | `session_id` 参数 |
| Supervisor — 不静默失败原则（透明标注） | `degradation.user_notice` 字段 |
| SQL Worker — 用户确认闸门（高风险查询） | `POST /query/{query_id}/confirm` |
| SQL Worker — 透明标注（数据质量、不可复现性） | `data_freshness` 对象 |
| 数据摄入 — 手动触发刷新（提供 API） | `POST /admin/reindex` |
| 同义词映射 — 人工审核队列（Web UI） | `GET /admin/synonyms` + approve/reject |
| 审计日志 — 每次查询记录 | `GET /admin/audit-logs` |
| 基础设施 — SSO + JWT + API Key | securitySchemes |
| 基础设施 — 三级降级自动触发/自动恢复 | `/health` + `/health/degradation` |

### 1.2 优先支持设计文档明确描述的交互

以下交互在设计文档中反复出现（3 次以上），列为 P0 接口：
- 核心查询（非流式 + 流式）
- SQL 确认闸门
- 健康检查（含降级状态）
- 数据源新鲜度
- 用户反馈

以下交互在设计文档中被提及但未展开，列为 P1 接口：
- 会话管理
- 同义词审核
- 重索引触发
- 审计日志

### 1.3 RESTful，但不过度 RESTful

**选择 REST 而非 gRPC 的理由：**
- 团队规模 2-3 人，REST 的学习成本和维护成本更低
- K8s Ingress/LB 对 REST 的原生支持优于 gRPC（健康检查、限流、日志）
- 企业内部工具，不需要 gRPC 的极致性能（P99 < 3s 目标在 REST 上完全可达）

**不过度 RESTful 的地方：**
- `/admin/synonyms/{id}/approve` — 按纯 REST 应该是 `PATCH /admin/synonyms/{id}` 改 status 字段。但 approve/reject 是业务操作（有 side effect：写生产映射表、记录审批人），用动词 endpoint 更明确
- `/query/{query_id}/confirm` — 同理，这是一个有副作用的业务动作，不是简单的资源状态变更

---

## 二、关键设计决策

### 2.1 为什么查询不设计为异步模式（202 Accepted + 轮询）

**决定：** 默认同步返回（30s 超时），超时返回部分结果。不引入异步轮询。

**理由：**
- 设计文档的延迟目标是 P99 < 8s（跨源最坏情况）。30s 内绝大部分查询都能完成
- 异步轮询增加客户端复杂度（Web UI 需要 loading → polling → 结果的状态机），对于内部工具来说过度工程
- 即使超时，Supervisor 的部分失败处理语义（设计文档已定义）保证返回部分结果，不是白屏
- 如果未来出现确实需要 >30s 的查询（如超大仓库全量代码搜索），再引入 202+轮询

**但**保留 `/query/{query_id}` 作为"查询结果查询"——客户端可以在 SSE 断开后用它获取完整结果。这不是异步模式的入口，是容错出口。

### 2.2 为什么需要两个查询端点（/query + /query/stream）

**决定：** 提供两个端点，一个返回完整 JSON，一个 SSE 流式返回。

**理由：**
- CI/脚本场景用 `/query`：一次性拿到完整 JSON，不需要处理 SSE
- Web UI 用 `/query/stream`：用户可以看到"系统正在做什么"——分类中 → 文档搜索中 → 代码搜索中 → 生成回答中。设计文档强调透明度，SSE 是实现透明度的技术手段
- 两个端点共享同一个后端处理逻辑，只有传输层不同

**SSE 事件设计的关键点：**
- `worker_start` / `worker_progress` / `worker_result` 让用户看到三个 Worker 的并行执行过程。这对应设计文档中的"不静默失败"原则——如果 Doc Worker 超时，用户能实时看到，而不是等 5 秒后得到一个缺少文档维度的结果
- `confirmation_required` 事件允许流式查询中途暂停，等待用户确认 SQL。确认后 SSE 流恢复

### 2.3 为什么 DegradationInfo 嵌入在正常响应中而非单独查询

**决定：** 降级信息作为 `degradation` 字段嵌入在每个查询响应中，同时提供独立的 `/health/degradation`。

**理由：**
- 设计文档的核心原则："不静默失败"。每次查询响应都必须告知用户当前是否在降级模式，以及这对结果质量的影响
- 即使用户不主动查询降级状态，他们也能在每次回答末尾看到透明标注（`degradation.user_notice`）
- 独立的 `/health/degradation` 是给运维/监控用的，不是给普通用户

### 2.4 错误码为什么用 E{N}{NNN} 格式而非 HTTP status 码

**决定：** 使用自定义错误码体系（E0001-E5999），HTTP status 码作为补充。

**理由：**
- HTTP status 码信息量不足：一个 500 可能是分类失败、SQL 生成失败、数据库连接失败——它们需要的用户提示和重试策略完全不同
- 结构化错误码让前端可以做差异化 UI：
  - `E1011`（查询太短）→ 展开输入框 + 提示"请补充更多信息"
  - `E2001`（Doc Worker 超时）→ 展示部分结果 + 标注缺失维度 + "重试"按钮
  - `E5001`（未登录）→ 跳转 SSO 登录页
- 告警规则可以按错误码分组：`E3xxx` 类错误飙升 → 检查数据库；`E1xxx` 类错误飙升 → 检查 LLM 服务
- HTTP status 码仍然保留，满足 REST 语义。大多数情况下 HTTP status 和错误码是一致的（E1xxx → 4xx/5xx）

### 2.5 认证为什么是 Bearer JWT + API Key 双模式

**决定：** Web UI 用 SSO → JWT Cookie，CI/脚本用 API Key Header。

**理由：**
- 对应设计文档明确定义的两种认证方式
- JWT 在 Cookie 中自动携带，Web UI 无需手动管理 token。8h 过期 + SSO 自动刷新
- API Key 是项目级的（非用户级），适合 CI 管道——不需要为 Jenkins 建一个"用户"
- API Key 只有只读权限——设计文档明确"CI/自动化脚本提供项目级 API Key（只读）"

**当前版本不做的事（v2）：**
- RBAC 细粒度权限（PM 不能查薪资表）
- API Key 的细粒度 scope 限制（只限特定数据源）
- 审计日志的敏感字段脱敏展示

---

## 三、与设计文档的对照检查

| 设计文档承诺 | API 是否体现 | 位置 |
|------------|------------|------|
| 跨源多跳查询（"这个需求影响了哪些代码和哪些表？"） | ✅ 一次 POST /query 完成完整流水线 | `POST /query` → response.sources 含 doc+code+sql |
| 多轮对话上下文继承 | ✅ session_id + 对话历史 | `POST /query` session_id + `GET /sessions/{id}` |
| 反问用户（模糊查询） | ✅ E1010/E1011 错误码 | error response 带 message + retryable=false |
| 不静默失败的透明标注 | ✅ 每次响应附带 degradation.user_notice | 所有 /query 响应 |
| SQL 高风险确认闸门 | ✅ 专用 endpoint | `POST /query/{query_id}/confirm` |
| 数据新鲜度标注 | ✅ data_freshness 对象 | 所有 /query 响应 |
| 四级降级自动触发/自动恢复 | ✅ degradation.level + /health/degradation | 每次响应 + 独立端点 |
| 同义词映射人工审核 | ✅ 审核队列 API | `/admin/synonyms` + approve/reject |
| 手动触发重索引 | ✅ reindex API | `POST /admin/reindex` |
| 审计日志（每次查询记录） | ✅ 审计日志查询 | `GET /admin/audit-logs` |
| 用户反馈闭环 | ✅ 反馈提交 | `POST /feedback` |
| SSO + JWT 认证 | ✅ bearerAuth | securitySchemes |
| CI/脚本 API Key（只读） | ✅ apiKey | securitySchemes |
| 限流 | ✅ 429 + E0030 | error response |

**没有遗漏。**

---

## 四、前端集成指南

### 4.1 Web UI 典型查询流程

```
1. 用户输入查询 → POST /query/stream (session_id=xxx)
2. SSE 事件到达:
   event: classification  → 显示"正在分析你的问题..."
   event: worker_start    → 显示"文档搜索中 ⏳ 代码搜索中 ⏳ 数据分析中 ⏳"
   event: worker_result   → 更新状态"文档搜索完成 ✅ (5条结果)"
   event: synthesis       → 逐 chunk 渲染 Markdown 回答
   event: done            → 隐藏 loading，显示完整回答 + 引用来源
   event: error           → 错误提示（不中断流，如果有部分结果继续展示）
3. 异常分支:
   needs_confirmation=true → SSE 暂停在 confirmation_required 事件
                          → 展示 SQL 确认 UI（SQL语句 + 风险提示 + [确认][修改]按钮）
                          → 用户确认 → POST /query/{id}/confirm
                          → SSE 恢复，从 worker_result(sql) 继续
4. 点踩反馈:
   用户点 👎 → 弹出原因选择器 → POST /feedback {query_id, rating: "negative", reason: "inaccurate"}
```

### 4.2 CI/脚本典型查询流程

```bash
# 非流式查询，一次性获取结果
curl -X POST https://spma.internal/api/v1/query \
  -H "X-API-Key: ${SPMA_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "过去7天各状态的订单数",
    "max_sources": ["sql"],
    "timeout_ms": 15000
  }'

# 响应: {query_id, answer, sources, sql_executed, degradation, latency_ms}
# CI 可以解析 answer 中的 Markdown 表格或直接用 sql_executed 验证
```

---

## 五、后续迭代

### v1.1（Phase 1 完成后）
- 查询历史搜索（全文搜索历史查询）
- 批量查询（一次提交多个问题，异步处理）
- Webhook 通知（查询完成回调 URL）

### v2（Phase 4 完成后）
- RBAC 细粒度权限
- API Key scope 限制
- 查询模板（保存常用查询、参数化）
- 数据导出（查询结果导出 CSV/Excel）
- WebSocket 替代 SSE（真正的双向通信）

---

## 附录：与其他 RAG 系统 API 的对比

| 系统 | 查询方式 | 流式 | 降级信息 | 确认闸门 | 审计日志 |
|------|---------|------|---------|---------|---------|
| **SPMA（本设计）** | REST + SSE | ✅ | ✅ 嵌入响应 | ✅ SQL确认 | ✅ |
| OpenAI Assistants | REST + SSE | ✅ | ❌ 不透明 | ❌ | ❌ |
| LangChain LangServe | REST | ✅ | ❌ | ❌ | ❌ |
| Perplexity API | REST | ✅ | ❌ | ❌ | ❌ |
| Glean | REST | ❌ | ✅ 有限 | ❌ | ✅ |

SPMA 的 API 在"透明性"这个维度上超过了绝大多数同类系统。这是因为设计文档反复强调的"不静默失败"原则——在企业内部工具场景下，让用户知道系统在做什么、哪里可能不准确，比给一个看似完美的答案更重要。

# Design: 基础设施与运维设计

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 模块职责：技术选型、Agent 基础设施、状态管理、降级容灾、安全认证、测试策略、部署发布

---

## 一、关键技术选型

| 组件 | 选型 | 理由 |
|------|------|------|
| **Agent 框架** | **LangGraph** | StateGraph + Send API + Checkpointer，最大社区。支持独立子图+namespace隔离 |
| 向量数据库 | PGVector (HNSW) | 复用现有 PostgreSQL 运维能力；混合标量+向量查询；单表千万级向量可承载 |
| 嵌入模型 | BGE-M3（1024维） | 用于 Doc Agent 和 SQL Agent 的中英文文档/Schema 检索（MTEB Retrieval 领先）；Code Agent 不使用 embedding |
| LLM（完备度判断） | **Claude Haiku** | ~300ms, ~$0.001/次，用于 Agent 完备度判断和语义验证 |
| LLM（生成/分类） | **Claude Sonnet** | 高质量回答，中文能力优秀，支持 Prompt Caching |
| LLM（本地降级） | **Qwen3-8B (vLLM)** | API 不可用时的兜底。Agent 完备度判断降级到此模型 |
| 数据脱敏 | Presidio + 自定义规则 | LLM 调用前自动脱敏（手机号、邮箱、金额、内部IP等），外网 API 安全合规 |
| **热状态存储** | **Redis**, TTL=5min | Write-through，Agent 状态持久化。Key: `agent:{user_id}:{session_id}:{query_id}:{agent_type}:state` |
| **冷 trace 存储** | **PostgreSQL** | Write-back（查询结束后异步写入），不阻塞 Agent 循环。Agent 执行 trace 完整记录 |
| 摄入调度 | APScheduler + PG 队列 | 替代 Kafka——2-3 人团队无需维护消息队列；摄入任务量级不需要流处理 |
| 缓存 | Redis | 热点问答缓存 TTL=1h（基于文档新鲜度）、查询结果缓存 TTL=5min |
| **重试/退避** | **tenacity** | exponential backoff（multiplier=0.5, max=2s），LLM 调用 429 自动重试 |
| 可观测性 | OpenTelemetry → Grafana + Langfuse | 全链路追踪 + LLM 调用专项监控 + **Agent Dashboard**（收敛轮次、虚假信心率、Token成本） |
| 部署 | K8s Deployment + Rollout | 对 <100 用户场景，蓝绿部署优于金丝雀（流量太小无统计意义） |

---

## 二、Agent 基础设施

采用"状态共享 + 循环独立"的抽象层次：

```python
class AgentState(TypedDict):
    """所有Agent共享的状态字段"""
    round: int
    confidence: float
    results: list[dict]
    token_used: int
    assessment_history: list[str]

class AgentInfrastructure:
    """共享方法——每个Agent通过mixin使用"""
    def check_convergence(self, state: AgentState) -> bool: ...
    def consume_budget(self, tokens: int) -> bool: ...
    def save_checkpoint(self, state: AgentState) -> None: ...
```

### Checkpointer 隔离

每个 Agent 子图使用独立 LangGraph checkpointer namespace，避免并发冲突：

```python
# Supervisor Send API 注入 namespace 到子图 config
Send("doc_agent", state, config={"configurable": {"namespace": f"{query_id}:doc"}})
Send("code_agent", state, config={"configurable": {"namespace": f"{query_id}:code"}})
Send("sql_agent", state, config={"configurable": {"namespace": f"{query_id}:sql"}})
```

### 状态存储层级

| 层级 | 技术 | Phase | 语义 |
|------|------|-------|------|
| **进程内存** | Python dict | Phase 1 | SQL Agent 单进程内 5 轮循环。无外部依赖 |
| **Redis 热状态** | Redis, TTL=5min | Phase 2+ | Write-through，每次状态变更同步写入 |
| **Postgres 冷 trace** | PostgreSQL | Phase 3+ | Write-back（查询结束后异步写入），不阻塞 Agent 循环 |

### 降级路径

```
Redis可用 ──→ Agent多轮循环（正常）
Redis不可用 ──→ Agent降级为单轮pipeline模式（退化为非Agentic行为）
              → logger.warning("Redis unavailable, falling back to single-pass mode")
```

---

## 三、Agent Action Guard（安全白名单）

每个 Agent 可调用的工具受白名单限制：

```python
ALLOWED_ACTIONS = {
    'supervisor': ['classify_intent', 'extract_entities', 'rewrite_query',
                   'send_to_worker', 'collect_results', 'evaluate_quality',
                   'reschedule', 'finalize'],
    'doc':  ['bm25_search', 'vector_search', 'metadata_filter', 
             'completeness_check', 'expand_clues', 'return_results'],
    'code': ['ripgrep', 'read_file', 'glob', 'ast_expand', 
             'completeness_check', 'return_results'],
    'sql':  ['schema_rag', 'generate_sql', 'validate_sql', 'execute_readonly',
             'verify_results', 'semantic_verify', 'return_results'],
    'synthesis': ['rrf_fusion', 'llm_generate', 'citation_check', 
                  'cross_source_check', 'return_results'],
}

class AgentActionGuard:
    def validate(self, agent_type: str, proposed_action: str) -> bool:
        if proposed_action not in ALLOWED_ACTIONS.get(agent_type, set()):
            logger.error(f"BLOCKED: {agent_type} attempted {proposed_action}")
            return False
        return True
```

---

## 四、多级降级策略

| 级别 | 触发条件 | 降级动作 | 自动恢复 |
|------|---------|---------|---------|
| L0 全功能 | 正常 | 5 Agent 多轮循环 + LLM 生成 | - |
| L1 LLM降级 | 主LLM超时/错误率>阈值 | 切换到备用模型（本地 Qwen3-8B）；完备度判断降级为确定性条件 | 主LLM健康检查通过后切回 |
| L2 Agent降级 | Agent 循环延迟恶化/Token 成本爆炸 | 单个 Agent 通过 feature flag 回退到单轮 pipeline 模式 | Agent 指标恢复正常后切回 |
| L3 检索降级 | 向量数据库不可用/P99>500ms | 切换纯BM25关键词检索 | 向量库恢复后切回 |
| L4 缓存兜底 | 后端检索大面积故障 | 返回Redis缓存的热点问答 | 后端恢复后切回 |
| L5 静态兜底 | 所有动态服务不可用 | 返回预定义FAQ+提示联系管理员 | 系统完全恢复后切回 |

### Supervisor Agent 分类降级

- **Haiku API 不可用** → 切到本地 Qwen3-8B（分类质量下降 2-3%，规则层兜底）
- **全部 LLM 不可用** → 纯规则分类（正则匹配 + 关键词），准确率约 85%，但系统仍可用
- **分类置信度极低**（无实体、无关键词）→ 保守策略：三源全查

---

## 五、Agent 回滚机制（Feature Flags）

每个 Agent 有独立 feature flag，可秒级回退到 pipeline 模式：

```yaml
agents:
  sql_agentic: false        # false=当前3轮自修复, true=agentic语义验证（≤5轮）
  doc_agentic: false        # false=BM25+向量单次, true=agentic完备度判断+多轮
  code_agentic: false       # false=ripgrep+AST单次, true=agentic完备度判断+多轮
  supervisor_agentic: false # false=单次分类+规则, true=多轮编排
  synth_agentic: false      # false=LLM一次生成, true=自检循环
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。

---

## 六、熔断器设计（v2 启用，v1 用超时+重试）

```python
# v1: 简单超时+指数退避重试（tenacity, max 3次）
# v2: 当微服务间调用量增大后升级为完整熔断器
class CircuitBreaker:
    """
    - failure_threshold: 连续失败5次触发熔断
    - timeout: 熔断30秒后进入半开状态
    - half_open_max_requests: 半开状态允许3次探测请求
    - 状态: CLOSED → OPEN → HALF_OPEN → CLOSED
    """
```

### LLM 并发与退避

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=2),
    retry=lambda e: isinstance(e, RateLimitError),
)
async def call_llm_with_retry(prompt: str):
    try:
        return await haiku_api.generate(prompt)
    except RateLimitError:
        raise  # tenacity 自动重试
    except Exception:
        return await local_qwen.generate(prompt)  # 降级本地模型
```

> 429 不计入 Agent 轮次计数——这是基础设施问题，不是搜索质量问题。

---

## 七、部分失败处理语义

当一个 Agent 成功、另一个 Agent 失败时，Synthesis Agent 的行为：

| 场景 | 处理方式 |
|------|---------|
| 单源查询，目标 Agent 失败 | 降级到关键词搜索；仍失败则返回"该数据源暂时不可用，请稍后重试" |
| 跨源查询，1/2 Agent 失败 | 用部分结果生成回答，标注"以下信息缺少 [代码/文档/SQL] 维度的结果" |
| 跨源查询，2/3 Agent 失败 | 保留成功 Agent 的结果，降级到 L1/L2；提示用户缩小查询范围 |
| 全部 Agent 失败 | 触发 L4 缓存兜底（v2）；v1 返回友好错误+建议联系管理员 |
| Agent 超时（10s 硬上限） | 返回部分结果 + "⏱️ 查询超时，以下结果可能不完整" |

---

## 八、错误与降级速查表

| 错误 | 处置 | 用户感知 |
|------|------|---------|
| `MaxRoundsExceeded` | 返回当前最佳结果+透明标注 | "搜索未穷尽，结果可能不完整" |
| `LLMTimeoutError` | 降级为 pipeline 模式 | 无感知（自动切换） |
| `LLMResponseMalformed` | 重试1次→规则兜底 | 无感知 |
| `LLMRefusalError` | 跳过该轮，标记收敛 | 该 Agent 结果为空 |
| `TokenBudgetExhausted` | 强制收敛，取当前最佳结果 | "为控制成本，搜索未完全穷尽" |
| `AgentStateError`（Redis不可用）| 降级为单轮 pipeline | 无感知 |
| `AllWorkersEmptyError` | 返回"未找到相关信息" + 建议 | "未找到相关信息" |
| `ResultCardinalityAnomaly` | 分析原因→调整 SQL→重生成 | 无感知（自动修正） |
| `CitationNotFoundError` | 标注该引用为"未验证" ⚠️ | 引用标记 ⚠️ |
| `CrossSourceContradiction` | 显式标注矛盾 | "⚠️ Doc 说 A，Code 说 B" |

---

## 九、Token 预算管理

| 查询类型 | 预算（LLM调用次数） |
|---------|-------------------|
| 单源简单 | 8 次 |
| 单源复杂 | 12 次 |
| 跨源 | 20 次 |
| 三源全查 | 25 次 |

- 完备度判断使用 **Haiku**（~$0.001/次），最终生成使用 **Sonnet**（~$0.005/次）
- Phase 1-2 使用单 Agent token 硬上限（硬截断）
- Phase 3（Supervisor 上线时）引入跨 Agent 预算分配

---

## 十、成本模型

| 场景 | 非Agent | Agent P50 | Agent P99 | 成本倍率 |
|------|---------|----------|----------|---------|
| 单源 SQL 查询 | 3次 (~$0.015) | 5次 (~$0.025) | 8次 (~$0.04) | ~2x |
| 单源 Doc 查询 | 2次 (~$0.01) | 4次 (~$0.015) | 6次 (~$0.02) | ~1.5x |
| 跨源查询 | 5次 (~$0.025) | 10次 (~$0.04) | 20次 (~$0.07) | ~2.5x |
| 三源全查 | 5次 (~$0.025) | 12次 (~$0.05) | 25次 (~$0.09) | ~3x |

---

## 十一、延迟 SLO

| 查询类型 | P50 | P95 | P99 |
|---------|-----|-----|-----|
| 单源查询 | < 3s | < 6s | < 8s |
| 跨源查询 | < 6s | < 12s | < 15s |
| 整体硬上限 | — | — | 10s（强制中断）|

---

## 十二、安全与认证

### 12.1 认证方案
- **企业 SSO 集成：** OIDC/LDAP 对接企业统一身份认证（如飞书、企业微信、AD）
- **API Key：** 为自动化脚本/CI 管道提供项目级 API Key（只读）
- **JWT Session：** Web UI 使用 HttpOnly Cookie + JWT，过期时间 8h

### 12.2 数据安全
- **LLM 调用脱敏层：** 所有发送到外部 LLM API 的文本经过 Microsoft Presidio + 自定义规则脱敏，自动替换手机号、邮箱、金额、内部 IP、主机名
- **本地 LLM 优先：** 高敏感查询路由到本地部署的 Qwen3-8B（无数据出境）
- **只读副本：** SQL Agent 永远只连接数据库只读副本，物理隔离写操作
- **审计日志：** 每次查询记录——用户、时间、原始问题、检索到的片段、生成的回答、数据来源标注。用于合规审计和 RAG 质量改进
- **RBAC：** v2 引入基于角色的数据访问控制（PM 不可查薪资表、开发不可查客户 PII 等）

---

## 十三、测试策略

| 层级 | 内容 | 工具 | 覆盖率目标 |
|------|------|------|-----------|
| 单元测试 | 每个 Agent 的检索逻辑、SQL Guard 校验规则、脱敏规则、确定性收敛条件、Action Guard | pytest | ≥ 80% |
| 集成测试 | Agent → PGVector 检索、LLM 调用 Mock、只读副本连接、Send API 并行编排、Checkpointer 隔离 | pytest + testcontainers | 核心路径 100% |
| Agent 测试 | Agent 完整循环测试（含收敛条件验证、MockLLM） | pytest + MockLLM | 每个 Agent 3+ 循环场景 |
| RAG 质量评估 | Recall@10、MRR、Faithfulness（NLI 事实一致性）、幻觉率 | Ragas + 人工标注 50 条 ground truth | 与 Success Criteria 对齐 |
| Agent Eval | 收敛判断精确率、虚假信心率、收敛率、Recall@10 变化 | Agent Eval Dataset（50条×golden results） | 每次 Agent 变更后自动跑 |
| E2E 测试 | 完整查询链路（Supervisor → Agents → Synthesis → 响应） | pytest + fixture 数据 | 20+ 核心场景 |
| 混沌工程（v2） | 随机杀 Agent、模拟网络分区、注入延迟、Redis 不可用、LLM 超时 | Chaos Mesh | 月度演练 |

### LLM Mock 策略

```python
@pytest.fixture
def mock_llm_converges_round3():
    """模拟第3轮才收敛的LLM"""
    return MockLLM(responses=[
        {"sufficient": False, "missing": "缺少性能数据"},    # round 1
        {"sufficient": False, "missing": "缺少历史版本"},    # round 2
        {"sufficient": True, "confidence": 0.85},             # round 3
    ])

@pytest.mark.parametrize("mock_llm,expected_rounds", [
    ("mock_llm_converges_round1", 1),
    ("mock_llm_converges_round3", 3),
    ("mock_llm_never_converges", 3),  # 达到 max_rounds 强制停止
])
def test_agent_convergence(mock_llm, expected_rounds, request):
    mock = request.getfixturevalue(mock_llm)
    agent = DocAgent(llm=mock)
    result = await agent.search("用户登录为什么变慢了")
    assert result.rounds_used == expected_rounds
```

### Agent Eval Dataset

```
tests/eval/agent_eval_dataset.json:

{
  "queries": [
    {
      "query": "用户登录模块的PRD改了哪些内容",
      "query_type": "cross_source",
      "golden_docs": ["doc_001:chunk_3", "doc_001:chunk_5"],
      "golden_code": ["src/auth/oauth.py", "src/auth/login.py"],
      "golden_sql_tables": ["users", "user_sessions"]
    }
  ]
}
```

**Ground Truth 构建计划：** 从 Shadowing 观察中收集 50 条真实用户问题 → 人工标注正确答案和引用来源 → 作为 RAG 评估的基准数据集。后续从线上日志持续扩充。

### 分类器评估

- **评估集：** 从 Shadowing 观察和用户测试中收集 100 条真实查询 → 人工标注正确的 `sources`
- **目标：** 分类准确率 ≥ 95%，实体 correctness ≥ 90%，completeness ≥ 85%
- **持续改进：** 每次分类结果记录日志，每周 review 20 条，往规则层加 pattern

---

## 十四、可观测性

### 全链路追踪
- **OpenTelemetry → Grafana**：全链路追踪
- **Langfuse**：LLM 调用专项监控（token 用量、延迟、幻觉率）

### Agent 专用指标

| 指标 | 告警阈值 |
|------|---------|
| `agent_rounds_p99` | p99 > max_rounds → 调整收敛参数 |
| `agent_false_confidence_rate` | > 15% → 完备度判断质量下降 |
| `agent_early_stop_rate` | > 30% → 收敛条件过严或搜索质量下降 |
| `agent_degradation_rate` | > 10% → 基础设施问题（Redis/LLM） |
| `agent_loop_efficiency` (第N轮新增 / 第N-1轮) | < 0.3 → 边际收益递减，考虑收紧max_rounds |
| `supervisor_reschedule_rate` | > 30% → 分类/实体抽取或Worker检索质量下降 |
| `supervisor_timeout_rate` | > 5% → 5s超时设置过紧或Worker延迟恶化 |

### Agent Dashboard

- 循环次数分布（histogram per agent）
- 虚假信心率趋势（line chart）
- Token 成本热力图（by query_type）
- 降级/早停率（stacked area）
- 边际效率（bar chart，per round）

### 映射表健康度仪表盘

- 总映射数（目标：100-300 条）
- 本月新增 / 本月清理
- 映射命中率
- Top 10 最高频触发映射

---

## 十五、部署与发布

### 15.1 部署方式
- **平台：** Kubernetes 集群（企业内部）
- **打包：** Helm Chart 标准化部署
- **CI/CD：** GitHub Actions / Jenkins → 构建镜像 → 推送私有 Registry
- **配置管理：** ConfigMap + Secrets（数据库连接、LLM API Key、OIDC 配置、Agent feature flags 等）

### 15.2 发布策略

v1 蓝绿部署（Blue-Green）——新版本部署到 Green 环境 → 切换 LB 指向 → 保留 Blue 30 分钟用于回滚。金丝雀部署在 <100 用户场景下无统计意义，v2 再评估。

### 15.3 知识新鲜度目标

- 文档/代码变更到可检索 < 5 分钟（通过 Git Webhook / CI 触发增量索引）
- 数据库 Schema 变更 < 10 分钟（定时轮询 + 手动触发刷新）

### 15.4 可用性目标

系统可用性 ≥ 99.9%（含降级路径；月度统计）

---

## 十六、不静默失败原则

当系统用了降级策略（语义搜索兜底、纯规则抽取、上下文继承、Agent 超时）时，必须在返回给用户的回答末尾附加透明标注：

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

## 十七、Dependencies

- **数据源接入：** Confluence/Wiki API、Git 仓库访问权限、数据库只读副本
- **LLM 服务：** Claude API（Haiku 用于完备度判断 + Sonnet 用于生成/分类） + 本地 Qwen3-8B（vLLM 部署，API 不可用时的兜底）
- **基础设施：** K8s 集群、PostgreSQL + pgvector、Redis（Agent 热状态存储）
- **Agent 框架：** LangGraph（StateGraph + Send API + Checkpointer）
- **人员：** 后端工程师 1-2 人、算法/NLP 工程师 1 人（RAG 调优）、前端工程师 0.5 人（Streamlit/Gradio 即可）

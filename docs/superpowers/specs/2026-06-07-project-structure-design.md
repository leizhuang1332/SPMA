# Design: SPMA 项目目录结构设计

> 状态：DESIGN COMPLETE
> 生成日期：2026-06-07
> 关联文档：[SPMA 全局概览](../../designs/SPMA-design-00-global-overview.md) | [技术选型](../SPMA-technology-selection.md)

---

## 一、设计前提

| 决策 | 选择 | 理由 |
|------|------|------|
| 仓库策略 | Monorepo 单包 | 2-3 人团队，避免跨仓库版本同步；共享基础设施代码 |
| 源码布局 | src layout（`src/spma/`） | PEP 推荐；防止意外导入；强制 editable install |
| 包管理器 | uv | 用户指定；比 pip 快 10-100x；原生 workspace 支持 |
| Python 版本 | 3.13.* | 用户指定；最新稳定版 |
| 内部组织 | 领域驱动 + 跨领域共享层 | Agent 内高内聚 + 共享基础设施集中管理 |
| Linter/Formatter | Ruff | 单工具替代 Black+isort+flake8 |

---

## 二、目录结构总览

```
SPMA/
├── src/
│   └── spma/                    # 主包（src layout）
│       ├── __init__.py
│       ├── py.typed             # PEP 561 类型标记
│       │
│       ├── agents/              # 5 个 Agent（领域驱动）
│       │   ├── base.py          # Agent 基类: check_convergence, consume_budget, save_checkpoint
│       │   ├── supervisor/      # 编排 Agent（≤5轮, 5s）
│       │   ├── doc/             # 检索 Agent（≤3轮, 2s）
│       │   ├── code/            # 检索 Agent（≤3轮, 2s）
│       │   ├── sql/             # 执行 Agent（≤5轮, 3s）
│       │   └── synthesis/       # 审计 Agent（≤2轮, 2s）
│       │
│       ├── api/                 # REST API 层
│       │   ├── app.py           # FastAPI app 工厂
│       │   ├── dependencies.py  # 依赖注入
│       │   ├── routes/          # 8 个端点
│       │   ├── middleware/      # auth, rate_limit, request_id, audit, cors
│       │   └── schemas/         # 请求/响应 Pydantic 模型
│       │
│       ├── ingestion/           # 数据摄入管道
│       │   ├── scheduler.py     # APScheduler 配置
│       │   ├── doc_pipeline.py  # PRD 文档摄入
│       │   ├── code_pipeline.py # 代码仓库摄入
│       │   ├── sql_pipeline.py  # SQL Schema 摄入
│       │   ├── parsers/         # Docling / Unstructured / TreeSitter
│       │   ├── chunkers/        # 递归语义分块 / 代码分块
│       │   └── synonym_map.py   # 同义词映射表管理
│       │
│       ├── retrieval/           # 跨 Agent 共享检索基础设施
│       │   ├── embeddings.py    # BGE-M3 嵌入服务
│       │   ├── vector_store.py  # PGVector 客户端
│       │   ├── bm25.py          # BM25 检索（PG tsvector → ES Phase 3）
│       │   ├── bm25_interface.py # BM25 抽象接口（Protocol）
│       │   ├── reranker.py      # RRF 融合 + BGE-Reranker（Phase 3）
│       │   ├── hybrid_search.py # 混合检索编排
│       │   └── search_logger.py # 检索日志结构化写入
│       │
│       ├── llm/                 # LLM 抽象层
│       │   ├── clients.py       # Haiku/Sonnet/Qwen3-8B 统一客户端
│       │   ├── masking.py       # Presidio + 自定义规则脱敏
│       │   ├── token_budget.py  # Token 预算追踪器
│       │   └── prompts/         # 共享 Prompt 模板
│       │
│       ├── infrastructure/      # 跨领域基础设施
│       │   ├── state_store.py   # 三层状态存储（内存/Redis/PG）
│       │   ├── cache.py         # Redis 缓存
│       │   ├── feature_flags.py # Feature Flag 服务
│       │   ├── degradation.py   # 六级降级管理器（L0-L5）
│       │   ├── circuit_breaker.py # 熔断器（v2 启用）
│       │   ├── audit.py         # 审计日志
│       │   └── security.py      # JWT + API Key + RBAC
│       │
│       ├── models/              # 共享类型定义（Pydantic/TypedDict）
│       │   ├── agent_state.py   # AgentState 基类
│       │   ├── worker_output.py # WorkerOutput / Citation
│       │   ├── entities.py      # ExtractedEntities / WorkerEntities
│       │   ├── classification.py
│       │   ├── convergence.py
│       │   ├── search_log.py
│       │   └── common.py
│       │
│       ├── config/              # 配置加载
│       │   ├── settings.py      # Pydantic Settings
│       │   └── constants.py     # 硬编码常量
│       │
│       └── observability/       # 可观测性
│           ├── tracing.py       # OpenTelemetry
│           ├── metrics.py       # Prometheus 指标
│           └── langfuse_integration.py
│
├── tests/
│   ├── conftest.py              # 全局 fixtures
│   ├── unit/                    # 单元测试（mock, <5s 全量）
│   ├── integration/             # 集成测试（testcontainers）
│   ├── eval/                    # 评估数据集与脚本（独立运行）
│   └── e2e/                     # 端到端测试（真实 LLM，手动触发）
│
├── config/                      # 外部 YAML 配置文件
│   ├── spma.yaml
│   ├── feature_flags.yaml
│   ├── alerts.yaml
│   └── ingestion.yaml
│
├── deployments/
│   ├── docker/                  # 4 个 Dockerfile（api/agent/ingestion/vllm）
│   └── helm/spma/               # Helm Chart
│
├── scripts/                     # 运维/工具脚本
├── docs/                        # 现有设计文档（不变）
├── prds/                        # 现有 PRD 文档（不变）
│
├── pyproject.toml
├── uv.lock
├── README.md
├── CLAUDE.md
└── .gitignore
```

---

## 三、Agent 内部文件约定

每个 Agent 统一遵循以下文件结构：

| 文件 | 职责 | 必要 |
|------|------|------|
| `graph.py` | LangGraph StateGraph 构建——节点 + 条件边 + 编译 | ✅ 必须 |
| `state.py` | Agent 专属 State TypedDict 定义 | ✅ 必须 |
| `prompts.py` | Agent 专属 LLM Prompt 模板 | ✅ 必须 |
| 领域逻辑模块 | 检索/生成/校验等核心逻辑 | 按复杂度加减 |

Supervisor Agent（文件最多，7 个）：
- `classifier.py` — 意图分类（LLM + 规则兜底）
- `entity_extractor.py` — 实体抽取（共享 LLM 调用）
- `completeness.py` — 实体完备度评估（确定性代码）
- `query_rewriter.py` — 查询改写流水线（6 种方案可插拔）
- `quality.py` — Worker 质量三维评分
- `dispatcher.py` — Send API 并行派发 + fan-in

SQL Agent（逻辑最复杂）：
- `schema_rag.py` — Schema RAG 检索
- `generator.py` — LLM SQL 生成
- `guard.py` — SQLGuard 五层校验（非协商项）
- `executor.py` — 只读副本执行
- `verifier.py` — 语义验证
- `quality.py` — QualityReport 生成

Doc/Code/Synthesis Agent 各 3-4 个领域逻辑文件。

---

## 四、共享基础设施的设计原则

### 4.1 BM25 抽象层（`retrieval/bm25_interface.py`）

```python
# Protocol 定义，不依赖具体实现
class BM25Interface(Protocol):
    async def search(query: str, top_k: int, filters: dict) -> list[BM25Hit]: ...
    async def index(documents: list[dict]) -> None: ...
```

- Phase 1-2：`PGtsvectorBM25` 实现（零新服务，PG 内置全文搜索）
- Phase 3：`ElasticsearchBM25` 实现（IK 中文分词，kNN 混合搜索）
- 切换时 Agent 代码零改动

### 4.2 状态存储三层（`infrastructure/state_store.py`）

```python
class StateStorageProtocol(Protocol):
    async def save(key: str, state: dict, ttl: int) -> None: ...
    async def load(key: str) -> dict | None: ...
```

- `ProcessMemoryStore` — Phase 1（Python dict）
- `RedisHotStore` — Phase 2+（Write-through, TTL=5min）
- `PostgresColdStore` — Phase 3+（Write-back, 异步写入）

### 4.3 降级状态机（`infrastructure/degradation.py`）

L0（全功能）→ L1（LLM 降级）→ L2（Agent→pipeline）→ L3（纯 BM25）→ L4（缓存兜底）→ L5（静态 FAQ）

每级配置：trigger 条件 + actions 动作 + auto_recovery 检查间隔 + recovery 条件。

---

## 五、pyproject.toml 关键配置

```toml
[project]
name = "spma"
version = "0.1.0"
requires-python = ">=3.13,<3.14"

[project.optional-dependencies]
# Phase 1: SQL Agent + 基础 API + 基础设施
core = [
    "langchain>=1.0",
    "langgraph>=1.0",
    "fastapi>=0.115",
    "uvicorn[standard]",
    "psycopg[binary,pool]",
    "pgvector",
    "redis[hiredis]",
    "apscheduler",
    "sqlglot",
    "presidio-analyzer",
    "presidio-anonymizer",
    "anthropic",
    "openai",
    "pydantic>=2",
    "pydantic-settings",
    "tenacity",
    "httpx",
    "pyyaml",
]

# Phase 2: Doc Agent + Synthesis
doc = ["spma[core]", "elasticsearch[async]", "sentence-transformers"]

# Phase 3: Supervisor Agent + Code Agent
code = ["spma[doc]", "tree-sitter"]

# 可观测性
observability = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp",
    "prometheus-client",
    "langfuse",
]

# 开发工具
dev = [
    "spma[all]",
    "pytest>=8",
    "pytest-asyncio",
    "pytest-cov",
    "testcontainers[postgres,redis]",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit",
]

all = ["spma[code]", "spma[observability]"]

[tool.ruff]
target-version = "py313"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.13"
strict = true
```

### 依赖分组说明

| extras | 安装命令 | 场景 |
|--------|---------|------|
| `spma[core]` | `uv sync` | Phase 1 开发——SQL Agent + API |
| `spma[doc]` | `uv sync --extra doc` | Phase 2 开发——Doc + Synthesis |
| `spma[code]` | `uv sync --extra code` | Phase 3 开发——Supervisor + Code |
| `spma[dev]` | `uv sync --extra dev` | 完整开发环境（含测试/类型检查） |
| `spma[all]` | `uv sync --extra all` | 生产部署（全量依赖） |

---

## 六、Docker 镜像与部署对应

| Dockerfile | 内容 | K8s Service |
|-----------|------|-------------|
| `Dockerfile.api` | FastAPI + 路由 + 中间件（轻量，无 Agent 逻辑） | `api-gateway`（可水平扩展） |
| `Dockerfile.agent` | LangGraph + 5 个 Agent 子图 + 检索基础设施 | `agent-service`（Sticky Session 或 Redis 共享状态） |
| `Dockerfile.ingestion` | APScheduler + 解析器 + 分块器 + 嵌入调用 | `ingestion-worker`（单实例或 leader election） |
| `Dockerfile.vllm` | vLLM + BGE-M3 + Qwen3-8B | `vllm-inference`（GPU node, HPA by num_requests_running） |

---

## 七、测试策略分层

| 层级 | 目录 | 运行频率 | 依赖 | 耗时目标 |
|------|------|---------|------|---------|
| Unit | `tests/unit/` | 每次 commit（pre-commit）+ CI | MockLLM, pytest fixtures | < 5s 全量 |
| Integration | `tests/integration/` | CI 每个 PR | testcontainers (pgvector, redis, es) | < 2min |
| Eval | `tests/eval/` | 每次 Agent 变更后手动触发 | 真实 LLM + 标注数据集 | ~10min |
| E2E | `tests/e2e/` | 发布前手动触发 | 完整测试环境 | ~30min |

CI 只跑 Unit + Integration。Eval 和 E2E 通过 `python -m tests.eval.run_eval` 独立触发。

---

## 八、关键设计原则

1. **契约优先（Contract-First）：** 每个模块的输入输出必须先定义（`models/` 共享类型）→ API 端点先有 Schema（`api/schemas/`）→ Agent 先有 State 定义（`agents/*/state.py`）
2. **确定性优先（Determinism-First）：** 纯规则校验（Action Guard、实体完备度、SQL Guard）走 `completeness.py`/`guard.py`；LLM 判断只在确定性条件不满足时触发（`prompts.py` 兜底）
3. **不静默失败（No Silent Failure）：** 降级/异常通过 `transparency.py`（Synthesis Agent）和 `degradation.py`（基础设施）显式标注
4. **可观测内建（Observability-Built-In）：** `observability/` 在 Phase 1 即引入——即使最初只用 `print`，Span 结构从一开始就保留
5. **Agent 不互调：** `agents/` 下各 Agent 只导入 `models/` 和 `base.py`，不交叉导入——对应架构设计文档"Agent 间不互调，网状调用推迟到 Phase 5+"
6. **渐进式引入：** 依赖按 Phase 通过 extras 分组（core → doc → code → all），避免一次性安装全部依赖。BM25 和状态存储通过 Protocol 抽象，Phase 升级时 Agent 代码零改动

---

## 九、与设计文档的对应关系

| 设计文档 | 对应代码模块 |
|---------|------------|
| SPMA-design-01: Supervisor Agent | `agents/supervisor/` |
| SPMA-design-02: Doc Agent | `agents/doc/` |
| SPMA-design-03: Code Agent | `agents/code/` |
| SPMA-design-04: SQL Agent | `agents/sql/` |
| SPMA-design-05: Data Ingestion | `ingestion/` |
| SPMA-design-06: Infrastructure | `infrastructure/` + `retrieval/` + `llm/` + `observability/` |
| SPMA-design-07: Agent Architecture | `agents/base.py`（收敛契约）+ `models/agent_state.py`（共享状态） |
| API-01~06: API Contracts | `api/` |
| SPMA-technology-selection | `pyproject.toml`（依赖）+ `deployments/`（部署） |

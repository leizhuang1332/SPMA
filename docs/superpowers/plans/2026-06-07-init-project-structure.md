# SPMA 项目目录结构初始化

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 初始化 SPMA 项目的完整目录结构骨架——创建所有 Python 包、配置文件、Docker 模板、测试框架，使用 uv 管理依赖，Python 3.13.*，不写业务代码。

**Architecture:** 按已批准的[项目目录结构设计](../specs/2026-06-07-project-structure-design.md)创建完整文件骨架。采用 src layout + 领域驱动组织。每个 py 文件包含模块 docstring 和必要的类型导入占位符。pyproject.toml 使用 uv 格式，依赖按 Phase 分 extras 组。

**Tech Stack:** uv, Python 3.13, FastAPI, LangGraph, Pydantic v2, pytest, Ruff, mypy, Docker, Helm

---

### Task 1: 根项目配置文件

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Modify: `README.md`（当前为空）

- [ ] **Step 1: 创建 pyproject.toml**

```toml
[project]
name = "spma"
version = "0.1.0"
description = "企业级多源RAG智能问答系统 - SPMA (Supervisor-Powered Multi-Agent)"
requires-python = ">=3.13,<3.14"
readme = "README.md"
license = {text = "MIT"}
authors = [
    {name = "SPMA Team"}
]
keywords = ["rag", "llm", "agent", "langgraph", "text-to-sql"]

dependencies = [
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

[project.optional-dependencies]
doc = [
    "elasticsearch[async]",
    "sentence-transformers",
]
code = [
    "tree-sitter",
]
observability = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp",
    "prometheus-client",
    "langfuse",
]
dev = [
    "pytest>=8",
    "pytest-asyncio",
    "pytest-cov",
    "testcontainers[postgres,redis]",
    "ruff>=0.8",
    "mypy>=1.13",
    "pre-commit",
]
all = [
    "spma[doc]",
    "spma[code]",
    "spma[observability]",
]

[project.scripts]
spma-api = "spma.api.app:main"
spma-ingest = "spma.ingestion.scheduler:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.uv]
dev-dependencies = []

[tool.ruff]
target-version = "py313"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP", "B", "SIM"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"

[tool.mypy]
python_version = "3.13"
strict = true
ignore_missing_imports = true

[tool.pytest.ini_options]
minversion = "8.0"
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "-v",
    "--strict-markers",
    "--tb=short",
]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
    "integration: marks tests as integration tests",
    "e2e: marks tests as end-to-end tests",
]
```

- [ ] **Step 2: 创建 .gitignore**

```gitignore
# Python
__pycache__/
*.py[cod]
*.pyo
*.egg-info/
dist/
build/
*.egg
.eggs/

# Virtual environments
.venv/
venv/
env/

# uv
uv.lock

# IDE
.vscode/
.idea/
*.swp
*.swo
*~

# OS
.DS_Store
Thumbs.db

# Environment variables
.env
.env.*
!.env.example

# Config secrets
config/secrets.yaml
*.key
*.pem

# Logs
logs/
*.log

# Test coverage
htmlcov/
.coverage
.coverage.*
coverage.xml
*.lcov

# MyPy
.mypy_cache/
.dmypy.json

# Ruff
.ruff_cache/

# Docker
.dockerignore

# Helm
deployments/helm/spma/charts/
deployments/helm/spma/Chart.lock

# Temporary files
*.tmp
*.bak
tmp/
```

- [ ] **Step 3: 更新 README.md**

```markdown
# SPMA — 企业级多源RAG智能问答系统

Supervisor-Powered Multi-Agent RAG 系统。

## 快速开始

### 环境要求
- Python 3.13+
- uv

### 安装

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv sync

# 安装开发依赖
uv sync --extra dev
```

### 运行

```bash
# 启动 API 服务
uv run spma-api

# 启动数据摄入
uv run spma-ingest
```

### 测试

```bash
uv run pytest
```

## 项目结构

参见 [项目目录结构设计](docs/superpowers/specs/2026-06-07-project-structure-design.md)

## 设计文档

- [全局概览](SPMA-design-00-global-overview.md)
- [5 Agent 架构](SPMA-design-07-agent-architecture.md)
- [技术选型](docs/SPMA-technology-selection.md)
```

- [ ] **Step 4: 验证文件内容**

```bash
cat pyproject.toml | head -5
cat .gitignore | head -5
cat README.md | head -5
```

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml .gitignore README.md
git commit -m "chore: add root project config files (pyproject.toml, .gitignore, README)"
```

---

### Task 2: uv 初始化依赖锁

- [ ] **Step 1: 确认 Python 版本**

```bash
python3.13 --version
```
Expected: `Python 3.13.x`

如果没有 Python 3.13：
```bash
uv python install 3.13
```

- [ ] **Step 2: 创建虚拟环境并安装 core 依赖**

```bash
cd /Users/Ray/TraeProjects/SPMA
uv venv --python 3.13
source .venv/bin/activate
uv sync
```
Expected: 下载并安装所有 `[project.dependencies]` 中的包，生成 `uv.lock`

- [ ] **Step 3: 验证安装**

```bash
uv run python -c "import langgraph; import fastapi; import pydantic; print('Core deps OK')"
```
Expected: `Core deps OK`

- [ ] **Step 4: 安装开发依赖**

```bash
uv sync --extra dev
uv run ruff --version
uv run mypy --version
```
Expected: 显示 Ruff 和 mypy 版本号

- [ ] **Step 5: 提交**

```bash
git add uv.lock
git commit -m "chore: initialize uv lock file with core + dev dependencies"
```

---

### Task 3: 创建 src/spma/ 包骨架 + 共享类型模型

**Files:**
- Create: `src/spma/__init__.py`
- Create: `src/spma/py.typed`
- Create: `src/spma/models/__init__.py`
- Create: `src/spma/models/agent_state.py`
- Create: `src/spma/models/worker_output.py`
- Create: `src/spma/models/entities.py`
- Create: `src/spma/models/classification.py`
- Create: `src/spma/models/convergence.py`
- Create: `src/spma/models/search_log.py`
- Create: `src/spma/models/common.py`

- [ ] **Step 1: 创建目录结构并编写包入口**

```bash
mkdir -p src/spma/models
```

```python
# src/spma/__init__.py
"""SPMA — 企业级多源RAG智能问答系统。

Supervisor-Powered Multi-Agent RAG 系统。
5 个独立 Agent（Supervisor, Doc, Code, SQL, Synthesis）
通过 LangGraph StateGraph + Send API 并行编排。
"""

__version__ = "0.1.0"
```

```bash
touch src/spma/py.typed
```

- [ ] **Step 2: 创建共享类型 — agent_state.py**

```python
# src/spma/models/agent_state.py
"""所有 Agent 共享的基础状态模型。

设计依据: SPMA-design-07 第五节 状态管理
"""

from typing import TypedDict


class AgentState(TypedDict, total=False):
    """所有 Agent 共享的基础状态字段。

    每个 Agent 在此基础上追加自己特有的字段。
    """

    round: int
    """当前轮次编号（从 1 开始）"""

    confidence: float
    """Agent 自评信心 (0-1)。确定性收敛 ≥ 0.85，LLM 判断充足 0.6-0.85"""

    results: list[dict]
    """本轮检索/执行结果列表"""

    token_used: int
    """已消耗的 LLM 调用次数"""

    assessment_history: list[str]
    """完备度判断历史，每条为 "sufficient" 或 "insufficient: <原因>" """

    llm_calls: int
    """本轮 LLM 调用次数"""

    latency_ms: int
    """本轮累计延迟（毫秒）"""

    has_exact_match: bool
    """是否命中精确匹配实体（req_ids / table_names / code_refs）"""

    convergence_reason: str
    """收敛原因。如 "deterministic", "llm_judged_sufficient", "max_rounds_reached" """
```

- [ ] **Step 3: 创建共享类型 — worker_output.py**

```python
# src/spma/models/worker_output.py
"""Worker Agent 输出契约。

设计依据: SPMA-design-07 第四节 Agent 交互协议
"""

from typing import TypedDict, Literal, NotRequired


class Citation(TypedDict):
    """引用元数据——每条检索结果的出处标注。"""

    source_type: Literal["prd", "code", "sql"]
    """来源数据源类型"""

    source_id: str
    """来源标识: doc_id:chunk_id, file_path:line, 或 table.column"""

    snippet: str
    """引用原文片段，≤200 字符"""

    relevance_score: NotRequired[float]
    """相关度分数 0-1"""

    metadata: NotRequired[dict]
    """额外元数据（版本、时间等）"""


class WorkerOutput(TypedDict, total=False):
    """Worker Agent → Supervisor 的标准输出。

    所有 Worker Agent 返回此结构，Supervisor 通过 LangGraph reducer 收集。
    """

    $schema: str
    """Schema 版本标识: "spma/worker-output/1.0" """

    task_id: str
    """派发任务 ID，格式: {query_id}:{agent_type}"""

    query_id: str
    """用户查询 ID (UUID)"""

    worker_type: Literal["doc", "code", "sql"]
    """Worker Agent 类型"""

    # —— 结果 ——
    result_count: int
    """返回结果数量（≥ 0）"""

    results: list[dict]
    """检索/SQL 执行结果列表"""

    citations: list[Citation]
    """引用元数据列表"""

    # —— 质量信号 ——
    confidence: float
    """Worker 自评信心 (0-1)"""

    has_exact_match: bool
    """是否命中精确匹配实体"""

    # —— 执行元数据 ——
    rounds_used: int
    """内部消耗的检索轮数"""

    convergence_reason: str
    """收敛原因"""

    total_llm_calls: int
    total_tokens: int
    latency_ms: int

    # —— 原始输入 ——
    original_query: str
    """原始检索 query"""

    # —— 降级 ——
    degradation: NotRequired[dict]
    """降级信息: {level: "L0"|"L1"|"L2"|"L3", reason, fallback_strategy, impact_description}"""

    # —— 跨源桥接实体 ——
    discovered_entities: NotRequired[dict]
    """Worker 在检索过程中发现的新实体: {req_ids, table_names, code_refs, module, person}"""


class WorkerDispatch(TypedDict, total=False):
    """Supervisor → Worker Agent 的任务派发。

    设计依据: API-02 第三节
    """

    task_id: str
    query_id: str
    agent_type: Literal["doc", "code", "sql"]
    original_query: str
    rewritten_query: str
    sub_queries: list[dict]
    entities: dict
    max_rounds: int
    timeout_ms: int
    token_budget: int
    previous_results: list[dict]
    hints_from_other_workers: dict
    feature_flags: dict
    model_override: str | None


class DegradationInfo(TypedDict):
    """降级信息"""

    level: Literal["L0", "L1", "L2", "L3", "L4", "L5"]
    reason: str
    fallback_strategy: str
    impact_description: str


class DiscoveredEntities(TypedDict, total=False):
    """Worker 检索过程中发现的新实体——用于跨源桥接"""

    req_ids: list[str]
    table_names: list[str]
    code_refs: list[str]
    module: str | None
    person: str | None
```

- [ ] **Step 4: 创建共享类型 — entities.py**

```python
# src/spma/models/entities.py
"""实体抽取与分发的数据模型。

设计依据: SPMA-design-01 第六节 实体抽取
"""

from typing import TypedDict, NotRequired


class ExtractedEntities(TypedDict, total=False):
    """Supervisor 从用户问题中抽取的结构化实体。

    12 种实体类型，对应三类数据源的检索需求。
    设计依据: SPMA-design-01 §6.1
    """

    # —— 跨源通用实体 ——
    module: str | None
    """功能模块: "用户登录"、"支付网关"、"审批流" """

    req_ids: list[str]
    """需求 ID: ["REQ-2024-0187", "PROJ-1234"] """

    time_range: str | None
    """时间范围: "上周"、"过去7天"、"2026年3月" """

    version: str | None
    """版本/分支: "v2.3"、"release/2026Q1" """

    # —— SQL 相关实体 ——
    table_names: list[str]
    """表名: ["users", "orders"] """

    column_names: list[str]
    """列名: ["status", "amount", "created_at"] """

    metrics: list[str]
    """指标/聚合: "新增用户数"、"订单总额"、"日活" """

    group_by: str | None
    """分组维度: "按天"、"按部门"、"按状态" """

    # —— 代码相关实体 ——
    code_refs: list[str]
    """文件/类/函数引用: ["oauth.py", "TokenService", "login_oauth"] """

    person: str | None
    """代码作者/提交人: "张三"、"@leizhuang1332" """

    # —— 文档相关实体 ——
    doc_types: list[str]
    """文档类型: ["PRD", "技术方案", "接口文档", "会议纪要"] """


class WorkerEntities(TypedDict, total=False):
    """Supervisor 抽取的实体子集——下发给单个 Worker 的视角。

    设计依据: API-02 §3.1
    """

    module: str | None
    req_ids: list[str]
    time_range: str | None
    version: str | None
    table_names: list[str]
    column_names: list[str]
    metrics: list[str]
    group_by: str | None
    code_refs: list[str]
    person: str | None
    doc_types: list[str]


class CompletenessLevel:
    """实体完备度等级（确定性代码评估，非 LLM）。

    设计依据: SPMA-design-01 §7.1
    """

    # 权重常量
    WEIGHT_EXACT_MATCH = 10  # req_ids, table_names, code_refs
    WEIGHT_SEMANTIC_ANCHOR = 5  # module
    WEIGHT_AUXILIARY = 2  # time_range, person, version
    WEIGHT_LIGHT = 1  # group_by, doc_types

    # 分级阈值
    RICH = 10  # ≥ 10: 至少一个精确匹配级实体
    PARTIAL = 5  # 5-9: 有功能锚点但无精确匹配
    BARE = 0  # < 5: 无任何有效检索锚点
```

- [ ] **Step 5: 创建共享类型 — classification.py**

```python
# src/spma/models/classification.py
"""意图分类数据模型。

设计依据: SPMA-design-01 第五节 意图分类器
"""

from typing import TypedDict, Literal

# 合法的数据源类型
SourceType = Literal["doc", "code", "sql"]

# 查询类型
QueryType = Literal["trace", "search", "data_query", "explain"]


class ClassificationResult(TypedDict):
    """Supervisor 意图分类输出。

    设计依据: SPMA-design-01 §5.1
    """

    sources: list[SourceType]
    """需要调度的 Worker Agent 列表"""

    is_cross_source: bool
    """是否为跨源查询（需要多个 Worker）"""

    entities: dict | None
    """抽取的实体，结构见 ExtractedEntities"""

    query_type: QueryType
    """查询类型: trace=溯源, search=搜索, data_query=数据查询, explain=解释"""


# 分类失败模式枚举（用于日志和监控）
CLASSIFICATION_FAILURE_MODES = {
    "ambiguous_query": "模糊查询——默认三源全查",
    "term_ambiguity": "术语歧义——规则优先",
    "short_no_context": "无上下文短查询——反问或继承上一轮",
    "cross_source_miss": "跨源漏判——规则补刀",
}
```

- [ ] **Step 6: 创建共享类型 — convergence.py**

```python
# src/spma/models/convergence.py
"""Agent 收敛契约的类型定义。

设计依据: SPMA-design-07 第二节 收敛契约
"""

from typing import TypedDict, Literal

# 收敛判定来源
ConvergenceSource = Literal[
    "deterministic",  # 确定性条件命中（不调 LLM）
    "llm_judged_sufficient",  # LLM 判断"信息充足"
    "max_rounds_reached",  # 达到最大轮数，强制收敛
    "timeout",  # Agent 超时
    "token_budget_exhausted",  # Token 预算耗尽
    "error",  # 异常导致收敛
]


class ConvergenceResult(TypedDict):
    """单轮完备度判断的结果。"""

    verdict: Literal["sufficient", "insufficient"]
    """判断结论"""

    source: ConvergenceSource
    """判定来源——确定性条件还是 LLM 判断"""

    reason: str
    """详细原因描述，如 "结果≥5 AND req_ids命中" 或 "LLM 判断缺少 XX 信息" """

    confidence: float
    """此判断的自评信心 0-1"""

    missing_info: list[str]
    """如果 insufficient，列出缺失的信息维度"""

    suggested_actions: list[str]
    """建议的补救措施: ["expand_clues", "relax_time_range", "retry_with_synonyms"]"""


class AssessmentVerdict(TypedDict):
    """完备度评估的简单二值输出——用于确定性收敛路径。"""

    sufficient: bool
    reason: str
```

- [ ] **Step 7: 创建共享类型 — search_log.py**

```python
# src/spma/models/search_log.py
"""检索日志数据结构。

设计依据: SPMA-design-02 §1.5.3 埋点日志结构
"""

from typing import TypedDict, NotRequired


class SearchLogEntry(TypedDict, total=False):
    """所有 Worker 共享的检索日志基类。

    写入 Kafka topic: spma.search.logs 或 ClickHouse。
    """

    $schema: str  # "spma/search_log/1.0"
    log_id: str  # UUID
    timestamp: str  # ISO 8601

    # —— Worker 标识 ——
    worker_type: str  # "doc" | "code" | "sql"
    worker_version: str  # 语义版本

    # —— 输入快照 ——
    query_id: str
    query_text: str
    query_type: str  # "precise" | "semantic" | "hybrid" | "exact_refs" | ...
    trigger: str  # "supervisor_dispatch" | "supervisor_reschedule"
    entities: dict  # 注入的实体

    # —— Agent 循环 ——
    agent_rounds: int
    convergence_reason: str

    # —— 延迟 ——
    latency_ms: int

    # —— 用户反馈（异步填充） ——
    feedback: NotRequired[dict]
```

- [ ] **Step 8: 创建共享类型 — common.py**

```python
# src/spma/models/common.py
"""跨模块共享的通用类型定义。"""

from typing import TypedDict, NotRequired


class RequestMetadata(TypedDict):
    """请求元数据——贯穿整个查询生命周期。"""

    request_id: str
    """UUID 格式的请求 ID"""

    timestamp: str
    """ISO 8601 时间戳 (UTC)"""

    client_version: NotRequired[str]
    """客户端版本: "spma-web/1.2.0" """

    session_id: NotRequired[str]
    """会话 ID（多轮对话）"""

    user_id: str
    """用户标识"""


class AgentTrace(TypedDict, total=False):
    """一次查询中所有 Agent 的执行轨迹摘要。"""

    supervisor_rounds: int
    doc_rounds: int
    code_rounds: int
    sql_rounds: int
    synthesis_rounds: int
    total_llm_calls: int
    total_tokens: int
    estimated_cost_usd: float
    degradation_level: str  # "L0" - "L5"
    convergence_reason: str


class DataFreshness(TypedDict, total=False):
    """各数据源的知识新鲜度时间戳。"""

    doc_updated_at: str
    code_indexed_at: str
    sql_schema_refreshed_at: str
```

- [ ] **Step 9: 创建 models/__init__.py**

```python
# src/spma/models/__init__.py
"""共享类型定义模块。

SPMA 所有 Agent 和 API 层的类型契约集中于此。
包括:
- Agent 状态基类 (AgentState)
- Worker 输出契约 (WorkerOutput, Citation)
- 实体抽取模型 (ExtractedEntities, WorkerEntities)
- 意图分类模型 (ClassificationResult)
- 收敛判断模型 (ConvergenceResult)
- 检索日志模型 (SearchLogEntry)
- 通用类型 (RequestMetadata, AgentTrace, DataFreshness)
"""

from spma.models.agent_state import AgentState
from spma.models.classification import ClassificationResult, QueryType, SourceType
from spma.models.common import AgentTrace, DataFreshness, RequestMetadata
from spma.models.convergence import (
    AssessmentVerdict,
    ConvergenceResult,
    ConvergenceSource,
)
from spma.models.entities import (
    CompletenessLevel,
    ExtractedEntities,
    WorkerEntities,
)
from spma.models.search_log import SearchLogEntry
from spma.models.worker_output import (
    Citation,
    DegradationInfo,
    DiscoveredEntities,
    WorkerDispatch,
    WorkerOutput,
)

__all__ = [
    # agent_state
    "AgentState",
    # classification
    "ClassificationResult",
    "QueryType",
    "SourceType",
    # common
    "AgentTrace",
    "DataFreshness",
    "RequestMetadata",
    # convergence
    "ConvergenceResult",
    "ConvergenceSource",
    "AssessmentVerdict",
    # entities
    "ExtractedEntities",
    "WorkerEntities",
    "CompletenessLevel",
    # search_log
    "SearchLogEntry",
    # worker_output
    "WorkerOutput",
    "WorkerDispatch",
    "Citation",
    "DegradationInfo",
    "DiscoveredEntities",
]
```

- [ ] **Step 10: 提交**

```bash
git add src/
git commit -m "feat: create package skeleton and shared type models"
```

---

### Task 4: 创建 Agent 基类 + Supervisor Agent 骨架

**Files:**
- Create: `src/spma/agents/__init__.py`
- Create: `src/spma/agents/base.py`
- Create: `src/spma/agents/supervisor/__init__.py`
- Create: `src/spma/agents/supervisor/graph.py`
- Create: `src/spma/agents/supervisor/state.py`
- Create: `src/spma/agents/supervisor/classifier.py`
- Create: `src/spma/agents/supervisor/entity_extractor.py`
- Create: `src/spma/agents/supervisor/completeness.py`
- Create: `src/spma/agents/supervisor/query_rewriter.py`
- Create: `src/spma/agents/supervisor/quality.py`
- Create: `src/spma/agents/supervisor/dispatcher.py`
- Create: `src/spma/agents/supervisor/prompts.py`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p src/spma/agents/supervisor
```

- [ ] **Step 2: 创建 agents/__init__.py**

```python
# src/spma/agents/__init__.py
"""SPMA Agent 模块。

5 个独立 Agent，每个作为 LangGraph 子图运行:
- Supervisor Agent: 编排中枢——意图分类、实体抽取、查询改写、多轮编排循环
- Doc Agent: PRD 文档检索——BM25+向量混合检索→完备度判断→线索扩展重搜
- Code Agent: 代码检索——ripgrep 实时搜索→完备度判断→调用链展开重搜
- SQL Agent: Text-to-SQL 执行——Schema RAG→LLM SQL生成→Guard→执行→语义验证
- Synthesis Agent: 审计融合——RRF融合→LLM生成初稿→引用完整性/跨源一致性/覆盖度检查

设计依据: SPMA-design-07 第一节 架构概述
"""
```

- [ ] **Step 3: 创建 agents/base.py**

```python
# src/spma/agents/base.py
"""Agent 基类——所有 Agent 共享的基础设施方法。

设计依据: SPMA-design-07 第六节 Agent 基础设施
"""

from spma.models.agent_state import AgentState


class BaseAgent:
    """所有 Agent 的基类，提供共享的收敛判断、预算管理和检查点方法。

    每个 Agent 通过组合（mixin）方式使用这些方法，而非继承。
    """

    @staticmethod
    def check_convergence(state: AgentState) -> tuple[bool, str]:
        """检查当前 Agent 是否满足收敛条件。

        确定性条件优先（代码规则），LLM 判断兜底。

        Args:
            state: 当前 Agent 状态

        Returns:
            (是否收敛, 收敛原因)
        """
        raise NotImplementedError("子类必须实现 check_convergence")

    @staticmethod
    def consume_budget(state: AgentState, tokens: int) -> bool:
        """从 Token 预算中扣减，返回是否有剩余额度。

        每次 LLM 调用前必须检查。超限抛出 TokenBudgetExhausted。

        Args:
            state: 当前 Agent 状态
            tokens: 本次消耗的 LLM 调用次数

        Returns:
            True 如果还有剩余预算
        """
        raise NotImplementedError("子类必须实现 consume_budget")

    @staticmethod
    def save_checkpoint(state: AgentState) -> None:
        """将 Agent 状态写入 LangGraph Checkpointer。

        由 LangGraph 自动管理调用时机，每轮状态变更后触发。

        Args:
            state: 当前 Agent 状态
        """
        raise NotImplementedError("子类必须实现 save_checkpoint")
```

- [ ] **Step 4: 创建 Supervisor Agent 的 10 个模块文件**

每个文件包含模块 docstring 和必要的类型导入占位符：

```python
# src/spma/agents/supervisor/__init__.py
"""Supervisor Agent — 编排中枢。

职责: 理解用户意图 → 抽取关键实体 → 改写查询 → Send API 并行派发
     → 收集 Worker 结果 → 质量评估 → 收敛/重调度。

收敛契约: ≤5轮, 超时5s
设计依据: SPMA-design-01
"""

# src/spma/agents/supervisor/state.py
"""Supervisor Agent 专属状态定义。

设计依据: SPMA-design-01 §1 Agent状态数据模型
"""

from spma.models.agent_state import AgentState
from spma.models.classification import ClassificationResult
from spma.models.entities import ExtractedEntities
from spma.models.worker_output import WorkerOutput


class SupervisorState(AgentState, total=False):
    """Supervisor Agent 专属状态字段。"""

    original_query: str
    """用户原始问题"""

    classification: ClassificationResult
    """意图分类结果"""

    entities: ExtractedEntities
    """抽取的实体"""

    rewritten_queries: dict[str, str]
    """改写后的查询 keyed by worker_type: {"doc": "...", "code": "...", "sql": "..."}"""

    worker_outputs: list[WorkerOutput]
    """本轮 Worker 返回结果（LangGraph reducer: operator.add）"""

    quality_scores: dict[str, float]
    """每个 Worker 的质量评分: {"doc": 0.85, "code": 0.78, "sql": 0.82}"""

    reschedule_count: int
    """已重调度次数（max 2）"""

    final_results: list[dict]
    """最终收集的最佳结果"""
```

```python
# src/spma/agents/supervisor/graph.py
"""Supervisor Agent 的 LangGraph StateGraph 定义。

构建模式:
  分类+抽取(Round 1) → Send API 并行派发 → fan-in 收集
  → 质量评估 → 评分≥0.6 收敛 / <0.6 + 重调度<2 → 调整参数重派

设计依据: SPMA-design-01 §1 编排循环总览
"""

# src/spma/agents/supervisor/classifier.py
"""意图分类器——LLM结构化分类 + 规则兜底两层架构。

设计依据: SPMA-design-01 第五节 意图分类器设计
"""

from spma.models.classification import ClassificationResult


async def classify_intent(user_query: str, context: dict) -> ClassificationResult:
    """LLM 意图分类（第一层）——确定需要哪些 Worker Agent。

    Args:
        user_query: 用户原始问题
        context: 对话上下文（session_id, 上轮分类结果）

    Returns:
        ClassificationResult: {sources, is_cross_source, entities, query_type}
    """
    raise NotImplementedError


def rule_based_classification(user_query: str) -> ClassificationResult:
    """纯规则兜底（第二层）——LLM 不可用时使用。

    正则匹配 + 关键词词典，准确率约 85%。

    Args:
        user_query: 用户原始问题

    Returns:
        ClassificationResult
    """
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/entity_extractor.py
"""实体抽取——与意图分类共享 LLM 调用，一次请求同时输出。

设计依据: SPMA-design-01 第六节 实体抽取设计
"""

from spma.models.entities import ExtractedEntities


async def extract_entities(
    user_query: str, classification: dict
) -> ExtractedEntities:
    """从用户问题中抽取 12 种结构化实体。

    Args:
        user_query: 用户原始问题
        classification: 意图分类结果（用于上下文）

    Returns:
        ExtractedEntities: 12 种实体类型的结构化抽取结果
    """
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/completeness.py
"""实体完备度评估——确定性代码（不调用 LLM）。

设计依据: SPMA-design-01 第七节 实体完备度评估
"""

from spma.models.entities import CompletenessLevel, ExtractedEntities


def evaluate_completeness(entities: ExtractedEntities) -> tuple[str, int, str]:
    """评估抽取实体的完备度——纯 Python 函数，不调 LLM。

    信息增益加权打分:
    - req_ids/table_names/code_refs → 10 分（精确匹配级）
    - module → 5 分（语义锚点级）
    - time_range/person/version → 2 分（辅助过滤级）
    - group_by/doc_types → 1 分（轻度过滤级）

    Args:
        entities: 抽取的实体

    Returns:
        (level, score, strategy):
        - "rich" (≥10): 精确检索，跳过语义搜索
        - "partial" (5-9): 混合检索（元数据过滤 + 语义搜索）
        - "bare" (<5): 纯语义搜索兜底或反问用户
    """
    raise NotImplementedError


def route_by_completeness(
    level: str, user_query: str, context: dict
) -> dict:
    """根据完备度等级选择处置策略。

    Args:
        level: "rich" | "partial" | "bare"
        user_query: 用户原始问题
        context: 对话上下文

    Returns:
        调度指令: {strategy, query, sources, note}
    """
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/query_rewriter.py
"""查询改写流水线——6种可插拔改写方案。

设计依据: SPMA-design-01 第八节 查询改写设计
"""


async def normalize_query(user_query: str) -> str:
    """方案1: 标准化——同义词映射表替换。始终开启，~1ms。"""
    raise NotImplementedError


async def expand_query(user_query: str) -> list[str]:
    """方案2: 扩展——LLM生成3-5个相关关键词。始终开启，~300ms。"""
    raise NotImplementedError


async def decompose_query(user_query: str) -> list[dict]:
    """方案3: 分解——跨源查询时拆分为独立子查询。条件触发，~500ms。"""
    raise NotImplementedError


async def hyde_generate(user_query: str) -> str:
    """方案4: HyDE——LLM生成假设性文档用于向量检索。条件触发，~1500ms。

    触发条件（三者同时满足）:
    1. query ≤ 30字
    2. completeness 为 partial 或 bare
    3. 目标为 Doc Agent
    """
    raise NotImplementedError


async def step_back_rewrite(user_query: str) -> str:
    """方案5: 退一步改写——具体问题→更广泛的背景问题。Phase 3+，~2000ms。"""
    raise NotImplementedError


async def context_aware_rewrite(user_query: str, history: list) -> str:
    """方案6: 上下文感知改写——多轮对话中指代词消解。Phase 3+。"""
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/quality.py
"""Supervisor 质量函数——Worker 输出的三维评分。

设计依据: SPMA-design-01 §2 质量函数, SPMA-design-07 §3 质量函数
"""

from spma.models.worker_output import WorkerOutput


def evaluate_worker_quality(
    output: WorkerOutput, query_type: str
) -> float:
    """对单个 Worker 输出做三维加权评分。

    维度:
    1. 结果数量 (0-0.3): 0条→0.0, <3条→0.1, <10条→0.2, ≥10条→0.3
    2. Worker 自评置信度 (0-0.3): output.confidence × 0.3
    3. 精确匹配命中 (0-0.4): req_ids/table_names/code_refs命中→0.4, 否则0.0

    权重矩阵按 query_type 动态调整:
    - data_query: count=0.3, confidence=0.3, exact_match=0.4
    - search: count=0.4, confidence=0.4, exact_match=0.2
    - trace: count=0.2, confidence=0.3, exact_match=0.5

    Args:
        output: Worker 返回的 WorkerOutput
        query_type: 查询类型 (data_query/search/trace)

    Returns:
        质量评分 0-1
    """
    raise NotImplementedError


def should_reschedule(
    quality_scores: dict[str, float], reschedule_count: int
) -> bool:
    """判断是否需要重调度。

    Args:
        quality_scores: 各 Worker 的质量评分
        reschedule_count: 已重调度次数

    Returns:
        True 如果有 Worker 评分 < 0.6 且重调度 < 2 次
    """
    raise NotImplementedError


def adjust_params(
    quality_scores: dict[str, float],
    worker_outputs: list[WorkerOutput],
    failed_workers: list[str],
) -> dict:
    """重调度时调整检索参数——从成功 Worker 结果中提取桥接实体。

    Args:
        quality_scores: 各 Worker 评分
        worker_outputs: 所有 Worker 的输出
        failed_workers: 评分 < 0.6 的 Worker 类型列表

    Returns:
        调整后的检索参数: {worker_type: {new_query, relaxed_filters, ...}}
    """
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/dispatcher.py
"""Supervisor 的 Send API 并行派发逻辑。

设计依据: API-02 第三节 Supervisor→Worker 派发协议
"""

from spma.models.worker_output import WorkerDispatch


def build_dispatches(
    state: "SupervisorState",
) -> list[dict]:
    """根据分类结果构造 Send API 的并行派发列表。

    为每个需要的 Agent 类型创建一个 WorkerDispatch，
    注入实体、收敛约束、上下文和 feature flags。

    Args:
        state: Supervisor 当前状态

    Returns:
        LangGraph Send 对象列表，每个对应一个 Worker 子图
    """
    raise NotImplementedError


def collect_worker_outputs(
    worker_outputs: list["WorkerOutput"],
) -> dict[str, "WorkerOutput"]:
    """LangGraph reducer: 收集并索引 Worker 返回结果。

    Args:
        worker_outputs: fan-in 收集的 WorkerOutput 列表

    Returns:
        {worker_type: WorkerOutput} 索引
    """
    raise NotImplementedError
```

```python
# src/spma/agents/supervisor/prompts.py
"""Supervisor Agent 的 LLM Prompt 模板。

设计依据: SPMA-design-01 §5.1 分类 Prompt, §6.1 抽取 Prompt
"""

CLASSIFICATION_PROMPT = """你是一个查询路由器。分析用户问题，输出 JSON。

数据源定义:
- doc: PRD 文档、产品需求、功能规格、需求变更
- code: 代码实现、函数、类、文件路径、bug、架构
- sql: 业务数据、统计、报表、指标查询

分类规则:
- 含需求ID [REQ-XXXXX] → 至少包含 doc
- 含表名/列名/数据/统计/多少 → 至少包含 sql
- 含文件路径/函数名/代码/类名/bug → 至少包含 code
- "X影响Y"、"X对应哪个Z" → 跨源查询，标记 is_cross_source=true
- 模糊查询无法判断 → 默认三源全查，标记 query_type="search"

示例:
"上周用户登录改了什么需求？" → {{"sources": ["doc"], "is_cross_source": false, ...}}
"REQ-187 改了哪些代码和表？" → {{"sources": ["doc","code","sql"], "is_cross_source": true, ...}}
"过去7天新增了多少用户？" → {{"sources": ["sql"], "is_cross_source": false, ...}}
"oauth.py 的 token_refresh 是谁写的？" → {{"sources": ["code"], "is_cross_source": false, ...}}
"用户登录怎么做的？" → {{"sources": ["doc","code","sql"], "is_cross_source": true, "query_type": "search", ...}}

用户问题: {user_query}
"""

ENTITY_EXTRACTION_PROMPT = """从用户问题中抽取以下实体。找不到的字段设为 null 或空列表。

实体说明:
- module: 问题涉及的功能模块或业务领域
- req_ids: 需求编号（格式 REQ-XXXXX 或 PROJ-XXXXX）
- time_range: 时间范围表述。保留原文，如"上周"、"过去7天"、"2026年Q1"
- version: 版本号或分支名
- table_names: 提到的数据库表名（英文或中文表名）
- column_names: 提到的列名/字段名
- metrics: 要查询的指标，如"新增用户数"、"订单总额"
- group_by: 分组维度，"按天"→daily，"按部门"→department
- code_refs: 提到的文件名、类名、函数名
- person: 提到的人名（代码作者/提交人）
- doc_types: 提到的文档类型，如"PRD"、"技术方案"、"接口文档"

用户问题: {user_query}
"""

QUERY_EXPANSION_PROMPT = """为以下用户查询生成 3-5 个相关的搜索关键词或术语（仅输出关键词列表，用逗号分隔）。
不要改变查询的本意，只是为了提高搜索召回率而补充同义词和相关术语。

查询: {query}

关键词:"""

QUERY_DECOMPOSE_PROMPT = """将以下复杂查询分解为 2-4 个独立的子查询，每个子查询针对一个特定的数据源或子问题。

数据源:
- doc: PRD文档、产品需求
- code: 代码实现
- sql: 数据库查询/数据统计

查询: {query}

以 JSON 输出: [{"query": "子查询1", "target": "doc"}, ...]
"""
```

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/
git commit -m "feat: create Agent base class and Supervisor Agent skeleton"
```

---

### Task 5: 创建 Doc Agent + Code Agent 骨架

**Files:**
- Create: `src/spma/agents/doc/__init__.py`
- Create: `src/spma/agents/doc/graph.py`
- Create: `src/spma/agents/doc/state.py`
- Create: `src/spma/agents/doc/retriever.py`
- Create: `src/spma/agents/doc/completeness.py`
- Create: `src/spma/agents/doc/clue_expander.py`
- Create: `src/spma/agents/doc/prompts.py`
- Create: `src/spma/agents/code/__init__.py`
- Create: `src/spma/agents/code/graph.py`
- Create: `src/spma/agents/code/state.py`
- Create: `src/spma/agents/code/searcher.py`
- Create: `src/spma/agents/code/term_builder.py`
- Create: `src/spma/agents/code/router.py`
- Create: `src/spma/agents/code/ast_expander.py`
- Create: `src/spma/agents/code/completeness.py`
- Create: `src/spma/agents/code/prompts.py`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p src/spma/agents/doc src/spma/agents/code
```

- [ ] **Step 2: 创建 Doc Agent 的 7 个模块文件**

由于文件内容被下一行相同模块名覆盖（doc和code各文件结构类似），此处统一生成。每个文件遵循 `"""模块docstring\n\n设计依据: SPMA-design-0X §X.X\n"""` 格式：

```python
# src/spma/agents/doc/__init__.py
"""Doc Agent — PRD文档检索Agent。

检索Agent。BM25+向量混合检索 → 完备度判断 → 不够则线索扩展重搜 → 够了返回结果。

收敛契约: ≤3轮, 超时2s
设计依据: SPMA-design-02
"""

# src/spma/agents/doc/state.py
"""Doc Agent 专属状态定义。

设计依据: SPMA-design-02 Agent状态数据模型
"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class BM25Hit(dict):
    """BM25 检索命中。doc_id, chunk_id, rank, score, snippet, metadata"""
    pass


class VectorHit(dict):
    """向量检索命中。doc_id, chunk_id, rank, score, snippet, metadata"""
    pass


class FusedResult(dict):
    """RRF 融合后结果。doc_id, chunk_id, rrf_score, bm25_rank, vector_rank, snippet, metadata"""
    pass


class DocAgentState(AgentState, total=False):
    """Doc Agent 专属状态字段。

    设计依据: API-03 §2.2
    """

    query: str
    """本轮检索 query（可能被改写）"""

    original_query: str
    """用户原始问题"""

    entities: WorkerEntities
    """Supervisor 下发的实体"""

    action: str
    """检索动作: "bm25_vector_search" | "metadata_filter" | "expand_clues" """

    bm25_candidates: list[BM25Hit]
    """BM25 检索结果 (Top-20)"""

    vector_candidates: list[VectorHit]
    """向量检索结果 (Top-20)"""

    fused_results: list[FusedResult]
    """RRF 融合后结果 (Top-10)"""

    weight_mode: str
    """权重模式: "precise" | "semantic" | "hybrid" """

    assessment: str
    """完备度: "sufficient" | "insufficient: missing X" """

    max_rounds: int
    timeout_ms: int
    token_budget: int
```

```python
# src/spma/agents/doc/graph.py
"""Doc Agent 的 LangGraph StateGraph 定义。

节点: search(混合检索) → assess(完备度判断)
条件边: 不够 → 线索扩展 → 回到search / 够了 → END

设计依据: SPMA-design-02 Agent循环图
"""

# src/spma/agents/doc/retriever.py
"""Doc Agent 混合检索——BM25 + BGE-M3 向量检索 + RRF 融合。

分层权重: precise(BM25主导) / semantic(向量主导) / hybrid(等权)

设计依据: SPMA-design-02 §1 检索策略
"""

# src/spma/agents/doc/completeness.py
"""Doc Agent 完备度判断。

确定性条件: 结果≥5条 AND req_ids命中 → 自动收敛（不调LLM）
LLM兜底: 确定性条件不满足 → Haiku判断是否充足

设计依据: SPMA-design-02 收敛契约
"""

# src/spma/agents/doc/clue_expander.py
"""Doc Agent 线索扩展——从检索结果中提取新关键词→放宽过滤→重新检索。

设计依据: SPMA-design-02 §1.4 线索扩展策略
"""

# src/spma/agents/doc/prompts.py
"""Doc Agent 的 LLM Prompt 模板——完备度判断 Prompt。

设计依据: SPMA-design-02 完备度判断
"""

COMPLETENESS_PROMPT = """判断以下 PRD 文档检索结果是否足以回答用户问题。

用户问题: {query}
检索结果: {results}

判断标准:
1. 是否找到了与问题直接相关的 PRD 片段？
2. 是否覆盖了问题的所有方面？
3. 如果有需求 ID，是否精确匹配到了对应的 PRD 文档？

输出 JSON: {"sufficient": true/false, "confidence": 0.0-1.0, "missing": ["缺失的方面"], "reasoning": "..."}
"""
```

- [ ] **Step 3: 创建 Code Agent 的 9 个模块文件**

```python
# src/spma/agents/code/__init__.py
"""Code Agent — 代码检索Agent。

检索Agent。ripgrep实时搜索 → 完备度判断 → 不够则调用链展开重搜 → 够了返回结果。

收敛契约: ≤3轮, 超时2s
设计依据: SPMA-design-03
"""

# src/spma/agents/code/state.py
"""Code Agent 专属状态定义。

设计依据: API-03 §3.2
"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class SearchTermSet(dict):
    """搜索词集合——搜索词构造管线的产出。
    exact_terms, fuzzy_terms, tag_terms
    """
    pass


class RipgrepHit(dict):
    """ripgrep 搜索结果。
    repo, file_path, line_number, match_text, match_type, confidence
    """
    pass


class ExpandedFile(dict):
    """AST 调用图扩展结果。
    repo, file_path, file_content, imports, calls, called_by, relation_to_seed, depth
    """
    pass


class CodeAgentState(AgentState, total=False):
    """Code Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities
    search_terms: SearchTermSet
    candidate_repos: list[str]
    route_method: str
    route_confidence: str  # "HIGH" | "MEDIUM" | "LOW"
    ripgrep_results: list[RipgrepHit]
    expanded_context: list[ExpandedFile]
    assessment: str
    call_depth: int
    new_files_this_round: int
    fallback_layer: int
    fallback_method: str
    max_rounds: int
    timeout_ms: int
    token_budget: int
```

```python
# src/spma/agents/code/graph.py
"""Code Agent 的 LangGraph StateGraph 定义。

节点: ripgrep搜索 → 完备度判断
条件边: 不够 → 调用链展开 → 回到ripgrep / 够了 → END

设计依据: SPMA-design-03 Agent循环图
"""

# src/spma/agents/code/searcher.py
"""Code Agent 搜索器——ripgrep + glob + read_file 实时文件系统搜索（零索引）。

设计依据: SPMA-design-03 检索策略
"""

# src/spma/agents/code/term_builder.py
"""搜索词构造管线——中文→英文代码标识符翻译 + 渐进式回退。

5层回退: 精确匹配 → 词干拆分 → 扩展仓库 → 模糊匹配 → LLM重试

设计依据: SPMA-design-03 搜索词构造
"""

# src/spma/agents/code/router.py
"""文件路径路由——通过 file_path_cache 表将查询路由到候选仓库。

设计依据: SPMA-design-03 文件路径路由
"""

# src/spma/agents/code/ast_expander.py
"""TreeSitter AST 调用图展开——提取 caller/callee/import 关系。

设计依据: SPMA-design-03 调用图扩展
"""

# src/spma/agents/code/completeness.py
"""Code Agent 完备度判断。

确定性条件: 结果≥3条 AND (调用链深度≤2 OR 第3轮无新增文件) → 自动收敛
LLM兜底: 确定性条件不满足 → Haiku判断是否充足

设计依据: SPMA-design-03 收敛契约
"""

# src/spma/agents/code/prompts.py
"""Code Agent 的 LLM Prompt 模板——完备度判断 + 搜索词翻译 Prompt。"""

COMPLETENESS_PROMPT = """判断以下代码搜索结果是否足以回答用户问题。

用户问题: {query}
代码搜索结果: {results}
调用链展开结果: {expanded_context}

判断标准:
1. 是否找到了与问题直接相关的代码文件？
2. 调用链展开是否发现了关键的 caller/callee/import 关系？
3. 如果有代码引用（文件/函数/类名），是否精确匹配？

输出 JSON: {"sufficient": true/false, "confidence": 0.0-1.0, "missing": ["缺失的方面"], "reasoning": "..."}
"""

CODE_TERM_TRANSLATION_PROMPT = """将以下中文业务术语翻译为可能的英文代码标识符。
输出 JSON: ["english_term_1", "english_term_2", ...]

中文术语: {chinese_term}
"""
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/doc/ src/spma/agents/code/
git commit -m "feat: create Doc Agent and Code Agent skeletons"
```

---

### Task 6: 创建 SQL Agent + Synthesis Agent 骨架

**Files:**
- Create: `src/spma/agents/sql/__init__.py, graph.py, state.py, schema_rag.py, generator.py, guard.py, executor.py, verifier.py, quality.py, prompts.py`
- Create: `src/spma/agents/synthesis/__init__.py, graph.py, state.py, fusion.py, generator.py, auditor.py, transparency.py, prompts.py`

- [ ] **Step 1: 创建目录并编写 SQL Agent 骨架**

```bash
mkdir -p src/spma/agents/sql src/spma/agents/synthesis
```

SQL Agent 10 个文件 + Synthesis Agent 8 个文件。由于格式与前两个 Agent 一致，此处用循环方式批量创建：

```bash
# SQL Agent 文件列表
cat > /tmp/sql_files.txt << 'EOF'
__init__.py
graph.py
state.py
schema_rag.py
generator.py
guard.py
executor.py
verifier.py
quality.py
prompts.py
EOF

# Synthesis Agent 文件列表
cat > /tmp/synthesis_files.txt << 'EOF'
__init__.py
graph.py
state.py
fusion.py
generator.py
auditor.py
transparency.py
prompts.py
EOF
```

- [ ] **Step 2: 编写各文件的 docstring（SQL Agent）**

```python
# src/spma/agents/sql/__init__.py
"""SQL Agent — Text-to-SQL 执行Agent。

执行Agent。Schema RAG → LLM SQL生成 → SQL Guard 五层校验
→ 只读副本执行 → 语义验证 → 不够 → 携带错误反馈重新生成。

收敛契约: ≤5轮, 超时3s
设计依据: SPMA-design-04
"""

# src/spma/agents/sql/state.py
"""SQL Agent 专属状态定义。

设计依据: API-03 §4.2
"""

from spma.models.agent_state import AgentState
from spma.models.entities import WorkerEntities


class SchemaHit(dict):
    """Schema RAG 检索命中: table_name, ddl_snippet, column_comment,
    business_meaning, enum_values, business_rules, relevance_score"""
    pass


class GuardResult(dict):
    """SQL Guard 校验结果: passed, syntax_errors, forbidden_operations,
    table_existence_errors, performance_warnings, risk_level,
    requires_user_confirmation"""
    pass


class QueryResult(dict):
    """SQL 执行结果: columns, rows, row_count, execution_time_ms,
    replica_lag_ms, data_snapshot_at, sql_executed"""
    pass


class QualityReport(dict):
    """结果质量报告: issues(list), issue_count, confidence"""
    pass


class QualityIssue(dict):
    """质量问题: type, column, description, severity"""
    pass


class SQLAgentState(AgentState, total=False):
    """SQL Agent 专属状态字段。"""

    query: str
    original_query: str
    entities: WorkerEntities
    schema_search_results: list[SchemaHit]
    business_metadata: dict
    generated_sql: str
    guard_result: GuardResult
    guard_passed: bool
    execution_result: QueryResult
    execution_success: bool
    row_count: int
    semantic_check: str  # "passed" | "failed: reason"
    quality_report: QualityReport
    assessment: str
    sql_history: list[str]
    max_rounds: int
    timeout_ms: int
    token_budget: int
```

```python
# src/spma/agents/sql/graph.py
"""SQL Agent 的 LangGraph StateGraph 定义。

节点: generate(LLM SQL生成) → guard(SQL Guard) → execute(只读执行) → verify(语义验证)
条件边: guard失败→带错误回到generate / verify不通过→带异常回到generate / 通过→END

设计依据: SPMA-design-04 Agent循环图
"""

# src/spma/agents/sql/schema_rag.py
"""Schema RAG——检索相关表的 DDL + 列注释 + 业务元数据。

增强注入: 列的业务含义、枚举值映射、外键关系、常见查询

设计依据: SPMA-design-04 §3.1 业务元数据注入
"""

# src/spma/agents/sql/generator.py
"""LLM SQL 生成器——注入业务元数据 + few-shot 示例 + 上轮错误反馈。

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

# src/spma/agents/sql/guard.py
"""SQL Guard 五层校验——非协商安全项。

Layer 1: SQLGlot 语法校验
Layer 2: DDL/DML 拦截 (DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER)
Layer 3: 表/列存在性验证
Layer 4: 性能保护（缺失WHERE/笛卡尔积/缺失LIMIT）
Layer 5: 只读副本执行 + 超时控制

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

# src/spma/agents/sql/executor.py
"""只读副本 SQL 执行器——连接池 + 超时控制 + 数据新鲜度记录。

永远不在主库上执行。

设计依据: SPMA-design-04 §1 只读副本执行
"""

# src/spma/agents/sql/verifier.py
"""SQL 语义验证器——从"语法对"到"语义对"。

确定性条件: 执行成功 AND 行数∈[1,10000] → 自动收敛
LLM兜底: 统计异常/NULL比例/分布异常 → Haiku语义验证

设计依据: SPMA-design-04 §3.3 Agent循环语义验证增强
"""

# src/spma/agents/sql/quality.py
"""数据质量检测——执行后结果质量扫描。

检查: 空结果、NULL比例异常、数值列异常值、时间范围合理性

设计依据: SPMA-design-04 §4.2 数据质量问题
"""

# src/spma/agents/sql/prompts.py
"""SQL Agent 的 LLM Prompt 模板——SQL 生成 + 语义验证 Prompt。

设计依据: SPMA-design-04
"""

SQL_GENERATION_PROMPT = """根据 Schema 信息和业务元数据，将自然语言问题翻译为 SQL。

Schema 信息:
{schema_info}

业务元数据:
{business_metadata}

查询历史（含失败 SQL 和错误信息）:
{sql_history}

要求:
1. 只生成 SELECT 语句
2. 使用表名和列名的原始英文名称
3. 添加必要的 WHERE 条件（如软删除过滤、时间范围）
4. 聚合查询使用 GROUP BY
5. 输出格式: 仅 SQL，不要解释

用户问题: {query}
"""

SEMANTIC_VERIFY_PROMPT = """判断以下 SQL 执行结果是否在语义上正确回答了用户问题。

用户问题: {query}
执行的 SQL: {sql}
执行结果统计: {result_stats}

检查项目:
1. 返回的行数是否合理？
2. NULL 值比例是否异常？
3. 数值分布是否合理？
4. 结果是否在语义上回答了用户的问题？

输出 JSON: {"passed": true/false, "issues": ["问题1", ...], "confidence": 0.0-1.0}
"""
```

- [ ] **Step 3: 编写 Synthesis Agent 骨架**

```python
# src/spma/agents/synthesis/__init__.py
"""Synthesis Agent — 审计融合Agent。

审计Agent。RRF融合多Worker引用 → LLM生成初稿
→ 引用完整性检查 → 跨源一致性检查 → 问题覆盖度检查 → 不够 → 修正。

收敛契约: ≤2轮, 超时2s
设计依据: API-04
"""

# src/spma/agents/synthesis/state.py
"""Synthesis Agent 专属状态定义。

设计依据: API-04 §3
"""

from spma.models.agent_state import AgentState


class FusedCitation(dict):
    """RRF 融合后的引用: source_type, source_id, snippet, rrf_score,
    worker_confidence, source_rankings"""
    pass


class UnverifiedCitation(dict):
    """无法验证的引用: source_id, reason, impact"""
    pass


class CrossSourceContradiction(dict):
    """跨源矛盾: claim, source_a, source_a_claim, source_b, source_b_claim, resolution"""
    pass


class SynthesisAgentState(AgentState, total=False):
    """Synthesis Agent 专属状态字段。"""

    original_query: str
    worker_outputs: list  # List[WorkerOutput]
    draft_answer: str  # 初稿（Markdown）
    fused_citations: list[FusedCitation]
    rrf_params: dict  # {k, weights}
    citation_coverage: float  # 引用覆盖率 0-1
    unverified_citations: list[UnverifiedCitation]
    contradictions: list[CrossSourceContradiction]
    coverage_gaps: list[str]
    final_answer: str
    final_citations: list[dict]
    audit_trail: str
    max_rounds: int
    timeout_ms: int
    token_budget: int
```

```python
# src/spma/agents/synthesis/graph.py
"""Synthesis Agent 的 LangGraph StateGraph 定义。

节点: RRF融合 → LLM生成初稿 → 引用完整性检查
条件边: 不够 → 修正回到生成 / 够了 → END

设计依据: API-04 Synthesis Agent
"""

# src/spma/agents/synthesis/fusion.py
"""加权 RRF 融合算法——将多个 Worker 的引用结果合并排序。

公式: weighted_RRF(d) = Σ w_i / (k + rank_i(d))

设计依据: API-04 §6 RRF融合算法接口
"""

# src/spma/agents/synthesis/generator.py
"""LLM 初稿生成——结构化 Prompt（引用标注 + 跨源区分 + 不确定性标注）。

设计依据: API-04 §7.1 初稿生成Prompt
"""

# src/spma/agents/synthesis/auditor.py
"""Synthesis Auditor——引用完整性 + 跨源一致性 + 问题覆盖度检查。

设计依据: API-04 §7.2 自检Prompt
"""

# src/spma/agents/synthesis/transparency.py
"""不静默失败——降级策略、数据局限、跨源矛盾的透明标注。

设计依据: API-04 §8.2 透明度标注规则
"""

# src/spma/agents/synthesis/prompts.py
"""Synthesis Agent 的 LLM Prompt 模板。

设计依据: API-04 §7
"""

SYNTHESIS_PROMPT = """你是一个企业知识助手。根据以下检索结果，回答用户问题。

用户问题: {original_query}

检索结果:
{doc_results}
{code_results}
{sql_results}

要求:
1. 用 Markdown 格式组织回答，包含章节标题、列表、代码块
2. 每条陈述必须标注引用来源，格式: [源类型: 标识符]
3. 区分"确定的事实"和"推测的结论"，后者需明确标注
4. 如果跨源信息存在矛盾，显式标注矛盾点
5. 如果有未能回答的部分，在末尾列出
6. 使用中文回答
"""

AUDIT_PROMPT = """你是一个严谨的审计员。检查刚才生成的回答:

{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/Code/SQL 三源的信息有矛盾吗？
3. 覆盖度: 用户的原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{{
  "citation_coverage": 0.0-1.0,
  "unverified_citations": [{{"source_id": "...", "reason": "...", "impact": "low|medium|high"}}],
  "contradictions": [{{"claim": "...", "source_a": "...", "source_a_claim": "...", "source_b": "...", "source_b_claim": "..."}}],
  "coverage_gaps": ["..."],
  "verdict": "sufficient" | "insufficient: <原因>"
}}
"""
```

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/sql/ src/spma/agents/synthesis/
git commit -m "feat: create SQL Agent and Synthesis Agent skeletons"
```

---

### Task 7: 创建 API 层骨架

**Files:**
- Create: `src/spma/api/__init__.py`
- Create: `src/spma/api/app.py`
- Create: `src/spma/api/dependencies.py`
- Create: `src/spma/api/routes/__init__.py, query.py, session.py, feedback.py, health.py, agent_card.py, ingestion.py, admin.py`
- Create: `src/spma/api/middleware/__init__.py, auth.py, rate_limit.py, request_id.py, audit.py, cors.py`
- Create: `src/spma/api/schemas/__init__.py, query.py, session.py, feedback.py, health.py, ingestion.py, common.py`

- [ ] **Step 1: 创建目录**

```bash
mkdir -p src/spma/api/routes src/spma/api/middleware src/spma/api/schemas
```

- [ ] **Step 2: 创建 API 核心文件**

```python
# src/spma/api/__init__.py
"""SPMA REST API 层。

FastAPI 应用，对应 API-01 文档的 8 个端点。
包括: 查询(流式/非流式)、会话管理、反馈、健康检查、Agent Card、摄入管理、管理端点。

设计依据: API-01 外部 REST API
"""

# src/spma/api/app.py
"""FastAPI 应用工厂。

create_app() → 注册所有路由、中间件、生命周期事件。

设计依据: API-01 端点总览
"""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """创建并配置 FastAPI 应用实例。

    Returns:
        配置完成的 FastAPI app，包含所有路由和中间件
    """
    raise NotImplementedError


def main():
    """uvicorn 入口: uv run spma-api"""
    raise NotImplementedError
```

```python
# src/spma/api/dependencies.py
"""FastAPI 依赖注入。

通过 Depends() 注入: 状态存储、LLM 客户端、Feature Flag 服务、缓存等。
"""

# src/spma/api/routes/__init__.py
"""API 路由模块——对应 API-01 文档的 8 个端点。

- query: POST /api/v1/query + /query/stream (SSE)
- session: GET/DELETE /api/v1/session/{id}
- feedback: POST /api/v1/feedback
- health: GET /api/v1/health + /agent-card
- ingestion: POST /ingest/* + GET /ingest/status + GET /ingest/freshness
- admin: GET/PUT feature-flags + GET/POST degradation
"""
```

- [ ] **Step 3: 创建路由文件（8 个端点 + __init__.py）**

```python
# src/spma/api/routes/query.py
"""查询端点——POST /api/v1/query + POST /api/v1/query/stream (SSE 流式)。

设计依据: API-01 §2 核心端点, §3 流式端点
"""

# src/spma/api/routes/session.py
"""会话端点——GET + DELETE /api/v1/session/{session_id}。

设计依据: API-01 §4 会话管理
"""

# src/spma/api/routes/feedback.py
"""反馈端点——POST /api/v1/feedback。

设计依据: API-01 §5 用户反馈
"""

# src/spma/api/routes/health.py
"""健康检查——GET /api/v1/health + GET /api/v1/agent-card。

设计依据: API-01 §6 健康检查 + §7 Agent Card
"""

# src/spma/api/routes/agent_card.py
"""Agent Card——GET /api/v1/agent-card（A2A 风格服务发现）。

设计依据: API-01 §7 Agent Card
"""

# src/spma/api/routes/ingestion.py
"""摄入管理——POST /ingest/* + GET /ingest/status/* + GET /ingest/freshness。

设计依据: API-05 数据摄入 API
"""

# src/spma/api/routes/admin.py
"""管理端点——Feature Flags 管理 + 降级状态管理 + 配置热加载。

设计依据: API-06 §3 Feature Flags, §5 降级配置
"""
```

- [ ] **Step 4: 创建中间件文件**

```python
# src/spma/api/middleware/__init__.py
"""FastAPI 中间件——auth(JWT+API Key), rate_limit(滑动窗口),
request_id(X-Request-ID), audit(请求日志), cors
"""

# src/spma/api/middleware/auth.py
"""认证中间件——JWT 验证 + API Key 验证。

Authorization: Bearer <JWT_TOKEN>
X-API-Key: <API_KEY>

设计依据: API-00 §7.1 认证
"""

# src/spma/api/middleware/rate_limit.py
"""限流中间件——滑动窗口算法。

- 每用户: 30 req/min
- 每IP: 60 req/min
- 全局: 1000 req/min

设计依据: API-00 §7.3 限流
"""

# src/spma/api/middleware/request_id.py
"""请求 ID 中间件——X-Request-ID 注入 + 响应头回传。

设计依据: API-00 §5.1 请求元数据
"""

# src/spma/api/middleware/audit.py
"""审计中间件——请求/响应日志异步写入 PostgreSQL audit_logs 表。

设计依据: API-00 §6 审计日志结构
"""

# src/spma/api/middleware/cors.py
"""CORS 中间件——开发环境放宽，生产环境收紧。
"""
```

- [ ] **Step 5: 创建 Schema 文件**

```python
# src/spma/api/schemas/__init__.py
"""API 请求/响应 Pydantic 模型——对应 API 文档的 Schema 定义。"""

# src/spma/api/schemas/query.py
"""查询相关 Schema: QueryRequest, QueryResponse, StreamEvent, QueryContext, QueryHints。

设计依据: API-01 §2
"""

# src/spma/api/schemas/session.py
"""会话相关 Schema: SessionContext, TurnInfo。

设计依据: API-01 §4
"""

# src/spma/api/schemas/feedback.py
"""反馈相关 Schema: FeedbackRequest (rating, comment, tags)。

设计依据: API-01 §5
"""

# src/spma/api/schemas/health.py
"""健康检查 Schema: HealthResponse, ComponentStatus。

设计依据: API-01 §6
"""

# src/spma/api/schemas/ingestion.py
"""摄入管理 Schema: IngestionRequest, PipelineStatus。

设计依据: API-05 §2-6
"""

# src/spma/api/schemas/common.py
"""通用 Schema: ErrorResponse, Pagination, ResponseMetadata, 分页参数。

设计依据: API-00 §4 通用错误模型
"""
```

- [ ] **Step 6: 提交**

```bash
git add src/spma/api/
git commit -m "feat: create API layer skeleton (routes, middleware, schemas)"
```

---

### Task 8: 创建摄入管道 + 跨领域模块骨架

**Files:**
- `src/spma/ingestion/` — 6 模块 + parsers/ (5 文件) + chunkers/ (4 文件)
- `src/spma/retrieval/` — 8 模块
- `src/spma/llm/` — 4 模块 + prompts/ (4 文件)
- `src/spma/infrastructure/` — 8 模块
- `src/spma/config/` — 3 模块
- `src/spma/observability/` — 4 模块

- [ ] **Step 1: 创建所有目录**

```bash
mkdir -p src/spma/ingestion/parsers src/spma/ingestion/chunkers
mkdir -p src/spma/retrieval
mkdir -p src/spma/llm/prompts
mkdir -p src/spma/infrastructure
mkdir -p src/spma/config
mkdir -p src/spma/observability
```

- [ ] **Step 2: 批量创建各模块的 __init__.py 和骨架文件**

由于文件数量较多（~40 个模块文件），为每个编写完整 docstring：

**ingestion/ 模块（15 个文件）：**

```python
# src/spma/ingestion/__init__.py
"""数据摄入管道——三种异构数据源的离线/异步同步。

支持: PRD 文档(Confluence Webhook) + 代码仓库(Git Webhook) + SQL Schema(定时轮询)
新鲜度目标: 文档/代码 < 5min, Schema < 10min

设计依据: SPMA-design-05 数据摄入管道设计
"""

# src/spma/ingestion/scheduler.py
"""APScheduler 摄入调度——cron/webhook/interval 三种触发方式。

- doc: webhook(实时) + cron(每日02:00 UTC全量)
- code: webhook(push事件, 防抖10s)
- sql: interval(10min) + 手动触发

设计依据: SPMA-design-05 §4 摄入调度
"""

def main():
    """入口: uv run spma-ingest"""
    raise NotImplementedError
```

```python
# src/spma/ingestion/doc_pipeline.py
"""PRD 文档摄入主流程。

Confluence Webhook → Docling 解析 HTML → 递归语义分块
→ BGE-M3 嵌入 → PGVector + 元数据表 upsert

设计依据: SPMA-design-05 §1 PRD文档摄入管道
"""

# src/spma/ingestion/code_pipeline.py
"""代码仓库摄入主流程。

Git Webhook → git pull → git ls-files → upsert file_path_cache
→ TreeSitter 解析变更文件 AST → upsert code_metadata

注意: 不存储源代码——Code Agent 通过 read_file 实时读取。

设计依据: SPMA-design-05 §2 代码摄入管道
"""

# src/spma/ingestion/sql_pipeline.py
"""SQL Schema 摄入主流程。

information_schema 自省 → DDL + 列注释提取
→ 业务元数据注入（列注释/枚举值/外键/常见查询）
→ BGE-M3 嵌入 → PGVector

设计依据: SPMA-design-05 §3 SQL Schema摄入管道
"""

# src/spma/ingestion/synonym_map.py
"""同义词映射表管理——用户用语 → 系统内部名的标准化映射。

冷启动: information_schema + PRD标题 + git目录 + 人工补充 (~80-110条)
持续维护: 用户修正挖掘 + 检索落差分析 + LLM月度挖掘 + 衰变检查

设计依据: SPMA-design-01 §8.2 映射表维护
```

```python
# src/spma/ingestion/parsers/__init__.py
"""文档解析器集合——Docling(Confluence HTML)、Unstructured(PDF/DOCX)、TreeSitter(代码AST)。"""

# src/spma/ingestion/parsers/base.py
"""DocumentParser Protocol——所有解析器遵循的统一接口。

定义: parse(url) → ParsedDocument
"""

# src/spma/ingestion/parsers/docling_parser.py
"""Docling 解析器——Confluence/Wiki HTML 富文本。

保留: 标题层级、表格结构、列表嵌套、内联代码
"""

# src/spma/ingestion/parsers/unstructured_parser.py
"""Unstructured 解析器——通用文档格式（PDF, DOCX, Markdown）。

备选方案，当 Docling 无法处理时使用。
"""

# src/spma/ingestion/parsers/treesitter_parser.py
"""TreeSitter 解析器——代码 AST 提取调用图。

提取: calls, called_by, imports, function definitions
支持: Python, TypeScript, Java, Go, Rust
```

```python
# src/spma/ingestion/chunkers/__init__.py
"""分块策略集合——递归语义分块 + 代码文件级分块。"""

# src/spma/ingestion/chunkers/base.py
"""Chunker Protocol——所有分块器遵循的统一接口。

定义: chunk(document) → list[Chunk]
"""

# src/spma/ingestion/chunkers/semantic_chunker.py
"""递归语义分块器——按自然边界切割文档。

策略: 先按一级标题切 → 二级标题切 → 段落切 → 句子切
参数: ~500 tokens/块, 50-token overlap
分隔符优先级: \\n## > \\n### > \\n\\n > \\n > 。
"""

# src/spma/ingestion/chunkers/code_chunker.py
"""代码分块器——文件级粒度。

代码不需要分块——但 file_path_cache 需要文件级粒度。
"""
```

- [ ] **Step 3: 创建 retrieval/ 模块（8 个文件）**

```python
# src/spma/retrieval/__init__.py
"""跨 Agent 共享的检索基础设施。

包括: BGE-M3 嵌入服务、PGVector 向量存储、BM25 关键词检索、
     RRF 融合排序、混合检索编排、检索日志

设计依据: SPMA-design-02, SPMA-technology-selection
"""

# src/spma/retrieval/embeddings.py
"""BGE-M3 嵌入服务——vLLM 客户端。

模型: BGE-M3 (1024维, Dense+Sparse+ColBERT三合一)
部署: vLLM v0.6+ V1 Engine, PagedAttention + Prefix Caching

"""

# src/spma/retrieval/vector_store.py
"""PGVector 向量存储客户端。

索引: HNSW (m=16, ef_construction=200, ef_search=100)
距离: cosine
能力: 向量检索 + SQL JOIN 元数据过滤（同一事务内）

"""

# src/spma/retrieval/bm25.py
"""BM25 关键词检索。

Phase 1-2: PostgreSQL tsvector + zhparser 中文分词
Phase 3+: Elasticsearch ik_smart 中文分词 + kNN 向量搜索

"""

# src/spma/retrieval/bm25_interface.py
"""BM25 抽象接口（Protocol）。

通过 Protocol 解耦具体实现:
- PGtsvectorBM25: Phase 1-2 使用
- ElasticsearchBM25: Phase 3 切换时零 Agent 代码改动

"""

from typing import Protocol


class BM25Interface(Protocol):
    """BM25 检索引擎的抽象接口。"""

    async def search(self, query: str, top_k: int, filters: dict | None = None) -> list[dict]:
        """BM25 关键词搜索。

        Args:
            query: 搜索查询文本
            top_k: 返回结果数量
            filters: 元数据过滤条件（如 doc_type, time_range）

        Returns:
            BM25Hit 列表，按相关性降序
        """
        ...

    async def index(self, documents: list[dict]) -> None:
        """索引文档。

        Args:
            documents: 文档列表，每条含 {id, text, metadata}
        """
        ...

    async def delete(self, doc_ids: list[str]) -> None:
        """删除文档索引。

        Args:
            doc_ids: 要删除的文档 ID 列表
        """
        ...

    async def health_check(self) -> bool:
        """检查引擎是否可用。"""
        ...

# src/spma/retrieval/reranker.py
"""重排序——RRF 等权/加权融合 + BGE-Reranker v2 M3 Cross-encoder。

Phase 1-2: RRF 等权融合（k=60）
Phase 2: 按 query_type 分层权重 (precise/semantic/hybrid)
Phase 3: BGE-Reranker v2 M3 对 RRF Top-20 精排

设计依据: SPMA-design-02 §1.5 混合检索权重确定
"""

# src/spma/retrieval/hybrid_search.py
"""混合检索编排——BM25 + 向量并行检索 → RRF 融合 → 可选 Reranker。

编排流程:
1. 并行调用 BM25.search() 和 vector_store.search()
2. RRF 融合两个 Top-20 为 Top-10
3. (Phase 3+) BGE-Reranker 对 Top-20 精排
"""

# src/spma/retrieval/search_logger.py
"""检索日志记录——结构化写入 Kafka/ClickHouse。

记录: BM25 Top-20 + 向量 Top-20 + RRF 融合 Top-10 + Agent 循环信息
用户反馈异步回填，不阻塞检索主链路。

设计依据: SPMA-design-02 §1.5.3 埋点日志结构详情
"""
```

- [ ] **Step 4: 创建 llm/ 模块（8 个文件）**

```python
# src/spma/llm/__init__.py
"""LLM 抽象层——统一 Claude API + 本地 Qwen3-8B 的调用接口。

模型分层:
- 高速路径 (<500ms): Claude Haiku → 意图分类/实体抽取/完备度判断/语义验证
- 质量路径 (<2s): Claude Sonnet → 回答生成/SQL 生成/复杂推理
- 降级路径 (本地): Qwen3-8B(vLLM) → 全部 LLM 不可用时的兜底

设计依据: SPMA-technology-selection §3 LLM模型选型
"""

# src/spma/llm/clients.py
"""LLM 客户端——Haiku/Sonnet API + Qwen3-8B vLLM 本地。

统一接口: chat(messages, model, **kwargs) → str
动态模型选择: 运行时按 state 自动切换 Haiku/Sonnet
指数退避重试: tenacity, 429→重试3次, multiplier=0.5s, max_wait=2s
降级: 非 429 错误直接降级到 Qwen3-8B
"""

# src/spma/llm/masking.py
"""数据脱敏——Presidio + 自定义规则。

Layer 1: Microsoft Presidio 通用 PII（手机号/邮箱/身份证/信用卡）
Layer 2: 自定义正则（内部IP/主机名/金额/API Key）
决策: 外网API→全部脱敏, 本地vLLM→可选脱敏

设计依据: SPMA-technology-selection §12 安全与合规
"""

# src/spma/llm/token_budget.py
"""Token 预算追踪器——跨 Agent 共享。

预算按 query_type 分配:
- 单源简单: 8 次
- 单源复杂: 12 次
- 跨源: 20 次
- 三源全查: 25 次

每次 LLM 调用前 consume()，超限抛 TokenBudgetExhausted。

设计依据: SPMA-design-06 §9 Token预算管理
"""

class TokenBudgetExhausted(Exception):
    """Token 预算耗尽异常。"""
    pass


class TokenBudgetTracker:
    """Token 预算追踪器（跨 Agent 共享）。"""

    def consume(self, amount: int, agent_type: str) -> bool: ...
    def remaining(self) -> int: ...
    def snapshot(self) -> dict: ...

# src/spma/llm/prompts/__init__.py
"""共享 Prompt 模板——非 Agent 专属的通用 Prompt。"""

# src/spma/llm/prompts/classification.py
"""意图分类 Prompt 模板。
设计依据: SPMA-design-01 §5.1
"""

# src/spma/llm/prompts/generation.py
"""回答生成 Prompt 模板——Synthesis Agent 使用。
设计依据: API-04 §7.1
"""

# src/spma/llm/prompts/completeness.py
"""通用完备度判断 Prompt 模板——所有 Agent 共享。
设计依据: SPMA-design-07 §2 收敛条件设计原则
"""
```

- [ ] **Step 5: 创建 infrastructure/ 模块（8 个文件）**

```python
# src/spma/infrastructure/__init__.py
"""跨领域基础设施——状态存储、缓存、Feature Flags、降级、熔断、审计。

设计依据: SPMA-design-06 基础设施与运维设计
"""

# src/spma/infrastructure/state_store.py
"""三层状态存储——进程内存 → Redis热状态 → PostgreSQL冷trace。

Layer 1: ProcessMemoryStore (Phase 1, Python dict, 无外部依赖)
Layer 2: RedisHotStore (Phase 2+, Write-through, TTL=5min)
Layer 3: PostgresColdStore (Phase 3+, Write-back, 异步写入)

降级: Redis 不可用 → 自动降级到进程内存，标注 degradation level

设计依据: SPMA-design-06 §2 Checkpointer隔离 + SPMA-design-07 §5 状态管理
"""

from typing import Protocol


class StateStorageProtocol(Protocol):
    """状态存储的抽象接口——三层实现共用。"""

    async def save(self, key: str, state: dict, ttl: int | None = None) -> None: ...
    async def load(self, key: str) -> dict | None: ...
    async def delete(self, key: str) -> None: ...
    async def health_check(self) -> bool: ...

# src/spma/infrastructure/cache.py
"""Redis 缓存——热点问答(TTL=1h) + 查询结果(TTL=5min) + LLM翻译(TTL=24h)。

写入策略: Write-through(Agent状态), Write-around(热点问答), Write-through(翻译)

设计依据: API-06 §2 缓存契约
"""

# src/spma/infrastructure/feature_flags.py
"""Feature Flag 服务——每个 Agent 独立开关，秒级生效。

Flags 定义 (config/feature_flags.yaml):
- agents: sql_agentic, doc_agentic, code_agentic, supervisor_agentic, synth_agentic
- query: normalization, expansion, decomposition, hyde, step_back, context_aware
- retrieval: hybrid_search_weighted, code_fallback, sql_user_confirmation, cross_reranker
- degradation: auto_recovery

回滚触发: 虚假信心率>15% OR P99延迟恶化>30% OR Token成本恶化>50%

设计依据: SPMA-design-06 §5 Agent回滚机制 + API-06 §3 Feature Flags
"""

class FeatureFlagService:
    """Feature Flag 服务——秒级生效 + 变更审计。"""

    def is_enabled(self, flag_name: str, context: dict | None = None) -> bool: ...
    def get_all_flags(self) -> dict: ...
    def update_flag(self, flag_name: str, value: bool, reason: str, updated_by: str) -> bool: ...

# src/spma/infrastructure/degradation.py
"""六级降级管理器——L0(全功能) → L5(静态FAQ)。

每级配置: trigger条件 + actions动作 + auto_recovery检查间隔 + recovery条件

设计依据: SPMA-design-06 §4 多级降级策略 + API-06 §5 降级配置契约
"""

from typing import Literal

DegradationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

# 降级配置速查表
DEGRADATION_CONFIG: dict[DegradationLevel, dict] = {
    "L0": {"trigger": [], "actions": ["全功能 5 Agent 多轮循环"], "auto_recovery_sec": 0},
    "L1": {"trigger": ["主LLM超时率>10%", "主LLM 5xx>5%"],
           "actions": ["切Qwen3-8B", "完备度→确定性条件"],
           "auto_recovery_sec": 30},
    "L2": {"trigger": ["Agent P99延迟恶化>50%", "Token成本恶化>100%"],
           "actions": ["Agent→pipeline模式"],
           "auto_recovery_sec": 60},
    "L3": {"trigger": ["向量库不可用", "向量检索P99>500ms"],
           "actions": ["纯BM25检索"],
           "auto_recovery_sec": 30},
    "L4": {"trigger": ["后端检索大面积故障"],
           "actions": ["Redis缓存热点问答"],
           "auto_recovery_sec": 30},
    "L5": {"trigger": ["所有动态服务不可用"],
           "actions": ["静态FAQ + 联系管理员"],
           "auto_recovery_sec": 60},
}

class DegradationManager:
    """降级状态机——管理 L0↔L5 切换。"""

    def current_level(self) -> DegradationLevel: ...
    def check_and_degrade(self) -> bool: ...
    def check_and_recover(self) -> bool: ...
    def manual_degrade(self, level: DegradationLevel, reason: str) -> None: ...
    def manual_recover(self) -> None: ...

# src/spma/infrastructure/circuit_breaker.py
"""熔断器（v2 启用，v1 用超时+重试）。

三态模型: CLOSED(正常) → OPEN(熔断, 连续5次失败, 持续30s)
         → HALF_OPEN(探测3次) → CLOSED / 重新OPEN

设计依据: SPMA-design-06 §6 熔断器设计
"""

# src/spma/infrastructure/audit.py
"""审计日志——每次查询异步写入 PostgreSQL audit_logs 表。

记录: 用户/时间/原始query/分类/Agent结果/成本/降级级别
不阻塞 Agent 循环（asyncio.create_task）

设计依据: API-00 §6 审计日志结构
"""

# src/spma/infrastructure/security.py
"""安全组件——JWT 验证 + API Key 验证 + RBAC (v2)。

设计依据: API-00 §7 通用安全约定
"""
```

- [ ] **Step 6: 创建 config/ + observability/ 模块**

```python
# src/spma/config/__init__.py
"""配置加载模块。

配置来源优先级:
1. 环境变量（最高优先级）—— 数据库连接、API Key、Secrets
2. K8s ConfigMap —— 非敏感配置
3. YAML 配置文件 (config/*.yaml) —— 功能配置、SLO、告警
4. 数据库 feature_flags 表 —— 运行时动态开关
5. 代码默认值 —— 兜底
"""

# src/spma/config/settings.py
"""Pydantic Settings——从环境变量 + YAML + DB Flags 加载配置。

分层:
- env vars (最高优先)
- K8s ConfigMap
- config/spma.yaml
- DB feature_flags table
- 代码默认值 (最低优先)
"""

# src/spma/config/constants.py
"""硬编码常量——Agent 收敛参数默认值、模型名、超时值、权重默认值。

设计依据: SPMA-design-07 §2 收敛契约
"""

# Agent 收敛参数默认值
DEFAULT_MAX_ROUNDS = {
    "supervisor": 5,
    "doc": 3,
    "code": 3,
    "sql": 5,
    "synthesis": 2,
}

DEFAULT_TIMEOUT_MS = {
    "supervisor": 5000,
    "doc": 2000,
    "code": 2000,
    "sql": 3000,
    "synthesis": 2000,
    "hard_limit": 10000,  # 整体硬上限
}

# 模型名称
MODEL_HAIKU = "claude-haiku-4-5-20251001"
MODEL_SONNET = "claude-sonnet-4-6"
MODEL_LOCAL_FALLBACK = "qwen3-8b-local"

# 收敛质量阈值
QUALITY_THRESHOLD = 0.6  # Supervisor 质量评分收敛阈值
MAX_RESCHEDULE_ATTEMPTS = 2  # 最多重调度次数

# RRF 融合参数
RRF_K = 60  # 平滑常数

# 检索权重默认值（Phase 1 等权）
DEFAULT_RRF_WEIGHTS = {"doc": 1.0, "code": 1.0, "sql": 1.0}
```

```python
# src/spma/observability/__init__.py
"""可观测性模块——三层架构。

Layer 1: Langfuse — LLM 调用追踪 + Agent 循环追踪 + Token 成本 + Prompt 版本管理
Layer 2: OpenTelemetry — 全链路分布式追踪 (API→Supervisor→Worker→DB)
Layer 3: Grafana + Prometheus — 基础设施指标 + GPU 利用率 + 告警

设计依据: SPMA-technology-selection §9 可观测性选型
"""

# src/spma/observability/tracing.py
"""OpenTelemetry 全链路追踪初始化。

TracerProvider + OTLP Exporter + GenAI Semantic Conventions。
Span 结构: API Gateway → Supervisor Agent → Doc/Code/SQL Agent → PGVector/Redis → LLM API
"""

# src/spma/observability/metrics.py
"""Prometheus 指标注册。

Agent 指标: agent_rounds_p99, false_confidence_rate, degradation_rate
Supervisor 指标: reschedule_rate, timeout_rate, quality_score_distribution
LLM 指标: llm_call_counter, token_usage_histogram, llm_latency_histogram
"""

# src/spma/observability/langfuse_integration.py
"""Langfuse 集成——trace → span → generation 嵌套。

Agent 循环追踪: round N → search → assess → convergence
Token 成本: 自动从 LLM 响应中提取 usage 信息
Prompt 版本: 通过 Langfuse Prompt Management 管理
"""
```

- [ ] **Step 7: 提交**

```bash
git add src/spma/ingestion/ src/spma/retrieval/ src/spma/llm/ src/spma/infrastructure/ src/spma/config/ src/spma/observability/
git commit -m "feat: create ingestion, retrieval, llm, infrastructure, config, and observability skeletons"
```

---

### Task 9: 创建外部配置文件

**Files:**
- Create: `config/spma.yaml`
- Create: `config/feature_flags.yaml`
- Create: `config/alerts.yaml`
- Create: `config/ingestion.yaml`

- [ ] **Step 1: 创建目录并编写主配置**

```bash
mkdir -p config
```

```yaml
# config/spma.yaml
# SPMA 主配置文件
# 配置来源优先级: env var > K8s ConfigMap > 本文件 > DB flags > 代码默认值

spma:
  version: "0.1.0"

  # ── Agent 收敛参数 ──
  agents:
    supervisor:
      max_rounds: 5
      timeout_ms: 5000
      reschedule_max_attempts: 2
      quality_threshold: 0.6
    doc:
      max_rounds: 3
      timeout_ms: 2000
      convergence_min_results: 5
      max_clue_expansion_rounds: 2
    code:
      max_rounds: 3
      timeout_ms: 2000
      convergence_min_results: 3
      max_call_depth: 2
    sql:
      max_rounds: 5
      timeout_ms: 3000
      convergence_row_min: 1
      convergence_row_max: 10000
      execution_timeout_ms: 2000
    synthesis:
      max_rounds: 2
      timeout_ms: 2000
      min_citation_coverage: 0.8

  # ── 整体超时 ──
  hard_timeout_ms: 10000

  # ── LLM 配置 ──
  llm:
    classification_model: "claude-haiku-4-5-20251001"
    generation_model: "claude-sonnet-4-6"
    completeness_model: "claude-haiku-4-5-20251001"
    fallback_model: "qwen3-8b-local"
    local_model_endpoint: "http://vllm.internal:8000/v1"
    max_retries: 3
    retry_multiplier_seconds: 0.5
    max_wait_seconds: 2.0

  # ── Token 预算 ──
  token_budget:
    single_source_simple: 8
    single_source_complex: 12
    cross_source: 20
    three_source_full: 25

  # ── 检索配置 ──
  retrieval:
    embedding_model: "BAAI/bge-m3"
    embedding_dim: 1024
    pgvector:
      index_type: "hnsw"
      m: 16
      ef_construction: 200
      ef_search: 100
      distance_metric: "cosine"
    bm25:
      engine: "pg_tsvector"  # Phase 1-2: pg_tsvector, Phase 3+: elasticsearch
      zh_parser: "zhparser"
    rrf:
      k: 60
      default_weights:
        doc: 1.0
        code: 1.0
        sql: 1.0

  # ── 连接配置（通过环境变量注入实际值） ──
  connections:
    postgres:
      readonly_replica: "${POSTGRES_READONLY_URL}"
      vector_db: "${PGVECTOR_URL}"
    redis:
      url: "${REDIS_URL}"
      db: 0
      ttl_seconds: 300
    llm_api:
      anthropic_api_key: "${ANTHROPIC_API_KEY}"
      anthropic_base_url: "https://api.anthropic.com"

  # ── 限流 ──
  rate_limits:
    per_user_per_minute: 30
    per_ip_per_minute: 60
    global_per_minute: 1000

  # ── 延迟 SLO ──
  latency_slo:
    single_source:
      p50_ms: 3000
      p95_ms: 6000
      p99_ms: 8000
    cross_source:
      p50_ms: 6000
      p95_ms: 12000
      p99_ms: 15000

  # ── 可用性 ──
  availability:
    target: 0.999  # 99.9%
    measurement_period_days: 30
```

```yaml
# config/feature_flags.yaml
# Feature Flags——运行时动态开关，秒级生效

agents:
  # Agentic 模式开关（false=单轮 pipeline, true=多轮循环）
  sql_agentic: false
  doc_agentic: false
  code_agentic: false
  supervisor_agentic: false
  synth_agentic: false

  # 查询改写功能开关
  query_normalization: true     # 标准化（始终开启）
  query_expansion: true         # 查询扩展
  query_decomposition: false    # 分解（Phase 2）
  query_hyde: false             # HyDE（Phase 2）
  query_step_back: false        # 退一步改写（Phase 3+）
  query_context_aware: false    # 上下文感知改写（Phase 3+）

  # 检索增强
  hybrid_search_weighted: false  # 加权混合检索（Phase 2）
  code_fallback: true            # Code Agent 渐进式回退
  sql_user_confirmation: true    # SQL 高风险查询用户确认
  cross_reranker: false          # Cross-encoder Reranker（Phase 3）

  # 降级
  degradation_auto_recovery: true  # 自动恢复（检查间隔 30s）

# 回滚触发条件
rollback_triggers:
  false_confidence_rate_threshold: 0.15    # 虚假信心率 > 15%
  p99_latency_degradation_threshold: 0.30  # P99 延迟恶化 > 30%
  token_cost_degradation_threshold: 0.50   # Token 成本恶化 > 50%
```

```yaml
# config/alerts.yaml
# Prometheus 告警规则

alerts:
  # Agent 指标
  agent_rounds_p99:
    threshold: "> max_rounds"
    severity: warning
    action: "调整收敛参数"

  agent_false_confidence_rate:
    threshold: "> 0.15"
    severity: critical
    action: "触发回滚 → pipeline 模式"

  agent_early_stop_rate:
    threshold: "> 0.30"
    severity: warning
    action: "检查收敛条件是否过严"

  agent_degradation_rate:
    threshold: "> 0.10"
    severity: critical
    action: "检查基础设施（Redis/LLM）"

  agent_loop_efficiency:
    threshold: "< 0.3"
    severity: info
    action: "边际收益递减，考虑收紧 max_rounds"

  # Supervisor 指标
  supervisor_reschedule_rate:
    threshold: "> 0.30"
    severity: warning
    action: "检查分类/实体抽取质量或 Worker 检索质量"

  supervisor_timeout_rate:
    threshold: "> 0.05"
    severity: warning
    action: "检查 5s 超时设置或 Worker 延迟"

  supervisor_quality_score_p50:
    threshold: "< 0.5"
    severity: warning
    action: "Worker 整体质量不足"

  # LLM 指标
  llm_error_rate:
    threshold: "> 0.10"
    severity: critical
    action: "触发 L1 降级"

  # 延迟 SLO
  p99_latency_single_source:
    threshold: "> 8000ms"
    severity: warning
    action: "检查 Worker 检索延迟"

  p99_latency_cross_source:
    threshold: "> 15000ms"
    severity: warning
    action: "检查跨源编排效率"
```

```yaml
# config/ingestion.yaml
# 数据摄入调度配置

ingestion:
  # ── PRD 文档 ──
  doc:
    webhook_enabled: true
    webhook_debounce_seconds: 30
    full_sync_schedule: "0 2 * * *"  # 每日凌晨 2:00 UTC
    parser: "docling"  # docling | unstructured
    chunk_size_tokens: 500
    overlap_tokens: 50
    embedding_model: "BAAI/bge-m3"
    embedding_batch_size: 32

  # ── 代码仓库 ──
  code:
    webhook_enabled: true
    webhook_debounce_seconds: 10
    repos: []  # 空=全部仓库
    update_file_path_cache: true
    update_code_metadata: true
    ast_languages: ["python", "typescript", "java", "go"]
    max_repos_parallel: 5

  # ── SQL Schema ──
  sql:
    polling_enabled: true
    polling_interval_seconds: 600  # 10 分钟
    databases: []  # 空=全部数据库
    include_table_data_samples: false
    refresh_few_shot_examples: false
    refresh_enum_definitions: true

  # ── 同义词映射 ──
  synonym_map:
    auto_update_enabled: true
    auto_apply_confidence_threshold: 0.9
    decay_check_days: 30
    sources:
      - information_schema
      - prd_titles
      - git_dirs

  # ── 全局 ──
  max_concurrent_ingestions: 3
  embedding_rate_limit_per_minute: 1000
  freshness_slo:
    doc_incremental_minutes: 5
    code_incremental_minutes: 5
    sql_polling_minutes: 10
```

- [ ] **Step 2: 提交**

```bash
git add config/
git commit -m "feat: create external YAML config files (spma, feature_flags, alerts, ingestion)"
```

---

### Task 10: 创建测试骨架

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/conftest.py`
- Create: 各 `tests/unit/`, `tests/integration/`, `tests/eval/`, `tests/e2e/` 目录及其 __init__.py 和 conftest.py
- Create: `tests/eval/agent_eval_dataset.json`

- [ ] **Step 1: 创建测试目录结构**

```bash
mkdir -p tests/unit/agents/supervisor
mkdir -p tests/unit/agents/doc
mkdir -p tests/unit/agents/code
mkdir -p tests/unit/agents/sql
mkdir -p tests/unit/agents/synthesis
mkdir -p tests/unit/retrieval
mkdir -p tests/unit/llm
mkdir -p tests/unit/infrastructure
mkdir -p tests/unit/ingestion
mkdir -p tests/integration
mkdir -p tests/eval
mkdir -p tests/e2e
```

- [ ] **Step 2: 创建根级测试配置**

```python
# tests/__init__.py
"""SPMA 测试套件。

三层测试结构:
- unit/: 单元测试（mock, <5s 全量, CI 每次 commit）
- integration/: 集成测试（testcontainers, <2min, CI 每个 PR）
- eval/: 评估脚本（真实 LLM + 标注数据集, 手动触发）
- e2e/: 端到端测试（真实 LLM + 完整环境, 发布前触发）
"""

# tests/conftest.py
"""全局测试 fixtures。

提供的 fixtures:
- mock_llm: MockLLM 客户端（按预编排序列逐轮返回）
- test_redis: testcontainers Redis 实例
- test_pgvector: testcontainers PGVector 实例
- test_client: FastAPI TestClient
"""

import pytest


@pytest.fixture
def mock_llm():
    """MockLLM fixture——按预编排响应序列逐轮返回。

    使用方式:
        mock_llm.set_responses([
            {"sufficient": False, "confidence": 0.4, "missing": ["code files"]},
            {"sufficient": False, "confidence": 0.6, "missing": ["related tables"]},
            {"sufficient": True, "confidence": 0.9, "missing": []},
        ])
    """
    raise NotImplementedError


@pytest.fixture
def test_redis():
    """testcontainers Redis 实例——集成测试用。"""
    raise NotImplementedError


@pytest.fixture
def test_pgvector():
    """testcontainers PGVector 实例——集成测试用。"""
    raise NotImplementedError
```

- [ ] **Step 3: 创建单元测试的包 init 文件和占位测试**

各单元测试目录创建 `__init__.py` 和至少一个骨架测试文件：

```bash
# 批量创建 __init__.py
find tests -type d -exec touch {}/__init__.py \;
```

```python
# tests/unit/agents/test_base.py
"""Agent 基类的单元测试。

测试: check_convergence, consume_budget, save_checkpoint
"""

import pytest


class TestBaseAgent:
    """Agent 基类测试。"""

    def test_check_convergence_deterministic(self):
        """测试确定性收敛路径——不调 LLM。"""
        pass

    def test_consume_budget_within_limit(self):
        """测试 Token 预算在限制内正常消耗。"""
        pass

    def test_consume_budget_exhausted(self):
        """测试 Token 预算耗尽时抛出异常。"""
        pass
```

其余测试文件同理——包含 class + 方法签名 + pass 占位。完整列表见设计文档 tests/ 目录结构。

- [ ] **Step 4: 创建 Agent Eval Dataset**

```json
// tests/eval/agent_eval_dataset.json
{
  "$schema": "spma/agent-eval-dataset/1.0",
  "description": "Agent 评估基准数据集——50 条标注 query × golden results",
  "version": "0.1.0",
  "queries": [
    {
      "query_id": "eval-001",
      "query": "用户登录模块的PRD改了哪些内容？影响了哪些代码文件和数据库表？",
      "query_type": "cross_source",
      "golden_docs": [],
      "golden_code": [],
      "golden_sql_tables": []
    }
  ]
}
```

```python
# tests/eval/run_eval.py
"""评估运行入口——python -m tests.eval.run_eval。

独立于 pytest，需要真实 LLM + 完整数据集 + 长时间执行。
"""

def main():
    """运行所有评估脚本。"""
    raise NotImplementedError


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 创建集成测试和 E2E 测试 conftest**

```python
# tests/integration/conftest.py
"""集成测试 fixtures——testcontainers 提供的真实依赖。"""

import pytest


@pytest.fixture(scope="session")
def pgvector_container():
    """testcontainers PGVector 实例——session 级复用。"""
    raise NotImplementedError


@pytest.fixture(scope="session")
def redis_container():
    """testcontainers Redis 实例——session 级复用。"""
    raise NotImplementedError
```

```python
# tests/e2e/conftest.py
"""E2E 测试 fixtures——真实 LLM + 完整测试环境。"""

import pytest


@pytest.fixture(scope="module")
def live_llm_client():
    """真实 LLM 客户端——E2E 测试使用。"""
    raise NotImplementedError


@pytest.fixture(scope="module")
def full_stack():
    """完整 SPMA 系统——API + 所有 Agent + 存储。"""
    raise NotImplementedError
```

- [ ] **Step 6: 提交**

```bash
git add tests/
git commit -m "feat: create test suite skeleton (unit, integration, eval, e2e)"
```

---

### Task 11: 创建部署模板

**Files:**
- Create: `deployments/docker/Dockerfile.api, Dockerfile.agent, Dockerfile.ingestion, Dockerfile.vllm`
- Create: `deployments/helm/spma/Chart.yaml, values.yaml, values-dev.yaml, values-prod.yaml`
- Create: `deployments/helm/spma/templates/*.yaml (8 个模板)`

- [ ] **Step 1: 创建目录和 Dockerfiles**

```bash
mkdir -p deployments/docker deployments/helm/spma/templates
```

```dockerfile
# deployments/docker/Dockerfile.api
# SPMA API Gateway 镜像
# 内容: FastAPI + 路由 + 中间件（轻量，无 Agent 逻辑）

FROM python:3.13-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 安装依赖
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 复制源码
COPY src/ src/

# 暴露端口
EXPOSE 8000

# 启动 API 服务
CMD ["uv", "run", "spma-api"]
```

```dockerfile
# deployments/docker/Dockerfile.agent
# SPMA Agent 服务镜像
# 内容: LangGraph + 5 个 Agent 子图 + 检索基础设施

FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 安装依赖（含所有 Agent extras）
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra all

# 复制源码
COPY src/ src/
COPY config/ config/

# Agent 服务端口（gRPC 或内部 HTTP）
EXPOSE 9000

CMD ["uv", "run", "python", "-m", "spma.agents.supervisor.graph"]
```

```dockerfile
# deployments/docker/Dockerfile.ingestion
# SPMA 数据摄入管道镜像
# 内容: APScheduler + 解析器(Docling/TreeSitter) + 分块器

FROM python:3.13-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra all

# 安装系统依赖（ripgrep, git）
RUN apt-get update && apt-get install -y --no-install-recommends \
    ripgrep git && \
    rm -rf /var/lib/apt/lists/*

COPY src/ src/
COPY config/ config/

CMD ["uv", "run", "spma-ingest"]
```

```dockerfile
# deployments/docker/Dockerfile.vllm
# vLLM 推理服务镜像
# 内容: vLLM V1 Engine + BGE-M3 + Qwen3-8B

FROM vllm/vllm-openai:latest

# 预下载模型（构建时）
ARG MODEL_NAME=BAAI/bge-m3
ARG HF_HUB_ENABLE_HF_TRANSFER=1

RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('BAAI/bge-m3')"

RUN python -c "from huggingface_hub import snapshot_download; \
    snapshot_download('Qwen/Qwen3-8B')"

# vLLM 启动参数
ENV VLLM_ENGINE=v1
ENV GPU_MEMORY_UTILIZATION=0.90
ENV MAX_MODEL_LEN=8192

EXPOSE 8000

CMD ["--model", "BAAI/bge-m3", "--enable-prefix-caching"]
```

- [ ] **Step 2: 创建 Helm Chart**

```yaml
# deployments/helm/spma/Chart.yaml
apiVersion: v2
name: spma
description: SPMA — 企业级多源RAG智能问答系统 Helm Chart
type: application
version: 0.1.0
appVersion: "0.1.0"
```

```yaml
# deployments/helm/spma/values.yaml
# 默认配置（生产环境通过 values-prod.yaml 覆盖）

global:
  imageRegistry: ""
  imagePullSecrets: []
  storageClass: ""

# API Gateway
api:
  replicaCount: 2
  image:
    repository: spma-api
    tag: "0.1.0"
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
    port: 8000
  resources:
    limits:
      cpu: "4"
      memory: 8Gi
    requests:
      cpu: "2"
      memory: 4Gi
  hpa:
    enabled: true
    minReplicas: 2
    maxReplicas: 6
    targetCPUUtilizationPercentage: 70

# Agent Service
agent:
  replicaCount: 2
  image:
    repository: spma-agent
    tag: "0.1.0"
  service:
    type: ClusterIP
    port: 9000
  resources:
    limits:
      cpu: "4"
      memory: 8Gi
    requests:
      cpu: "2"
      memory: 4Gi

# Ingestion Worker
ingestion:
  replicaCount: 1  # 单实例（避免重复摄入）
  image:
    repository: spma-ingestion
    tag: "0.1.0"
  resources:
    limits:
      cpu: "2"
      memory: 4Gi
    requests:
      cpu: "1"
      memory: 2Gi

# vLLM Inference
vllm:
  replicaCount: 2
  image:
    repository: spma-vllm
    tag: "0.1.0"
  gpu:
    enabled: true
    count: 1
    type: "nvidia.com/gpu"
  resources:
    limits:
      nvidia.com/gpu: 1
      memory: 80Gi
    requests:
      nvidia.com/gpu: 1
      memory: 80Gi

# Redis
redis:
  enabled: true
  architecture: standalone
  auth:
    enabled: false
  master:
    persistence:
      enabled: true
      size: 8Gi
    resources:
      limits:
        memory: 8Gi
      requests:
        memory: 8Gi

# PostgreSQL + PGVector
postgresql:
  enabled: true
  image:
    tag: "16"
  auth:
    database: spma
  primary:
    persistence:
      enabled: true
      size: 100Gi
    resources:
      limits:
        cpu: "8"
        memory: 32Gi
      requests:
        cpu: "4"
        memory: 16Gi
  readReplicas:
    replicaCount: 1

# External services
external:
  llmApi:
    anthropicApiKey: ""
    anthropicBaseUrl: "https://api.anthropic.com"
  postgres:
    readonlyReplicaUrl: ""
  redis:
    url: ""

# Feature Flags (default to pipeline mode)
featureFlags:
  sql_agentic: false
  doc_agentic: false
  code_agentic: false
  supervisor_agentic: false
  synth_agentic: false
  query_normalization: true
  query_expansion: true
```

```yaml
# deployments/helm/spma/values-dev.yaml
# 开发环境覆盖配置

global:
  imageRegistry: "localhost:5000"

api:
  replicaCount: 1
  resources:
    limits:
      cpu: "1"
      memory: 2Gi
    requests:
      cpu: "500m"
      memory: 1Gi
  hpa:
    enabled: false

agent:
  replicaCount: 1
  resources:
    limits:
      cpu: "1"
      memory: 2Gi
    requests:
      cpu: "500m"
      memory: 1Gi

ingestion:
  replicaCount: 1
  resources:
    limits:
      cpu: "1"
      memory: 2Gi
    requests:
      cpu: "500m"
      memory: 1Gi

vllm:
  replicaCount: 1

redis:
  master:
    persistence:
      size: 2Gi
    resources:
      limits:
        memory: 2Gi
      requests:
        memory: 2Gi

postgresql:
  primary:
    persistence:
      size: 20Gi
    resources:
      limits:
        cpu: "2"
        memory: 8Gi
      requests:
        cpu: "1"
        memory: 4Gi
```

```yaml
# deployments/helm/spma/values-prod.yaml
# 生产环境覆盖配置

api:
  replicaCount: 3
  hpa:
    maxReplicas: 10

agent:
  replicaCount: 3

vllm:
  replicaCount: 2
  gpu:
    count: 1

redis:
  architecture: replication
  auth:
    enabled: true

postgresql:
  primary:
    persistence:
      size: 500Gi
    resources:
      limits:
        cpu: "16"
        memory: 64Gi
      requests:
        cpu: "8"
        memory: 32Gi
  readReplicas:
    replicaCount: 2
```

- [ ] **Step 3: 创建 Helm 模板文件（8 个）**

由于模板文件内容高度模式化（K8s Deployment/Service/ConfigMap/Secret/Ingress/HPA），使用标准 K8s YAML 模板：

```yaml
# deployments/helm/spma/templates/api-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ include "spma.fullname" . }}-api
  labels:
    {{- include "spma.labels" . | nindent 4 }}
    component: api
spec:
  replicas: {{ .Values.api.replicaCount }}
  selector:
    matchLabels:
      {{- include "spma.selectorLabels" . | nindent 6 }}
      component: api
  template:
    metadata:
      labels:
        {{- include "spma.selectorLabels" . | nindent 8 }}
        component: api
    spec:
      containers:
      - name: api
        image: {{ .Values.global.imageRegistry }}/{{ .Values.api.image.repository }}:{{ .Values.api.image.tag }}
        ports:
        - containerPort: {{ .Values.api.service.port }}
        resources:
          {{- toYaml .Values.api.resources | nindent 10 }}
        envFrom:
        - configMapRef:
            name: {{ include "spma.fullname" . }}-config
        - secretRef:
            name: {{ include "spma.fullname" . }}-secrets
---
# 其余 7 个模板同理: agent-deployment, ingestion-deployment, vllm-deployment,
# api-service, configmap, secrets, ingress, hpa
```

- [ ] **Step 4: 提交**

```bash
git add deployments/
git commit -m "feat: create Docker deployment templates and Helm chart"
```

---

### Task 12: 创建工具脚本 + 最终验证

**Files:**
- Create: `scripts/seed_data.py`
- Create: `scripts/run_ingestion.py`
- Create: `scripts/eval_weights.py`

- [ ] **Step 1: 创建 scripts 目录和工具脚本**

```bash
mkdir -p scripts
```

```python
# scripts/seed_data.py
"""开发环境种子数据脚本——向 PGVector 写入测试文档和 Schema。

用法: uv run python scripts/seed_data.py
"""

def main():
    """写入测试数据到 PGVector 和元数据表。"""
    raise NotImplementedError


if __name__ == "__main__":
    main()
```

```python
# scripts/run_ingestion.py
"""手动触发摄入管道——绕过 APScheduler 直接执行。

用法:
    uv run python scripts/run_ingestion.py --source doc
    uv run python scripts/run_ingestion.py --source code --repos auth-service
    uv run python scripts/run_ingestion.py --source sql
"""

def main():
    """解析参数，执行对应管道的单次摄入。"""
    raise NotImplementedError


if __name__ == "__main__":
    main()
```

```python
# scripts/eval_weights.py
"""离线权重评估脚本——网格搜索 BM25/向量权重组合。

用法: uv run python scripts/eval_weights.py
"""

def main():
    """从标注数据集加载 → 网格搜索权重 → 输出 NDCG@10 矩阵。"""
    raise NotImplementedError


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 最终验证——确认目录结构完整性**

```bash
# 验证没有空目录（每个目录至少有一个 __init__.py）
find src/spma -type d -exec sh -c 'ls -1 "$1"/*.py 2>/dev/null | wc -l' _ {} \; | grep "^0" && echo "EMPTY DIRS FOUND" || echo "All dirs have .py files"

# 验证 pyproject.toml 语法
uv run python -c "import tomllib; tomllib.load(open('pyproject.toml','rb')); print('TOML valid')"

# 验证导入链
uv run python -c "from spma.models import AgentState, WorkerOutput, ExtractedEntities; print('models OK')"

# 统计文件数量
echo "=== File Count ==="
find src/spma -name "*.py" | wc -l
echo "Python files"
find tests -name "*.py" | wc -l
echo "Test files"
find config -name "*.yaml" | wc -l
echo "Config files"
```

Expected 输出（大约）:
```
All dirs have .py files
TOML valid
models OK
=== File Count ===
     110 Python files
      50 Test files
       4 Config files
```

- [ ] **Step 3: 最终提交**

```bash
git add scripts/
git add -A
git status
git commit -m "feat: add utility scripts and finalize project structure

Complete project skeleton initialized:
- src/spma/: 5 agents, API, ingestion, retrieval, llm, infrastructure, models, config, observability
- tests/: unit, integration, eval, e2e
- config/: spma.yaml, feature_flags.yaml, alerts.yaml, ingestion.yaml
- deployments/: 4 Dockerfiles + Helm chart
- scripts/: seed_data, run_ingestion, eval_weights
- pyproject.toml with uv, ruff, mypy, pytest configuration"
```

---

## 完成检查清单

- [ ] `pyproject.toml` 语法有效，`uv sync` 成功
- [ ] `src/spma/` 下所有包有 `__init__.py` 且无双 import 错误
- [ ] `tests/` 目录镜像 `src/spma/` 结构，conftest.py 覆盖全局 fixtures
- [ ] 4 个 YAML 配置文件存在且格式正确
- [ ] 4 个 Dockerfile 存在
- [ ] Helm Chart 模板完整
- [ ] Agent 内部文件约定一致（每个 Agent: graph.py + state.py + prompts.py + 领域逻辑模块）
- [ ] Ruff 零错误: `uv run ruff check src/`
- [ ] MyPy 零错误: `uv run mypy src/spma/models/`（至少 models 包类型正确）

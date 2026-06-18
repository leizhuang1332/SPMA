# Design: API-05 数据摄入管道全量落地

> 目标：将 [API-05 数据摄入 API](../API-05-data-ingestion-api.md) 完整落地到当前 SPMA 源码中
> 策略：全量落地——10 个端点 + 完整 Webhook + 调度器 + AST 调用图
> 相关设计：[数据摄入管道设计](../designs/SPMA-design-05-data-ingestion.md)
> 日期：2026-06-18

---

## 一、现状差距总结

| 层次 | 状态 |
|------|------|
| REST 路由（8 端点） | ❌ 全部未实现 |
| Webhook 路由（2 端点） | ❌ 全部未实现 |
| Pydantic Schema 模型 | ❌ 空文件 |
| Doc Pipeline | ✅ 核心逻辑完整，`upsert` 签名需修复 |
| Code Pipeline | ❌ 只有 docstring，需组装散件 |
| SQL Pipeline | 🟡 仅有独立函数，需封装为类 + 增量逻辑 |
| AST Parser | 🟡 TreeSitter 实际解析未集成，返回空字典 |
| Scheduler | 🟡 只有骨架，缺 doc/code webhook 接入 |
| Synonym Map | ❌ 只有 docstring，无实现 |
| Pipeline Run 状态追踪 | ❌ 完全缺失 |
| 新鲜度监控 | ❌ 完全缺失 |

---

## 二、总体架构

### 2.1 部署拓扑

```
┌──────────────────────────────────────────────────────────┐
│                    spma-api (FastAPI)                     │
│  ┌─────────────┐ ┌──────────────┐ ┌───────────────────┐  │
│  │ ingestion   │ │  webhook     │ │  admin routes     │  │
│  │ routes (8)  │ │  routes (2)  │ │  (degradation...) │  │
│  └──────┬──────┘ └──────┬───────┘ └───────────────────┘  │
│         │               │                                 │
│         └───────┬───────┘                                 │
│                 ▼                                         │
│  ┌──────────────────────────────────────┐                │
│  │        IngestionController           │                │
│  │  (编排层：参数校验 → 创建 run →      │                │
│  │   异步执行 pipeline → 更新 stats)    │                │
│  └──────────────┬───────────────────────┘                │
│                 │                                         │
│  ┌──────────────┼──────────────────────────────────────┐ │
│  │              ▼              PostgreSQL               │ │
│  │  ┌────────────────┐  ┌──────────────────┐           │ │
│  │  │ ingestion_runs │  │ file_path_cache  │           │ │
│  │  │ (pipeline状态) │  │ code_metadata    │           │ │
│  │  └────────────────┘  │ chunk_embeddings │           │ │
│  │                      │ synonym_map      │           │ │
│  │                      └──────────────────┘           │ │
│  └─────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────┐
│              spma-ingest (独立进程)                        │
│  ┌────────────────────────────────────────────┐          │
│  │            APScheduler                      │          │
│  │  ┌──────────┐ ┌──────────┐ ┌────────────┐  │          │
│  │  │ doc full │ │ sql poll │ │ synonym    │  │          │
│  │  │ sync cron│ │ interval │ │ auto-refresh│ │          │
│  │  └──────────┘ └──────────┘ └────────────┘  │          │
│  └────────────────────────────────────────────┘          │
└──────────────────────────────────────────────────────────┘
```

### 2.2 组件关系

所有 Pipeline 类统一暴露 `async def run(request) -> IngestionResult` 接口，供 `IngestionController` 和 Scheduler 共同调用。

```
IngestionController  (新增 — 编排层)
    ├── DocIngestionPipeline  (已有，需修复 upsert + 新增 run() 方法)
    │       ├── DoclingParser / UnstructuredParser
    │       ├── SemanticChunker
    │       ├── BGEM3Embedder
    │       ├── ESClient
    │       └── PGVectorStore
    ├── CodeIngestionPipeline (新增 — 组装散件)
    │       ├── GitManager
    │       ├── FilePathCache
    │       ├── ASTParser (需补全 TreeSitter)
    │       └── GitLogReqExtractor
    ├── SqlIngestionPipeline  (重构 — 封装为类 + 增量 diff)
    │       ├── SchemaIntrospector
    │       ├── ChunkBuilder
    │       └── SchemaEmbedder
    ├── PipelineRunStore      (新增 — 状态持久化)
    ├── SynonymMap            (新增 — 同义词映射)
    └── FreshnessService      (新增 — 新鲜度监控)
```

`IngestionResult` 结构：

```python
@dataclass
class IngestionResult:
    stats: dict      # API-05 §3.2/§4.2/§5.2 各类型 stats
    errors: list     # [{page_id, error, severity}, ...]
    status: str      # "completed" | "failed"
```

### 2.3 调用流程（以手动触发文档摄入为例）

```
POST /api/v1/ingest/documents
  → ingestion_router
    → Depends(get_current_admin)  # JWT 认证
    → DocIngestionRequest (Pydantic 校验)
    → IngestionController.ingest_documents(request)
        → PipelineRunStore.create("doc", request.mode) → run_id
        → background asyncio.Task: DocIngestionPipeline.run(request)
            → 解析 → 分块 → embed → ES+PGVector 双写
            → PipelineRunStore.update(run_id, stats)
        → return IngestionResponse(run_id, "running", ...)
```

设计决策：
- `IngestionController` 是纯编排层，不包含业务逻辑——只做参数校验、创建 run 记录、异步执行 pipeline、返回响应
- Controller 通过依赖注入获取 pipeline 实例，便于测试和替换
- Scheduler 进程复用同一个 Pipeline 类——两个入口走完全相同代码路径，仅触发方式不同

---

## 三、Pipeline 运行状态追踪

### 3.1 数据库表

```sql
CREATE TABLE ingestion_runs (
    pipeline_run_id   TEXT PRIMARY KEY,        -- "ingest-doc-20260607-102345"
    pipeline_type     TEXT NOT NULL,           -- "doc" | "code" | "sql"
    source            TEXT,                    -- "confluence" | "markdown_dir" | ...
    mode              TEXT NOT NULL,           -- "incremental" | "full"
    status            TEXT NOT NULL DEFAULT 'pending',  -- pending|running|completed|failed
    started_at        TIMESTAMPTZ,
    completed_at      TIMESTAMPTZ,
    estimated_completion TIMESTAMPTZ,
    stats             JSONB DEFAULT '{}',      -- 各 pipeline 类型的 stats 字段
    errors            JSONB DEFAULT '[]',      -- [{page_id, error, severity}, ...]
    created_by        TEXT,                    -- "manual" | "webhook" | "scheduler"
    created_at        TIMESTAMPTZ DEFAULT NOW()
);
```

### 3.2 PipelineRunStore

文件：[src/spma/ingestion/run_store.py](src/spma/ingestion/run_store.py)

```python
class PipelineRunStore:
    def __init__(self, db_pool: asyncpg.Pool)

    async def create(self, pipeline_type, source, mode, created_by) -> str  # → run_id
    async def update(self, run_id, status, stats, errors, completed_at)     # 原子更新
    async def get(self, run_id) -> dict | None                              # 单条查询
    async def get_latest(self, pipeline_type) -> dict | None                # 最新一条
    async def list_recent(self, limit=20) -> list[dict]                     # 最近 N 条
```

- 使用 `asyncpg.Pool`，与现有 `file_path_cache.py` 一致
- `stats` 用 JSONB——三种 pipeline 的结构不同（API-05 §3.2/§4.2/§5.2）
- `run_id` 格式：`ingest-{type}-{YYYYMMDD}-{HHmmss}`

---

## 四、Code Pipeline 组装

### 4.1 流程

```
Git Webhook / 手动触发
    │
    ▼
CodeIngestionPipeline.run(repos, mode, options)
    │
    ├─ Phase 0: GitManager.pull_repo (每个 repo)
    │
    ├─ Phase 1: FilePathCache.build_cache / incremental_update
    │           → PostgreSQL file_path_cache 表
    │
    ├─ Phase 2: GitLogReqExtractor.extract_req_links
    │           → {REQ-ID: [files]}
    │
    ├─ Phase 3: ASTParser.parse_directory (变更文件)
    │           → code_metadata 表的 {calls, called_by, imports}
    │
    └─ Phase 4: PipelineRunStore.update (stats 汇总)
```

### 4.2 并发策略

- 每个 repo 内部串行处理（避免并发 git 操作冲突）
- 不同 repo 之间并行，受 `max_repos_parallel` 限制
- `force_full_reclone: true` 时删除并重新 `git clone --depth 1`
- `re_parse_ast: true` 时全量重新解析而非仅增量文件

---

## 五、AST 解析器补全

### 5.1 语言覆盖

| 语言 | 函数定义 | 函数调用 | 类定义 | import/include |
|------|---------|---------|--------|---------------|
| Python | `function_definition` | `call` + `attribute` | `class_definition` | `import_statement` / `import_from_statement` |
| TypeScript | `function_declaration` + `arrow_function` + `method_definition` | `call_expression` | `class_declaration` | `import_statement` |
| Java | `method_declaration` + `constructor_declaration` | `method_invocation` | `class_declaration` | `import_declaration` |
| Go | `function_declaration` + `method_declaration` | `call_expression` | `type_declaration` | `import_declaration` |

### 5.2 Grammar 获取

使用 `tree-sitter` PyPI 包 + 各语言预编译的 `.so`/`.dylib` 动态库，缓存到 `~/.cache/tree-sitter/`。避免编译依赖（node-gyp），且与项目已有的 `tree-sitter` 可选依赖一致。

### 5.3 输出结构

对齐 API-05 §4.3 的 `CodeMetadataEntry`：

```python
class CodeMetadataEntry(TypedDict):
    repo: str
    file_path: str
    function_name: Optional[str]
    class_name: Optional[str]
    line_start: int
    line_end: int
    calls: list[str]
    called_by: list[str]     # 入库后通过 SQL 反向查询构建
    imports: list[str]
    req_ids: list[str]        # git log 中关联的需求 ID
    commit_hash: str
    updated_at: str
```

`called_by` 反向索引策略：解析时只提取正向 `calls`，入库后通过 SQL 反向查询构建——避免单文件解析时依赖全局上下文。

### 5.4 实现文件

修改 [src/spma/ingestion/code/ast_parser.py](src/spma/ingestion/code/ast_parser.py)，新增方法：

- `_ensure_grammar(language)` — 懒加载 TreeSitter grammar
- `parse_file(file_path)` — 单文件解析 → `CodeFileAST`
- `parse_directory(repo_path, changed_files=None)` — 批量解析目录

---

## 六、路由层

### 6.1 REST 端点

文件：[src/spma/api/routes/ingestion.py](src/spma/api/routes/ingestion.py)

| 方法 | 路径 | 说明 | 认证 |
|------|------|------|------|
| `POST` | `/api/v1/ingest/documents` | 手动触发 PRD 文档摄入 | Admin |
| `POST` | `/api/v1/ingest/code` | 手动触发代码仓库摄入 | Admin |
| `POST` | `/api/v1/ingest/schema` | 手动触发 SQL Schema 摄入 | Admin |
| `GET` | `/api/v1/ingest/status` | 查询摄入管道状态 | Admin |
| `GET` | `/api/v1/ingest/status/{pipeline_run_id}` | 查询特定运行状态 | Admin |
| `GET` | `/api/v1/ingest/freshness` | 查询知识新鲜度 | Authenticated |
| `POST` | `/api/v1/ingest/synonym-map/refresh` | 刷新同义词映射表 | Admin |
| `GET` | `/api/v1/ingest/synonym-map` | 查询同义词映射表 | Admin |

### 6.2 Webhook 端点

文件：[src/spma/api/routes/ingestion_webhooks.py](src/spma/api/routes/ingestion_webhooks.py)

**Confluence Webhook（`POST /api/v1/webhooks/confluence`）：**

```
验签: X-Confluence-Webhook-Token header == config secret
  → 防抖: Redis SETNX (page_id + version, debounce_seconds TTL)
    → 调用 Confluence REST API: GET /rest/api/content/{page_id}?expand=body.storage
      → DoclingParser 解析 HTML → SemanticChunker 分块 → embed → ES+PGVector 双写
        → PipelineRunStore 记录 webhook 触发的 run
```

**Git Webhook（`POST /api/v1/webhooks/git`）：**

```
验签: HMAC-SHA256(payload, webhook_secret) == X-Hub-Signature-256 header
  → 防抖: Redis SETNX (repo + branch, debounce_seconds TTL)
    → GitManager.handle_webhook(payload) → git pull
      → 增量更新 file_path_cache
        → ASTParser.parse_directory(changed_files)
          → 增量更新 code_metadata
            → PipelineRunStore 记录
```

### 6.3 认证

- 管理类端点：复用已有的 `get_current_admin` 依赖
- 新鲜度查询：`get_current_user` — 所有登录用户可查
- Webhook：不走 JWT——Confluence 用共享密钥 header，Git 用 HMAC 签名

### 6.4 Pydantic 模型

文件：[src/spma/api/schemas/ingestion.py](src/spma/api/schemas/ingestion.py)

严格按照 API-05 定义：

- `DocIngestionSource`（StrEnum）
- `DocIngestionRequest` + `DocIngestionFilters` + `DocIngestionOptions`
- `CodeIngestionRequest` + `CodeIngestionOptions`
- `SchemaIngestionRequest` + `SchemaIngestionOptions`
- `SynonymRefreshRequest`
- 各类 Response 模型

---

## 七、调度器

文件：[src/spma/ingestion/scheduler.py](src/spma/ingestion/scheduler.py)

入口：`uv run spma-ingest`（已在 [pyproject.toml](pyproject.toml#L71) 定义）

```
spma-ingest 进程
│
├─ Doc Full Sync:       cron "0 2 * * *"    → DocIngestionPipeline.run(mode="full")
├─ SQL Schema Polling:  interval 600s       → SqlIngestionPipeline.run(mode="incremental")
├─ Synonym Auto-Refresh: cron "0 3 * * *"  → SynonymMap.refresh()
└─ Freshness Check:     interval 300s       → FreshnessService.check_slo()
```

调度器调用与 REST 端点完全相同的 Pipeline 类方法，确保行为一致。Doc/Code 增量更新由 Webhook 驱动——调度器只负责全量兜底和定时轮询。

---

## 八、同义词映射

文件：[src/spma/ingestion/synonym_map.py](src/spma/ingestion/synonym_map.py)

### 8.1 数据表

```sql
CREATE TABLE synonym_map (
    id              SERIAL PRIMARY KEY,
    user_term       TEXT NOT NULL,
    canonical_term  TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT 'table_name',
    source          TEXT NOT NULL,           -- information_schema|prd_titles|git_dirs|manual
    confidence      REAL DEFAULT 0.5,
    status          TEXT DEFAULT 'active',   -- active|pending_review|deprecated
    hits_30d        INT DEFAULT 0,
    last_triggered_at TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

### 8.2 冷启动数据来源

| 来源 | 提取逻辑 | 预估条数 |
|------|---------|---------|
| `information_schema` | 表名↔表注释、列名↔列注释 | 50-60 |
| PRD 标题 | 需求关键词→需求ID映射 | 15-20 |
| Git 目录 | 目录名→模块名 | 10-15 |
| 人工补充 | 口语/缩写/别名 | 20-30 |

### 8.3 API

```python
class SynonymMap:
    def __init__(self, db_pool: asyncpg.Pool, config: dict)
    
    async def refresh(self, sources: list[str], auto_apply_threshold: float) -> int
    async def query(self, status: str = "all", limit: int = 100) -> list[dict]
    async def lookup(self, user_term: str) -> str | None
    async def apply_entry(self, entry_id: int) -> None
    async def mark_deprecated(self, entry_id: int) -> None
```

---

## 九、新鲜度监控

文件：[src/spma/ingestion/freshness.py](src/spma/ingestion/freshness.py)

实现 API-05 §7 `GET /api/v1/ingest/freshness` 端点。

数据来源：
- **实时数据**（SLO 判断）：`ingestion_runs` 表最近成功时间 vs 当前时间
- **存量数据**（最旧 chunk）：`chunk_embeddings` / `file_path_cache` 最老更新时间

SLO 阈值来自 [config/ingestion.yaml](config/ingestion.yaml) 的 `freshness_slo` 配置块。

---

## 十、FastAPI 集成

修改 [src/spma/api/app.py](src/spma/api/app.py)：

### 10.1 Startup 事件

```python
@app.on_event("startup")
async def startup_ingestion():
    """初始化摄入管道——ES/PGVector/Embedder → IngestionController。"""
    # 1. ES Client
    # 2. PGVector (复用已有)
    # 3. BGEM3Embedder
    # 4. DocIngestionPipeline
    # 5. CodeIngestionPipeline (GitManager + FilePathCache + ASTParser)
    # 6. SqlIngestionPipeline
    # 7. IngestionController
    # 注入到 dependencies.py 单例
```

### 10.2 路由注册

```python
from spma.api.routes.ingestion import router as ingestion_router
from spma.api.routes.ingestion_webhooks import router as webhook_router

app.include_router(ingestion_router, prefix="/api/v1")
app.include_router(webhook_router, prefix="/api/v1")
```

### 10.3 依赖注入链

在 [dependencies.py](src/spma/api/dependencies.py) 中新增：

```python
_ingestion_controller: IngestionController | None = None

def get_ingestion_controller() -> IngestionController: ...
def set_ingestion_controller(controller: IngestionController) -> None: ...
```

---

## 十一、需修复的现有问题

| 问题 | 位置 | 修复方式 |
|------|------|---------|
| `upsert` 签名不匹配 | [vector_store.py](src/spma/retrieval/vector_store.py) vs [doc_pipeline.py](src/spma/ingestion/doc_pipeline.py) | DocIngestionPipeline 改为逐个调用 `vector_store.upsert(chunk_id, source_id, source_type, content, embedding, metadata)`，内部 batch 循环 |
| `sql_pipeline.py` 无类封装 | [sql_pipeline.py](src/spma/ingestion/sql_pipeline.py) | 重构为 `SqlIngestionPipeline` 类，含 `run()` 和增量 diff 逻辑 |
| PGVectorStore 缺少 `delete_by_source` | [vector_store.py](src/spma/retrieval/vector_store.py) | 新增 `DELETE FROM chunk_embeddings WHERE source_id = $1` |
| `schemas/ingestion.py` 空文件 | [schemas/ingestion.py](src/spma/api/schemas/ingestion.py) | 按 API-05 §3.1/§4.1/§5.1/§10.1 实现全部 Pydantic 模型 |

---

## 十二、文件变更清单

```
新增文件 (5):
  src/spma/ingestion/run_store.py               PipelineRunStore
  src/spma/ingestion/synonym_map.py             SynonymMap (重写)
  src/spma/ingestion/freshness.py               FreshnessService
  src/spma/api/routes/ingestion.py              8 个 REST 端点路由
  src/spma/api/routes/ingestion_webhooks.py     2 个 Webhook 端点路由

重写文件 (4):
  src/spma/ingestion/code_pipeline.py           CodeIngestionPipeline 组装
  src/spma/ingestion/sql_pipeline.py            SqlIngestionPipeline 类封装 + 增量 diff
  src/spma/ingestion/scheduler.py               完整调度器 (doc/sql/synonym/freshness)
  src/spma/api/schemas/ingestion.py             Pydantic 模型全量实现

修改文件 (5):
  src/spma/ingestion/doc_pipeline.py            修复 upsert 调用签名
  src/spma/ingestion/code/ast_parser.py         补全 TreeSitter 调用图提取
  src/spma/retrieval/vector_store.py            新增 delete_by_source 方法
  src/spma/api/app.py                           注册路由 + startup_ingestion 事件
  src/spma/api/dependencies.py                  新增 IngestionController 单例注入
```

---

## 十三、测试策略

| 测试类型 | 覆盖范围 |
|---------|---------|
| 单元测试 | Pydantic schema 校验、PipelineRunStore CRUD、SynonymMap.lookup、ASTParser.parse_file（各语言 fixture）、SemanticChunker.split |
| 集成测试 | DocIngestionPipeline end-to-end（含 ES + PGVector testcontainers）、CodeIngestionPipeline（含 git init + TreeSitter）、SqlIngestionPipeline（含 testcontainers postgres） |
| E2E 测试 | FastAPI TestClient 遍历全部 10 个端点 + Webhook 验签/防抖 |

# OnesWiki SourceHandler 设计方案

**日期:** 2026-06-20
**状态:** 已批准
**目标:** 新增 `OneswikiSourceHandler`，接入企业内部 Ones Wiki 知识库作为文档摄入数据源。

## 1. 概述

为 SPMA 多源 RAG 系统新增 Ones Wiki 数据源支持。Ones Wiki 是企业内部的知识管理平台，提供 REST API 获取空间下的页面列表和页面内容。通过实现 `SourceHandler` 协议，将 Ones Wiki 页面纳入文档摄入管道。

## 2. Schema 变更

### 2.1 DocIngestionSource 枚举

新增枚举值：

```python
class DocIngestionSource(StrEnum):
    CONFLUENCE = "confluence"
    MARKDOWN_DIR = "markdown_dir"
    WIKI_API = "wiki_api"
    ONES_WIKI = "ones_wiki"  # 新增
```

### 2.2 DocIngestionRequest 新增 config 字段

```python
class DocIngestionRequest(BaseModel):
    source: DocIngestionSource = DocIngestionSource.CONFLUENCE
    mode: Literal["incremental", "full"] = "incremental"
    path: str | None = None
    filters: DocIngestionFilters = Field(default_factory=DocIngestionFilters)
    options: DocIngestionOptions = Field(default_factory=DocIngestionOptions)
    config: dict | None = None  # 新增：通用配置透传
```

`config` 为通用字典字段，OnesWiki 从此处读取认证和参数信息。其他 source handler 未来也可复用此字段。

## 3. OneswikiSourceHandler 架构

### 3.1 构造器

```python
class OneswikiSourceHandler:
    def __init__(self, run_store, config: dict):
        self._run_store = run_store  # PipelineRunStore，用于增量模式
        self._config = config or {}  # 全局 ingestion 配置（可选预设值）
```

### 3.2 request.config 参数

从 `request.config` 字典中读取以下参数：

| 参数 | 必填 | 说明 |
|---|---|---|
| `auth_token` | 是 | Bearer token（Authorization 头） |
| `cookie` | 是 | Cookie 字符串 |
| `team_uuid` | 是 | 团队 UUID |
| `space_uuid` | 是 | 空间 UUID |
| `parent_uuid` | 是 | 子树根节点 UUID |
| `base_url` | 否 | Ones 服务地址，默认 `https://ones.jtexpress.com.cn` |
| `concurrency` | 否 | 并发获取页面数，默认 5，设为 1 即顺序获取 |

### 3.3 fetch_documents 流程

```
fetch_documents(request)
  │
  ├── 1. 从 request.config 提取参数，校验必填字段（缺失立即报错）
  │
  ├── 2. 调用 GET /wiki/api/wiki/team/{team_uuid}/space/{space_uuid}/pages
  │       获取全量页面列表，失败则终止摄入
  │
  ├── 3. 以 parent_uuid 为根，在内存中递归构建子树
  │      找到 parent_uuid 匹配的节点，收集其所有子孙节点 UUID
  │
  ├── 4. 增量模式（request.mode == "incremental"）：
  │       从 run_store 获取上次成功摄入时间
  │       过滤 updated_time <= last_run 的页面
  │
  ├── 5. asyncio.Semaphore(concurrency) 控制并发
  │       逐个/并发调用 GET /wiki/api/wiki/team/{team}/page/{uuid}?action=view
  │       单个页面失败：记录日志 + errors，跳过继续
  │
  ├── 6. HTML content → Markdown（markdownify 库）
  │       转换异常时降级为原始 HTML 文本
  │
  └── 7. yield SourceDocument
```

### 3.4 子树递归构建算法

给定全量页面列表 `pages` 和根 `parent_uuid`：

1. 第一遍遍历：找到所有 `parent_uuid == root` 的直接子页面，收集 UUID
2. 第二遍遍历：对每个子页面，递归查找其子页面
3. 最终返回：根节点下所有子孙页面的 UUID 列表

由于树通常不深（≤5 层）、页面数不大（≤500），内存递归完全足够。

### 3.5 HTML → Markdown 转换

使用 `markdownify` 库（`pip install markdownify`）：

- `<h1>`-`<h6>` → `#`-`######`
- `<p>` → 段落文本
- `<strong>`/`<b>` → `**粗体**`
- `<em>`/`<i>` → `*斜体*`
- `<figure>` 中的 `<img>` → `![image](url)` 保留图片引用
- `<table>` → Markdown 表格
- `<ul>`/`<ol>` → Markdown 列表
- `<a href="...">` → `[text](url)`

### 3.6 SourceDocument 映射

| SourceDocument 字段 | 来源 |
|---|---|
| `text` | HTML → Markdown 转换后的内容 |
| `source_id` | 页面 `uuid` |
| `source_type` | `DocIngestionSource.ONES_WIKI` |
| `source_path` | `{base_url}/wiki/team/{team_uuid}/space/{space_uuid}/page/{uuid}` |
| `page_title` | 页面 `title` |
| `version` | `str(version)` |
| `updated_at` | `updated_time` (Unix 时间戳) 转 ISO 8601 |
| `doc_type` | `"prd"`（默认） |
| `req_ids` | `None` |

## 4. 错误处理策略

| 场景 | 行为 |
|---|---|
| `request.config` 缺少必填字段 | 抛出 `ValueError`，终止摄入 |
| 页面列表 API 失败 | 抛出异常，终止摄入 |
| 单个页面内容 API 失败 | `logger.warning` + 记录到 `errors` 列表，**跳过继续** |
| HTML 转换异常 | `logger.warning`，降级使用原始 HTML 文本 |
| 子树为空（parent_uuid 找不到） | `logger.warning`，返回空结果（不报错） |

## 5. 变更文件清单

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `src/spma/api/schemas/ingestion.py` | 修改 | +`ONES_WIKI` 枚举值, +`config` 字段 |
| `src/spma/ingestion/source_handlers/oneswiki_handler.py` | **新建** | `OneswikiSourceHandler` 完整实现 |
| `src/spma/ingestion/source_handlers/__init__.py` | 修改 | +导出 `OneswikiSourceHandler` |
| `src/spma/api/app.py` | 修改 | +注册 `"ones_wiki"` handler |
| `pyproject.toml` | 修改 | +`markdownify` 依赖 |

## 6. 注册方式

在 `app.py` 的 `create_app()` 中：

```python
from spma.ingestion.source_handlers import (
    MarkdownDirSourceHandler,
    OneswikiSourceHandler,  # 新增
)

source_handlers = {
    "markdown_dir": MarkdownDirSourceHandler(run_store, ingestion_cfg),
    "ones_wiki": OneswikiSourceHandler(run_store, ingestion_cfg),  # 新增
}
```

管道分发无需变更，`DocIngestionPipeline` 已通过 `self._handlers[request.source.value]` 自动路由。

## 7. 测试计划

### 7.1 单元测试 (`tests/unit/ingestion/test_oneswiki_handler.py`)

- **参数校验**：缺少 auth_token / cookie / team_uuid / space_uuid / parent_uuid 分别抛 ValueError
- **子树递归构建**：给定模拟页面列表，验证正确过滤子树
- **HTML 转 Markdown**：覆盖 h1-h6、粗体、斜体、图片 figure、表格、列表、链接
- **增量过滤**：给定 last_run 时间，验证正确过滤 updated_time
- **并发控制**：验证 Semaphore 限制并发数

### 7.2 集成测试 (`tests/integration/test_oneswiki_ingestion.py`)

- 完整 fetch_documents 流程（mock HTTP 响应）
- 单个页面 API 失败不中断整体摄入
- 空子树返回空结果
- 列表 API 失败终止摄入

## 8. 依赖

- `markdownify` — HTML 转 Markdown（**新增依赖**）
- `httpx` — 异步 HTTP 客户端（**项目中已有**）
- `asyncio` — 标准库，并发控制

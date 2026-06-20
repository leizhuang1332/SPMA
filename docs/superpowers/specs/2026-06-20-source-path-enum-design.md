# SourceDocument source_path & source_type Enum — Design Spec

**Date**: 2026-06-20
**Status**: Approved
**Scope**: SourceDocument 增加 source_path 字段；source_type 全面改用枚举；检索返回以 source_path 替代 source_id 展示

---

## 1. Motivation

- `source_id` 是机器可读的内部标识（SHA256 哈希、或 `{source_type}:{native_id}`），对用户不可读。用户看到 `[a3f2c8...] snippet` 无法理解来源
- `DocIngestionSource(StrEnum)` 枚举已在 `api/schemas/ingestion.py` 定义，但未被任何生产代码引用 — 所有 `source_type` 赋值都使用魔法字符串
- 需要增加 `source_path`（人类可读的文件路径或页面链接），在检索结果的引用中展示

## 2. Design Overview

### 2.1 Two Responsibilities, Two Fields

```
source_id   — 稳定的内部标识（不变）
              用途：ES/PGVector 删除、去重、精确过滤
              示例："a3f2c8e1..." / "confluence:test-001"

source_path — 人类可读的来源路径（新增）
              用途：最终展示给用户的引用标识
              示例："/home/project/docs/prd/user-auth.md"
                    "https://confluence.example.com/display/SPACE/User+Auth"
```

两者各司其职，互不干扰。

### 2.2 Data Flow

```
Ingestion (handler)
  │  source_type = DocIngestionSource.MARKDOWN_DIR
  │  source_path = str(filepath.resolve())          ← markdown: 绝对路径
  │  source_path = page_url                           ← confluence/wiki_api: URL
  │
  ▼
SourceDocument → DocChunk
  │  透传 source_path
  │
  ▼
Storage: ES mapping + source_path keyword / PGVector metadata_ JSONB
  │
  ▼
Retrieval: ES → LlamaIndex Retriever metadata["source_path"]
  │
  ▼
Generator: source_ref = source_path ?? source_id    ← 降级兼容存量数据
  │  [source_ref] snippet                            ← 替代原来的 [source_id]
```

### 2.3 Key Decisions

| 决策 | 选择 |
|------|------|
| source_path 与 source_id 关系 | 共存，职责分离 |
| markdown source_path | 文件绝对路径 |
| confluence/wiki source_path | 页面完整 URL（由 handler 从 API 提取） |
| 存量数据兼容 | generator 中 `source_path or source_id` 降级 |
| 枚举范围 | 仅改 ingestion 层（markdown_dir/confluence/wiki_api），不碰 worker 合约层（prd/code/sql） |

---

## 3. Schema Changes

### 3.1 `SourceDocument` — 新增 `source_path`，改 `source_type` 类型

```python
# src/spma/ingestion/source_handlers/base.py

from spma.api.schemas.ingestion import DocIngestionSource

@dataclass
class SourceDocument:
    text: str
    source_id: str
    source_type: DocIngestionSource    # 曾为 str
    source_path: str = ""              # 新增
    page_title: str = ""
    doc_type: str = "prd"
    version: str = ""
    req_ids: list[str] | None = None
    updated_at: str | None = None
```

### 3.2 `DocChunk` — 新增 `source_path`

```python
# src/spma/ingestion/chunkers/semantic_chunker.py

@dataclass
class DocChunk:
    chunk_id: str
    content: str
    source_id: str = ""
    source_type: str = ""
    source_path: str = ""              # 新增
    req_ids: list[str] = field(default_factory=list)
    doc_type: str = ""
    version: str = ""
    updated_at: str = ""
    chunk_index: int = 0
    page_title: str = ""
```

### 3.3 ES Mapping — 新增 `source_path` 字段

```yaml
# config/es_mapping.yaml

source_path:
  type: keyword
```

---

## 4. Source Type Enum Migration

### 4.1 Enum Definition (already exists, unchanged)

```python
# src/spma/api/schemas/ingestion.py

class DocIngestionSource(StrEnum):
    CONFLUENCE = "confluence"
    MARKDOWN_DIR = "markdown_dir"
    WIKI_API = "wiki_api"
```

### 4.2 Migration Map

| File | Line | Before | After |
|------|------|--------|-------|
| `ingestion/source_handlers/base.py` | 22 | `source_type: str` | `source_type: DocIngestionSource` |
| `ingestion/source_handlers/markdown_handler.py` | 60 | `source_type="markdown_dir"` | `source_type=DocIngestionSource.MARKDOWN_DIR` |
| `ingestion/doc_pipeline.py` | 36 | `source_type: str = "confluence"` | `source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE` |
| `ingestion/doc_pipeline.py` | 103 | `source_type: str = "confluence"` | `source_type: DocIngestionSource = DocIngestionSource.CONFLUENCE` |
| `ingestion/controller.py` | 209 | `"confluence"` | `DocIngestionSource.CONFLUENCE` |
| `ingestion/controller.py` | 228 | `"confluence"` | `DocIngestionSource.CONFLUENCE` |

### 4.3 NOT Modified

Worker 合约层的 `source_type` 是独立的语义空间（`"prd"/"code"/"sql"`），保持 `str` 不变：

- `models/worker_output.py` — `Citation.source_type: Literal["prd", "code", "sql"]`
- `models/classification.py` — `SourceType: Literal["doc", "code", "sql"]`
- `agents/supervisor/dispatcher.py` — `WORKER_TYPE_TO_SOURCE_TYPE`
- `agents/synthesis/generator.py` — 按 `"prd"/"code"/"sql"` 分组

---

## 5. Ingestion Changes

### 5.1 MarkdownHandler — 填充 source_path

```python
# src/spma/ingestion/source_handlers/markdown_handler.py

yield SourceDocument(
    text=content,
    source_id=self.make_source_id(filepath),
    source_type=DocIngestionSource.MARKDOWN_DIR,
    source_path=str(filepath.resolve()),    # 新增：绝对路径
    page_title=title,
    doc_type=request.doc_type,
    version=version,
    req_ids=req_ids,
    updated_at=updated_at,
)
```

### 5.2 Future Handlers (不在本次范围内，仅约定)

| Handler | source_path 填充方式 |
|---------|---------------------|
| ConfluenceSourceHandler | 从 API 返回的 `_links.base` + `_links.webui` 拼接完整 URL |
| WikiApiSourceHandler | 从 API 返回的 page URL 字段提取 |

---

## 6. Retrieval Changes

### 6.1 ES Client — 返回 source_path

```python
# src/spma/retrieval/es_client.py
# search() 返回的 source dict 中增加 source_path 字段
```

### 6.2 LlamaIndex Retriever — metadata 透传

```python
# src/spma/agents/doc/llamaindex_retrievers.py

metadata={
    "source_id": r.get("source_id"),
    "source_type": r.get("source_type", "bm25"),
    "source_path": r.get("source_path", ""),    # 新增
    ...
}
```

### 6.3 LlamaIndex Pipeline — 输出包含 source_path

```python
# src/spma/agents/doc/llamaindex_pipeline.py

return [
    {
        "chunk_id": n.node.node_id,
        "source_id": n.node.metadata.get("source_id"),
        "source_path": n.node.metadata.get("source_path", ""),  # 新增
        "content": n.node.get_content(),
        "score": float(n.score),
        "metadata": n.node.metadata or {},
    }
    for n in all_results[:20]
]
```

---

## 7. Generator — 用 source_path 展示

```python
# src/spma/agents/synthesis/generator.py

# Before
source_id = c.get("source_id", c.get("chunk_id", "?"))
lines.append(f"{i + 1}. [{source_id}] {snippet}")

# After
source_ref = c.get("source_path") or c.get("source_id", "?")
lines.append(f"{i + 1}. {source_ref}\n> {snippet}")
```

降级策略：存量数据没有 `source_path`，fallback 到 `source_id`。

---

## 8. Backward Compatibility

| 场景 | 处理 |
|------|------|
| 存量 ES 文档无 source_path | generator 降级到 source_id 展示 |
| 新旧代码混合部署 | source_path 为可选字段，空字符串 fallback 安全 |
| source_id 依赖逻辑 | 删除/去重/分页逻辑全部走 source_id，不受影响 |
| E2E 测试 | metadata 断言增加 source_path 字段 |

---

## 9. Testing

- ~~单测：`SourceDocument` 和 `DocChunk` 的 `source_path` 字段默认值~~ *(不需要专门的单测)*
- E2E 测试：更新 `test_doc_single_source.py`，验证检索结果 metadata 中包含 `source_path`
- 回归：所有现有 ingestion/retrieval 测试继续通过

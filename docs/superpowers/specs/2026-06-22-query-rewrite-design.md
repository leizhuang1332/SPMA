# Query Rewriter 查询重写器 — 设计规范

> 所属项目：SPMA 全局概览
> 所属模块：Supervisor Agent — Round 1 核心组件
> 设计依据：SPMA-design-01 §5.1 分类 Prompt, §6.1 抽取 Prompt, §7.1 查询改写
> 参考方案：Google Query Rewrite、Elasticsearch Query Expansion、LangChain QueryTransformer
> 编写日期：2026-06-22

---

## 1. 背景与问题

### 1.1 当前实现缺陷

当前 `rewrite_queries` 实现（`src/spma/agents/supervisor/query_rewriter.py`）存在以下问题：

| 问题 | 描述 | 影响 |
|------|------|------|
| **synonym_map 未使用** | `synonym_map` 参数传入但从未使用 | 用户用语无法映射到系统内部名，召回率下降 |
| **扩展策略过于简单** | 仅生成关键词列表直接拼接 | 无语义融合，可能产生歧义 |
| **分解容错缺失** | JSON 解析失败后返回空列表 | 跨源查询退化为单源查询 |
| **无质量评估** | 无法判断重写是否改善效果 | 缺少反馈机制 |
| **未利用对话历史** | 无 `conversation_history` 参数 | 无法处理指代消解 |

### 1.2 设计目标

| 目标 | 衡量指标 |
|------|----------|
| 提升召回率 | 检索命中率提升 ≥ 15% |
| 提升准确性 | 相关结果占比提升 ≥ 10% |
| 增强稳定性 | 重写失败率 ≤ 2% |
| 上下文感知 | 上下文相关查询处理准确率 ≥ 90% |
| 可观测性 | 可追踪每次重写效果 |

---

## 2. 整体架构

### 2.1 重写管道架构

```
┌─────────────────────────────────────────────────────────────┐
│                  Query Rewrite Pipeline                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ ① 同义词   │───▶│ ② 指代     │───▶│ ③ 查询     │     │
│  │   标准化    │    │   消解      │    │   扩展      │     │
│  └─────────────┘    └─────────────┘    └──────┬──────┘     │
│                                               │             │
│                                               ▼             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              ④ 查询分解（条件触发）                   │    │
│  │         is_cross_source=True 时执行                  │    │
│  └────────────────────────────┬────────────────────────┘    │
│                               │                             │
│                               ▼                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              ⑤ 质量评估与验证                        │    │
│  │  ├─ 语义相似度检查（每次重写调用 LLM）              │    │
│  │  ├─ 实体覆盖率检查                                  │    │
│  │  └─ 降级回退机制（质量不达标时回退）                │    │
│  └────────────────────────────┬────────────────────────┘    │
│                               │                             │
│                               ▼                             │
│                    输出重写结果字典                          │
│                    {"original": "...", "doc": "...", ...}   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 模块职责

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `_normalize_with_synonyms` | 用户用语 → 系统标准术语映射 | query, synonym_map, entities | normalized_query |
| `_resolve_references` | 指代消解（LLM 驱动，基于对话历史） | query, conversation_history, llm | resolved_query |
| `_expand_query` | 基于意图的语义扩展 | query, classification, entities, llm | expanded_query |
| `_decompose_query` | 跨源查询分解 | query, entities, sources, llm | sub_queries[] |
| `_evaluate_quality` | 重写质量评估（每次调用 LLM） | original, rewritten, llm | quality_score (0-1) |

---

## 3. 详细设计

### 3.1 主函数接口

```python
async def rewrite_queries(
    query: str,
    classification: dict,
    entities: dict,
    llm,
    synonym_map: dict | None = None,
    conversation_history: str = "",
) -> dict[str, str]:
    """
    查询重写主函数
    
    参数：
        query: 用户原始查询
        classification: 分类结果（包含 sources, is_cross_source, query_type）
        entities: 已抽取的实体
        llm: LLM 实例
        synonym_map: 同义词映射表（可选）
        conversation_history: 对话历史（可选）
    
    返回：
        dict[str, str]: 重写结果字典
            - "original": 原始查询
            - "normalized": 标准化后的查询
            - "resolved": 指代消解后的查询
            - "expanded": 扩展后的查询
            - "{source}": 面向各数据源的子查询
    """
```

### 3.2 输出格式

```python
{
    "original": "用户登录涉及哪些需求和代码",
    "normalized": "用户登录 authentication login 涉及哪些需求和代码",
    "resolved": "用户登录 authentication login 涉及哪些需求和代码",
    "expanded": "用户登录 authentication login 涉及哪些需求 REQ-XXXXX 和代码实现",
    "doc": "用户登录涉及哪些需求文档",
    "code": "用户登录 authentication login 涉及哪些代码实现",
    "sql": "用户登录涉及哪些数据库表和字段"
}
```

### 3.3 阶段一：同义词标准化

**目的**：将用户自然语言中的术语映射为系统内部的标准术语。

```python
async def _normalize_with_synonyms(
    query: str,
    synonym_map: dict | None,
    entities: dict,
) -> str:
    """同义词标准化：用户用语 → 系统标准术语"""
    if not synonym_map:
        return query
    
    normalized = query
    
    # 基于 synonym_map 的术语替换
    for user_term, system_terms in synonym_map.items():
        if user_term in normalized:
            normalized = normalized.replace(user_term, " ".join(system_terms))
    
    # 基于实体的精确映射
    entity_terms = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            entity_terms.extend(entities[key])
    
    if entity_terms:
        normalized = f"{normalized} {' '.join(entity_terms)}"
    
    return normalized.strip()
```

### 3.4 阶段二：指代消解（LLM 驱动）

**目的**：处理对话历史中的指代关系，将"这个问题"、"之前那个需求"等还原为具体内容。

**触发条件**：存在 `conversation_history` 且查询中包含指代性词汇。

```python
async def _resolve_references(
    query: str,
    conversation_history: str,
    llm,
) -> str:
    """指代消解：基于对话历史解析指代性表达式"""
    if not conversation_history:
        return query
    
    reference_patterns = ["这个", "那个", "上次", "之前", "刚才", "上述", "此"]
    has_reference = any(pattern in query for pattern in reference_patterns)
    
    if not has_reference:
        return query
    
    prompt = f"""你是一个上下文理解助手。请根据对话历史，将以下查询中的指代性表达式还原为具体内容。

对话历史：
{conversation_history}

当前查询：
{query}

要求：
1. 将"这个问题"、"那个需求"等指代性表达式替换为具体内容
2. 保持查询的核心语义不变
3. 输出还原后的完整查询，不要添加额外解释"""
    
    resp_obj = await llm.ainvoke(prompt)
    return resp_obj.content.strip()
```

### 3.5 阶段三：基于意图的查询扩展

**目的**：根据查询意图和已抽取实体，生成更丰富、更精确的查询表达式。

**触发条件**：查询长度 ≤ 50 字符 或 `query_type == "search"`

```python
async def _expand_query(
    query: str,
    classification: dict,
    entities: dict,
    llm,
) -> str:
    """基于意图的查询扩展"""
    query_type = classification.get("query_type", "search")
    
    if query_type == "search":
        prompt = f"""为以下搜索查询生成扩展查询，保留核心语义，增加相关术语和实体。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的核心语义
2. 增加相关的技术术语和实体名称
3. 输出一个扩展后的完整查询（不是关键词列表）
4. 查询长度控制在原查询的 1.5-2 倍"""
    
    elif query_type == "data_query":
        prompt = f"""将以下数据查询扩展为更精确的查询，包含表名、字段名等技术术语。

查询: {query}
已知表: {entities.get('table_names', [])}
已知字段: {entities.get('column_names', [])}
已知指标: {entities.get('metrics', [])}
要求:
1. 将中文术语转换为可能的表名/字段名
2. 保留原始查询的统计意图
3. 输出扩展后的查询"""
    
    elif query_type == "explain":
        prompt = f"""将以下解释性查询扩展为更详细的查询，增加相关的技术概念和实现细节。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的解释意图
2. 增加相关的技术概念和实现细节
3. 输出扩展后的查询"""
    
    elif query_type == "trace":
        prompt = f"""将以下追踪查询扩展为更精确的查询，包含具体的追踪路径和关联实体。

查询: {query}
已识别实体: {entities}
要求:
1. 保留原始查询的追踪意图
2. 增加具体的追踪路径和关联实体
3. 输出扩展后的查询"""
    
    else:
        return query
    
    resp_obj = await llm.ainvoke(prompt)
    expanded = resp_obj.content.strip()
    
    # 质量校验
    if await _evaluate_quality(query, expanded, llm) < 0.5:
        return query
    
    return expanded
```

### 3.6 阶段四：查询分解（跨源）

**目的**：将复杂的跨源查询分解为面向单一数据源的子查询。

**触发条件**：`is_cross_source=True` 且 `len(sources) > 1`

```python
async def _decompose_query(
    query: str,
    entities: dict,
    sources: list[str],
    llm,
) -> list[dict]:
    """跨源查询分解：多层级容错"""
    entities_str = str({k: v for k, v in entities.items() if v})
    
    prompt = f"""将以下复杂查询分解为 {len(sources)} 个独立的子查询，每个子查询面向单一数据源。

已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}

输出格式要求：
- 必须输出合法的 JSON 数组
- 每个元素包含 "query" 和 "target" 两个字段
- "target" 必须是 {', '.join(sources)} 中的一个
- 子查询应覆盖原始查询的所有核心意图

输出示例：
[{{"query": "子查询1", "target": "doc"}}, {{"query": "子查询2", "target": "code"}}]"""
    
    resp_obj = await llm.ainvoke(prompt)
    resp = resp_obj.content
    
    # 多层级解析策略
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        import re
        # 策略1：正则提取 JSON 数组
        json_match = re.search(r'\[.*\]', resp, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 策略2：提取键值对
        target_patterns = {
            source: re.search(rf'{source}[\s:]+["\']([^"\']+)["\']', resp) 
            for source in sources
        }
        result = []
        for source, pattern in target_patterns.items():
            if pattern:
                result.append({"query": pattern.group(1), "target": source})
        
        if result:
            return result
        
        # 策略3：兜底——每个 source 返回原始查询
        return [{"query": query, "target": source} for source in sources]
```

### 3.7 阶段五：质量评估

**目的**：评估重写后查询的质量，确保重写不会损害原始查询的语义。

**策略**：每次重写都调用 LLM 进行语义相似度评估。

```python
async def _evaluate_quality(
    original: str,
    rewritten: str,
    llm,
) -> float:
    """评估重写查询与原始查询的语义相似度（0-1）"""
    prompt = f"""评估以下重写查询是否保持了原始查询的核心语义。

评分标准：
- 1.0：完全一致，语义无偏差
- 0.8-0.9：略有扩展，但核心语义保持
- 0.5-0.7：有一定偏差，但仍相关
- < 0.5：语义偏差严重或完全无关

原始查询: {original}
重写查询: {rewritten}

评分(0-1):"""
    
    resp_obj = await llm.ainvoke(prompt)
    try:
        return float(resp_obj.content.strip())
    except ValueError:
        return 0.5
```

**质量门**：

| 质量评分 | 处理方式 |
|----------|----------|
| ≥ 0.7 | 接受重写结果 |
| 0.5-0.7 | 记录警告，接受结果 |
| < 0.5 | 拒绝重写，回退到原始查询 |

---

## 4. 实施计划

**实施方式**：一次性完整实现所有功能

### 实施清单

- [ ] 实现同义词标准化（`_normalize_with_synonyms`）
- [ ] 实现指代消解（`_resolve_references`）
- [ ] 实现四类意图感知扩展（`_expand_query`）
- [ ] 实现多层级容错分解（`_decompose_query`）
- [ ] 实现质量评估 + 质量门降级机制（`_evaluate_quality`）
- [ ] 实现轻量级日志（logging 模块）
- [ ] 更新 `graph.py` 调用处，传递 `synonym_map` 和 `conversation_history` 参数
- [ ] 更新 API routes 调用处

---

## 5. 日志方案（轻量级）

采用内存/日志框架方式，不创建数据库表。

### 5.1 日志格式

使用 Python `logging` 模块，每次重写操作记录：

```python
logger.info(f"Query rewrite: original={query[:50]}, "
            f"quality_score={score}, "
            f"sources={sources}, "
            f"expanded={expanded[:50] if expanded else None}")
```

### 5.2 日志内容

- 原始查询（前 50 字符）
- 质量评分
- 目标数据源
- 扩展/分解结果摘要

### 5.3 后续扩展

如需更详细的日志，可后续添加：
- 持久化到 `rewrite_logs` 表
- 人工抽检流程
- A/B 测试框架（`rewrite_strategy` 参数）

---

## 6. 调用集成

### 6.1 graph.py 改动

```python
async def rewrite_node(state: SupervisorState) -> dict:
    from spma.ingestion.synonym_map import SynonymMap
    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
        synonym_map=get_synonym_map(),
        conversation_history=state.get("conversation_history", ""),
    )
    return {"rewritten_queries": rewritten}
```

### 6.2 API routes 改动

```python
rewritten = await rewrite_queries(
    query=req.query,
    classification=classification,
    entities=entities,
    llm=llm,
    synonym_map=get_synonym_map(),
    conversation_history=req.conversation_history or "",
)
```

---

## 7. 降级路径

| 故障场景 | 降级策略 | 影响范围 |
|---------|---------|---------|
| LLM 不可用 | 跳过扩展、分解、评估，仅执行同义词标准化 | 扩展、分解、质量评估不可用 |
| synonym_map 不可用 | 跳过同义词标准化 | 标准化功能不可用 |
| 重写结果质量过低 | 回退到原始查询 | 单个查询使用原始查询 |
| JSON 解析失败 | 多层级容错：正则提取 → 默认子查询 | 查询分解使用默认策略 |
| 对话历史为空 | 跳过指代消解 | 指代消解功能不可用 |

---

## 8. 评估指标

| 指标 | 定义 | 目标值 |
|------|------|-------|
| 重写成功率 | 成功生成有效重写结果的比例 | ≥ 98% |
| 语义保持率 | 重写后查询与原查询语义一致的比例 | ≥ 90% |
| 实体覆盖率 | 重写查询包含已抽取实体的比例 | ≥ 85% |
| 召回率提升 | 重写后检索结果数量提升比例 | ≥ 15% |
| 准确性提升 | 相关结果占比提升比例 | ≥ 10% |

---

## 9. 参考资料

1. [SPMA-design-01-supervisor-agent.md](../designs/SPMA-design-01-supervisor-agent.md) — Supervisor Agent 设计
2. [SPMA-design-05-data-ingestion.md](../designs/SPMA-design-05-data-ingestion.md) — 数据摄入设计（同义词映射）
3. [src/spma/ingestion/synonym_map.py](../../src/spma/ingestion/synonym_map.py) — 同义词映射表实现
4. [src/spma/agents/supervisor/classifier.py](../../src/spma/agents/supervisor/classifier.py) — 意图分类器
5. [src/spma/agents/supervisor/entity_extractor.py](../../src/spma/agents/supervisor/entity_extractor.py) — 实体抽取器

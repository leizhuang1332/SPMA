# Design: Query Rewriter 查询重写器设计

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 所属模块：[Supervisor Agent 设计](SPMA-design-01-supervisor-agent.md) — Round 1 核心组件
> 设计依据：SPMA-design-01 §5.1 分类 Prompt, §6.1 抽取 Prompt, §7.1 查询改写
> 参考方案：Google Query Rewrite、Elasticsearch Query Expansion、LangChain QueryTransformer

---

## 模块在架构中的位置

查询重写是 Supervisor Agent Round 1 的核心步骤，位于意图分类和实体抽取之后、派发 Worker 之前：

```
用户输入
    │
    ▼
┌─────────────────────────────────────────────────────────────────┐
│ Round 1: Query Understanding (~500ms)                            │
│  ├─ 意图分类（确定需要哪些 Worker Agent）                          │
│  ├─ 实体抽取（确定在每个 Worker 里查什么）                          │
│  ├─ 查询改写（标准化 + 扩展 + 条件性分解/HyDE）◀── 本文档范围       │
│  └─ Send API 并行派发 → Doc Agent, Code Agent, SQL Agent         │
└─────────────────────────────────────────────────────────────────┘
```

**核心职责：** 将用户原始查询转换为更适合检索的查询形式，提升召回率和准确性。

---

## 一、问题分析：当前实现的缺陷

### 1.1 当前实现概览

当前 `rewrite_queries` 实现（`src/spma/agents/supervisor/query_rewriter.py`）包含三个阶段：

| 阶段 | 触发条件 | 实现逻辑 |
|------|---------|---------|
| 短查询扩展 | `len(query) <= 30` | LLM 生成 3-5 个关键词，直接拼接到原查询 |
| 跨源分解 | `is_cross_source=True` 且 `len(sources) > 1` | LLM 将复杂查询分解为面向单一数据源的子查询 |
| 兜底填充 | 未覆盖的数据源 | 使用扩展查询或原始查询填充 |

### 1.2 核心问题

#### 问题 1：synonym_map 参数完全未使用

```python
async def rewrite_queries(
    query: str,
    classification: dict,
    entities: dict,
    llm,
    synonym_map: dict | None = None,  # ⚠️ 传入但未使用
) -> dict[str, str]:
```

- **影响**：用户用语（如"登录"）无法映射到系统内部名（如"认证模块"），召回率下降
- **成熟方案对比**：Elasticsearch、Solr 均将同义词/别名映射作为查询重写的核心步骤

#### 问题 2：查询扩展策略过于简单

```python
async def _expand_query(query: str, llm) -> str:
    prompt = f"为以下用户查询生成 3-5 个相关的搜索关键词..."
    keywords = [k.strip() for k in resp.split(",") if k.strip()]
    return f"{query} {' '.join(keywords[:5])}"  # 简单拼接
```

- **缺陷**：
  - 关键词与原始查询直接拼接，没有语义融合（可能产生歧义）
  - 没有基于实体信息扩展（如将表名、字段名纳入扩展）
  - 没有考虑查询意图（`query_type`），统一用关键词扩展策略

#### 问题 3：查询分解缺乏容错和验证机制

```python
async def _decompose_query(query: str, entities: dict, sources: list[str], llm) -> list[dict]:
    ...
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        return []  # ⚠️ 失败后直接返回空列表，无降级策略
```

- **缺陷**：
  - JSON 解析失败后直接返回空列表，导致跨源查询退化为单源查询
  - 没有验证子查询是否合理（如子查询是否覆盖了原始查询的核心意图）
  - 没有考虑子查询之间的依赖关系

#### 问题 4：缺少查询重写质量评估

- 当前实现没有评估重写后查询的质量
- 无法判断重写是否改善了查询效果
- 缺少反馈机制来迭代优化重写策略

#### 问题 5：未利用对话历史

- `rewrite_queries` 没有接收 `conversation_history` 参数
- 无法基于上下文进行查询消歧和指代消解（如"这个问题"、"之前那个需求"）

---

## 二、设计目标

| 目标 | 描述 | 衡量指标 |
|------|------|---------|
| **提升召回率** | 通过同义词映射和语义扩展，覆盖更多相关结果 | 检索命中率提升 ≥ 15% |
| **提升准确性** | 通过查询分解和意图感知，生成更精准的子查询 | 相关结果占比提升 ≥ 10% |
| **增强稳定性** | 完善容错机制，避免单点失败导致整体降级 | 重写失败率 ≤ 2% |
| **上下文感知** | 支持对话历史中的指代消解和上下文理解 | 上下文相关查询处理准确率 ≥ 90% |
| **可观测性** | 提供重写质量评估和反馈机制 | 可追踪每次重写的效果 |

---

## 三、架构设计

### 3.1 重写管道架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Query Rewrite Pipeline                  │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────┐     │
│  │ 1. 同义词   │───▶│ 2. 指代     │───▶│ 3. 查询     │     │
│  │   标准化    │    │   消解      │    │   扩展      │     │
│  └─────────────┘    └─────────────┘    └──────┬──────┘     │
│                                               │             │
│                                               ▼             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              4. 查询分解（条件触发）                 │    │
│  │         is_cross_source=True 时执行                 │    │
│  └────────────────────────────┬────────────────────────┘    │
│                               │                             │
│                               ▼                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              质量评估与验证层                        │    │
│  │  ├─ 语义相似度检查（与原始查询对比）                  │    │
│  │  ├─ 实体覆盖率检查（是否覆盖已抽取实体）              │    │
│  │  └─ 降级回退机制（质量不达标时回退）                  │    │
│  └────────────────────────────┬────────────────────────┘    │
│                               │                             │
│                               ▼                             │
│                    输出重写结果字典                          │
│                    {"original": "...", "doc": "...", ...}   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 模块职责划分

| 模块 | 职责 | 输入 | 输出 |
|------|------|------|------|
| `_normalize_with_synonyms` | 用户用语 → 系统标准术语映射 | query, synonym_map, entities | normalized_query |
| `_resolve_references` | 指代消解（基于对话历史） | query, conversation_history | resolved_query |
| `_expand_query` | 基于意图的语义扩展 | query, classification, entities | expanded_query |
| `_decompose_query` | 跨源查询分解 | query, entities, sources | sub_queries[] |
| `_evaluate_quality` | 重写质量评估 | original, rewritten | quality_score (0-1) |

---

## 四、详细流程设计

### 4.1 流程总览

```
rewrite_queries(query, classification, entities, llm, synonym_map, conversation_history)
│
├─ 1. 同义词标准化
│     └─ normalized_query = _normalize_with_synonyms(query, synonym_map, entities)
│
├─ 2. 指代消解（如存在对话历史）
│     └─ resolved_query = _resolve_references(normalized_query, conversation_history, llm)
│
├─ 3. 查询扩展（条件触发）
│     └─ expanded_query = _expand_query(resolved_query, classification, entities, llm)
│
├─ 4. 查询分解（条件触发：is_cross_source=True）
│     └─ sub_queries = _decompose_query(resolved_query, entities, sources, llm)
│
├─ 5. 质量评估与验证
│     └─ 对每个重写结果执行 _evaluate_quality()
│
└─ 6. 构建输出字典
      └─ {"original": query, "normalized": ..., "doc": ..., "code": ..., "sql": ...}
```

### 4.2 阶段一：同义词标准化

**目的**：将用户自然语言中的术语映射为系统内部的标准术语，消除词汇鸿沟。

**输入**：
- `query`: 用户原始查询
- `synonym_map`: 同义词映射表（来自 `ingestion/synonym_map.py`）
- `entities`: 已抽取的实体

**处理逻辑**：

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
    
    # 1. 基于 synonym_map 的术语替换
    for user_term, system_terms in synonym_map.items():
        if user_term in normalized:
            normalized = normalized.replace(user_term, " ".join(system_terms))
    
    # 2. 基于实体的精确映射
    # 将 entities 中的 table_names、column_names、code_refs 等注入查询
    entity_terms = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            entity_terms.extend(entities[key])
    
    if entity_terms:
        normalized = f"{normalized} {' '.join(entity_terms)}"
    
    return normalized.strip()
```

**同义词映射表结构**（来自 `ingestion/synonym_map.py`）：

```python
{
    "登录": ["authentication", "login", "auth"],
    "下单": ["order", "purchase", "checkout"],
    "支付": ["payment", "pay", "transaction"],
    # ... 更多映射
}
```

### 4.3 阶段二：指代消解

**目的**：处理对话历史中的指代关系，将"这个问题"、"之前那个需求"等表达式还原为具体内容。

**触发条件**：存在 `conversation_history` 且查询中包含指代性词汇。

**输入**：
- `query`: 标准化后的查询
- `conversation_history`: 对话历史
- `llm`: LLM 实例

**处理逻辑**：

```python
async def _resolve_references(
    query: str,
    conversation_history: str,
    llm,
) -> str:
    """指代消解：基于对话历史解析指代性表达式"""
    if not conversation_history:
        return query
    
    # 检测是否包含指代性词汇
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

**示例**：

| 对话历史 | 当前查询 | 消解后查询 |
|---------|---------|-----------|
| "REQ-187 的实现方案是什么？" | "这个需求涉及哪些表？" | "REQ-187 涉及哪些表？" |
| "用户登录流程有问题" | "上次说的那个问题怎么解决？" | "用户登录流程的问题怎么解决？" |

### 4.4 阶段三：基于意图的查询扩展

**目的**：根据查询意图和已抽取实体，生成更丰富、更精确的查询表达式。

**触发条件**：
- 查询长度 ≤ 50 字符（短查询需要扩展）
- 或 `query_type == "search"`（搜索型查询需要扩展召回）

**输入**：
- `query`: 已消解指代的查询
- `classification`: 分类结果（包含 `query_type`）
- `entities`: 已抽取的实体
- `llm`: LLM 实例

**处理逻辑**：

```python
async def _expand_query(
    query: str,
    classification: dict,
    entities: dict,
    llm,
) -> str:
    """基于意图的查询扩展"""
    query_type = classification.get("query_type", "search")
    
    # 根据查询类型定制扩展策略
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
    
    # 质量校验：扩展后的查询不应与原查询完全无关
    if await _evaluate_quality(query, expanded, llm) < 0.5:
        return query
    
    return expanded
```

**扩展策略矩阵**：

| query_type | 扩展目标 | 扩展策略 |
|-----------|---------|---------|
| `search` | 提升召回率 | 增加相关术语和实体 |
| `data_query` | 精准定位表/字段 | 中文术语 → 技术术语转换 |
| `explain` | 深度理解 | 增加技术概念和实现细节 |
| `trace` | 路径追踪 | 增加追踪路径和关联实体 |

### 4.5 阶段四：查询分解（跨源）

**目的**：将复杂的跨源查询分解为面向单一数据源的子查询，提升各 Worker 的检索效率。

**触发条件**：`is_cross_source=True` 且 `len(sources) > 1`

**输入**：
- `query`: 已扩展的查询
- `entities`: 已抽取的实体
- `sources`: 目标数据源列表
- `llm`: LLM 实例

**处理逻辑**：

```python
async def _decompose_query(
    query: str,
    entities: dict,
    sources: list[str],
    llm,
) -> list[dict]:
    """跨源查询分解：将复杂查询拆分为面向单一数据源的子查询"""
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
- 子查询之间不应有重复内容

输出示例：
[{{"query": "子查询1", "target": "doc"}}, {{"query": "子查询2", "target": "code"}}]"""
    
    resp_obj = await llm.ainvoke(prompt)
    resp = resp_obj.content
    
    # 增强容错：多层级解析策略
    try:
        return json.loads(resp)
    except json.JSONDecodeError:
        # 策略1：尝试从非标准输出中提取 JSON 数组
        import re
        json_match = re.search(r'\[.*\]', resp, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        
        # 策略2：尝试提取键值对
        target_patterns = {source: re.search(rf'{source}[\s:]+["\']([^"\']+)["\']', resp) 
                           for source in sources}
        result = []
        for source, pattern in target_patterns.items():
            if pattern:
                result.append({"query": pattern.group(1), "target": source})
        
        if result:
            return result
        
        # 策略3：兜底——基于数据源生成默认子查询
        return [{"query": query, "target": source} for source in sources]
```

**分解验证规则**：

| 验证项 | 规则 | 失败处理 |
|--------|------|---------|
| JSON 格式 | 必须是合法 JSON 数组 | 尝试正则提取 → 默认子查询 |
| target 有效性 | target 必须在 sources 列表中 | 过滤无效 target |
| 意图覆盖 | 子查询集合应覆盖原始查询核心意图 | 质量评估 < 0.6 时回退 |
| 数量匹配 | 子查询数量应与 sources 数量一致 | 补充或合并子查询 |

### 4.6 阶段五：质量评估与验证

**目的**：评估重写后查询的质量，确保重写不会损害原始查询的语义。

**输入**：
- `original`: 原始查询
- `rewritten`: 重写后的查询
- `llm`: LLM 实例（可选，用于语义评估）

**处理逻辑**：

```python
async def _evaluate_quality(
    original: str,
    rewritten: str,
    llm,
) -> float:
    """评估重写查询与原始查询的语义相似度（0-1）"""
    if not llm:
        # 无 LLM 时使用简单启发式评估
        original_tokens = set(original.lower().split())
        rewritten_tokens = set(rewritten.lower().split())
        if not original_tokens:
            return 1.0
        return len(original_tokens & rewritten_tokens) / len(original_tokens)
    
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

**质量评估维度**：

| 维度 | 权重 | 评估方法 |
|------|------|---------|
| 语义相似度 | 0.6 | LLM 评估 |
| 实体覆盖率 | 0.3 | 重写查询是否包含已抽取实体 |
| 查询长度变化 | 0.1 | 长度变化不应超过 3 倍 |

**降级策略**：

```
质量评分 >= 0.7 → 接受重写结果
0.5 <= 质量评分 < 0.7 → 记录警告，接受结果
质量评分 < 0.5 → 拒绝重写，回退到原始查询
```

---

## 五、模块接口设计

### 5.1 主函数接口

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
            - "normalized": 标准化后的查询（如有）
            - "resolved": 指代消解后的查询（如有）
            - "expanded": 扩展后的查询（如有）
            - "{source}": 面向各数据源的子查询（如 doc, code, sql）
    """
```

### 5.2 输出格式规范

```python
{
    "original": "用户登录涉及哪些需求和代码",
    "normalized": "用户登录 authentication login 涉及哪些需求和代码",
    "resolved": "用户登录 authentication login 涉及哪些需求和代码",  # 无指代时与 normalized 相同
    "expanded": "用户登录 authentication login 涉及哪些需求 REQ-XXXXX 和代码实现",
    "doc": "用户登录涉及哪些需求文档",
    "code": "用户登录 authentication login 涉及哪些代码实现",
    "sql": "用户登录涉及哪些数据库表和字段"
}
```

### 5.3 与上游模块的接口

**上游调用**（`supervisor/graph.py`）：

```python
async def rewrite_node(state: SupervisorState) -> dict:
    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
        synonym_map=get_synonym_map(),  # 新增：从 ingestion 模块获取
        conversation_history=state.get("conversation_history", ""),  # 新增
    )
    return {"rewritten_queries": rewritten}
```

**上游调用**（`api/routes/query.py`）：

```python
rewritten = await rewrite_queries(
    query=req.query,
    classification=classification,
    entities=entities,
    llm=llm,
    synonym_map=get_synonym_map(),  # 新增
    conversation_history=req.conversation_history or "",  # 新增
)
```

---

## 六、质量保障

### 6.1 评估指标

| 指标 | 定义 | 目标值 |
|------|------|-------|
| 重写成功率 | 成功生成有效重写结果的比例 | ≥ 98% |
| 语义保持率 | 重写后查询与原查询语义一致的比例 | ≥ 90% |
| 实体覆盖率 | 重写查询包含已抽取实体的比例 | ≥ 85% |
| 召回率提升 | 重写后检索结果数量提升比例 | ≥ 15% |
| 准确性提升 | 相关结果占比提升比例 | ≥ 10% |

### 6.2 评估方法

**离线评估**：

1. 构建测试集：100 条真实用户查询，人工标注期望的重写结果
2. 运行重写器，对比自动重写结果与人工标注
3. 计算语义保持率和实体覆盖率

**在线评估**：

1. A/B 测试：对部分流量使用新重写器，对比检索效果
2. 监控指标：重写成功率、Worker 返回结果数量、用户满意度评分

### 6.3 持续改进机制

```
用户查询 → 重写器 → Worker → 结果
                              │
                              ▼
                         结果质量评估
                              │
                    ┌─────────┴─────────┐
                    │ YES              │ NO
                    ▼                  ▼
               记录成功案例      记录失败案例
                    │                  │
                    └─────────┬─────────┘
                              ▼
                         定期 review
                              │
                    ┌─────────┴─────────┐
                    │                   │
                    ▼                   ▼
               优化 Prompt        更新同义词映射
```

---

## 七、降级路径

| 故障场景 | 降级策略 | 影响范围 |
|---------|---------|---------|
| LLM 不可用 | 跳过扩展和分解，仅执行同义词标准化 | 扩展和分解功能不可用 |
| synonym_map 不可用 | 跳过同义词标准化 | 标准化功能不可用 |
| 重写结果质量过低 | 回退到原始查询 | 单个查询使用原始查询 |
| JSON 解析失败 | 多层级容错：正则提取 → 默认子查询 | 查询分解使用默认策略 |
| 对话历史为空 | 跳过指代消解 | 指代消解功能不可用 |

---

## 八、与市面成熟方案对比

| 方案 | 核心技术 | 适用场景 | SPMA 借鉴点 |
|------|----------|----------|------------|
| **Google Query Rewrite** | 语义理解 + 用户搜索模式挖掘 | Web 搜索 | 基于意图的扩展策略 |
| **Elasticsearch Query Expansion** | 同义词映射 + 模糊查询 + 停用词处理 | 企业搜索 | 同义词标准化模块 |
| **LangChain QueryTransformer** | LLM 语义扩展 + 结构化输出 | RAG 系统 | LLM 驱动的查询转换 |
| **IBM Watson Discovery** | 查询分解 + 语义验证 + 上下文感知 | 企业知识库 | 查询分解验证机制 |
| **Microsoft Azure AI Search** | 多语言支持 + 同义词映射 + 拼写纠正 | 云搜索服务 | 多语言扩展能力 |

---

## 九、实施计划

### Phase 1：基础优化（1-2 周）

- [ ] 集成 `synonym_map` 参数，实现同义词标准化
- [ ] 增强 `_decompose_query` 的容错机制
- [ ] 增加 `conversation_history` 参数支持

### Phase 2：质量提升（2-3 周）

- [ ] 实现基于意图的查询扩展策略
- [ ] 实现重写质量评估模块
- [ ] 实现指代消解功能

### Phase 3：持续优化（持续）

- [ ] 构建重写效果评估测试集
- [ ] 实现 A/B 测试框架
- [ ] 建立反馈驱动的迭代优化机制

---

## 十、参考资料

1. [SPMA-design-01-supervisor-agent.md](SPMA-design-01-supervisor-agent.md) — Supervisor Agent 设计
2. [SPMA-design-05-data-ingestion.md](SPMA-design-05-data-ingestion.md) — 数据摄入设计（同义词映射）
3. [src/spma/ingestion/synonym_map.py](../src/spma/ingestion/synonym_map.py) — 同义词映射表实现
4. [src/spma/agents/supervisor/classifier.py](../src/spma/agents/supervisor/classifier.py) — 意图分类器
5. [src/spma/agents/supervisor/entity_extractor.py](../src/spma/agents/supervisor/entity_extractor.py) — 实体抽取器
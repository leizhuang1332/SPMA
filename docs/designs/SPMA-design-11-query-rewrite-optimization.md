# Design: Query Rewriter 优化方案

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 所属模块：[Supervisor Agent 设计](SPMA-design-01-supervisor-agent.md) — Round 1 核心组件
> 设计依据：SPMA-design-10-query-rewrite.md 优化升级
> 参考方案：Dify Query Rewrite、QAnything Multi-Route Expansion、Cohere Rerank、RAGAs

---

## 一、问题回顾：当前实现的核心缺陷

| 缺陷 | 影响 | 严重程度 |
|------|------|---------|
| **synonym_map 未启用** | 用户用语无法映射到系统标准术语，召回率下降 | 高 |
| **指代消解触发条件简单** | 仅关键词匹配，易漏判/误判 | 中 |
| **缺乏查询缓存** | 每次请求都调用LLM，成本高、延迟大 | 高 |
| **扩展阈值不灵活** | 固定阈值50，无法自适应调整 | 中 |
| **分解策略不稳定** | 依赖LLM输出，缺乏规则兜底 | 中 |
| **缺乏学习机制** | 无法从历史反馈中优化重写策略 | 中 |

---

## 二、设计方案对比

### 方案总览

| 维度 | 方案 A：规则+LLM混合架构 | 方案 B：语义缓存+自适应策略 | 方案 C：多策略集成+反馈闭环 |
|------|------------------------|--------------------------|---------------------------|
| **核心思路** | 规则优先，LLM作为增强 | 缓存优先，自适应阈值 | 多路策略投票+离线评估反馈 |
| **synonym_map** | 完全启用+增量更新 | 完全启用+预热缓存 | 完全启用+代理维护 |
| **缓存机制** | 基础结果缓存 | 语义级缓存+TTL | 分层缓存+预热 |
| **指代消解** | 规则+LLM混合触发 | LLM语义分析+缓存 | 多路指代消解+投票 |
| **扩展策略** | 意图感知+规则阈值 | 自适应阈值+质量反馈 | 多策略并行+质量评估 |
| **分解策略** | 规则模板+LLM增强 | LLM分解+规则验证 | 多策略分解+一致性校验 |
| **学习机制** | 命中计数驱动更新 | 反馈驱动自适应 | 离线评估+策略进化 |
| **实施复杂度** | 中 | 中高 | 高 |
| **预期收益** | 召回率+15%，延迟-30% | 召回率+20%，延迟-50% | 召回率+25%，延迟-40% |

---

## 方案 A：规则+LLM 混合架构（推荐）

### 设计理念

> **规则优先，LLM 增强**：将确定性逻辑（同义词映射、简单指代）通过规则实现，LLM 专注于语义理解和复杂推理。

### 迁移路径

```
当前状态：synonym_map=None, 无缓存, 简单关键词指代, 固定阈值50
         ↓
Phase 1: 激活 synonym_map（修改 graph.py 和 query.py 调用点）
         ↓
Phase 2: 添加规则+LLM混合指代消解
         ↓
Phase 3: 添加基础查询缓存
         ↓
Phase 4: 实现自适应扩展阈值
         ↓
Phase 5: 添加规则模板分解
         ↓
目标状态：完整的规则+LLM混合架构
```

### 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                    Query Rewrite Pipeline v2.0                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────────┐    ┌──────────────────┐                  │
│  │ 1. 同义词标准化   │───▶│ 2. 指代消解      │                  │
│  │  (规则引擎)      │    │  (规则+LLM混合)   │                  │
│  └────────┬─────────┘    └────────┬─────────┘                  │
│           │                       │                             │
│           ▼                       ▼                             │
│  ┌─────────────────────────────────────────────────────┐       │
│  │               查询缓存层 (Query Cache)               │       │
│  │  ├─ 精确匹配缓存 (Exact Match)                       │       │
│  │  └─ 语义相似缓存 (Semantic Similarity)               │       │
│  └──────────────────┬──────────────────────────────────┘       │
│                     │ 缓存未命中                                │
│                     ▼                                          │
│  ┌─────────────────────────────────────────────────────┐       │
│  │ 3. 查询扩展（意图感知+自适应阈值）                    │       │
│  │    - search → 术语扩展（1.5-2倍）                    │       │
│  │    - data_query → 技术术语转换                       │       │
│  │    - explain → 概念扩展                              │       │
│  │    - trace → 路径扩展                                │       │
│  └──────────────────┬──────────────────────────────────┘       │
│                     │                                          │
│                     ▼                                          │
│  ┌─────────────────────────────────────────────────────┐       │
│  │ 4. 查询分解（规则模板+LLM增强）                      │       │
│  │    - 规则模板：预定义分解模式                         │       │
│  │    - LLM增强：复杂查询的智能分解                     │       │
│  │    - 验证层：确保子查询覆盖核心意图                   │       │
│  └──────────────────┬──────────────────────────────────┘       │
│                     │                                          │
│                     ▼                                          │
│  ┌─────────────────────────────────────────────────────┐       │
│  │ 5. 质量评估与验证                                    │       │
│  │    - 语义相似度检查                                  │       │
│  │    - 实体覆盖率检查                                  │       │
│  │    - 降级回退机制                                    │       │
│  └─────────────────────────────────────────────────────┘       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 核心改进点

#### 1. 同义词标准化完全启用

**设计变更**：
- `graph.py` 和 `query.py` 调用时传入实际的 `synonym_map`
- 从 `SynonymMap` 类获取活跃映射并转换为字典格式

**实现逻辑**：

```python
async def get_active_synonym_map(db_pool: asyncpg.Pool) -> dict:
    """获取所有活跃的同义词映射，转换为字典格式"""
    syn_map = SynonymMap(db_pool)
    result = await syn_map.query(status="active", limit=1000)
    mapping = {}
    for entry in result["entries"]:
        user_term = entry["user_term"]
        canonical_term = entry["canonical_term"]
        if user_term not in mapping:
            mapping[user_term] = []
        mapping[user_term].append(canonical_term)
    return mapping
```

**调用点修改**（`graph.py`）：

```python
async def rewrite_node(state: SupervisorState) -> dict:
    db_pool = get_db_pool()
    synonym_map = await get_active_synonym_map(db_pool)
    
    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
        synonym_map=synonym_map,
        conversation_history=state.get("conversation_history", ""),
    )
    return {"rewritten_queries": rewritten}
```

**调用点修改**（`api/routes/query.py`）：

```python
db_pool = get_db_pool()
synonym_map = await get_active_synonym_map(db_pool)

rewritten = await rewrite_queries(
    query=req.query,
    classification=classification,
    entities=entities,
    llm=llm,
    synonym_map=synonym_map,
    conversation_history=req.conversation_history or "",
)
```

#### 2. 规则+LLM 混合指代消解

**设计变更**：
- 规则层：基于词性标注和实体匹配的指代检测
- LLM层：规则无法处理时的语义分析

**实现逻辑**：

```python
async def _resolve_references(
    query: str,
    conversation_history: str,
    llm,
    entities: dict,
) -> str:
    """规则+LLM混合指代消解"""
    if not conversation_history:
        return query
    
    resolved = _rule_based_reference_resolution(query, conversation_history, entities)
    
    if resolved == query:
        resolved = await _llm_based_reference_resolution(query, conversation_history, llm)
    
    return resolved


def _rule_based_reference_resolution(
    query: str,
    conversation_history: str,
    entities: dict,
) -> str:
    """基于规则的指代消解"""
    resolved = query
    
    entity_types = {
        "需求": entities.get("req_ids", []),
        "表": entities.get("table_names", []),
        "字段": entities.get("column_names", []),
        "模块": entities.get("code_refs", []),
    }
    
    for pattern, entity_list in entity_types.items():
        if entity_list:
            resolved = resolved.replace(f"这个{pattern}", entity_list[0])
            resolved = resolved.replace(f"那个{pattern}", entity_list[-1])
    
    if "它" in resolved or "该" in resolved:
        recent_entity = _extract_recent_entity(conversation_history)
        if recent_entity:
            resolved = resolved.replace("它", recent_entity)
            resolved = resolved.replace("该", recent_entity)
    
    return resolved
```

#### 3. 查询结果缓存

**设计变更**：
- 添加精确匹配缓存（基于查询哈希）
- 添加语义相似缓存（基于Embedding相似度）
- 支持TTL自动过期

**实现逻辑**：

```python
class QueryRewriteCache:
    """查询重写结果缓存"""
    
    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        self._exact_cache = {}
        self._semantic_cache = []
        self._max_size = max_size
        self._ttl = ttl_seconds
    
    async def get(self, query: str, embedding_model) -> dict | None:
        query_hash = hash(query)
        if query_hash in self._exact_cache:
            result, timestamp = self._exact_cache[query_hash]
            if time.time() - timestamp < self._ttl:
                return result
        
        query_embedding = await embedding_model.embed_query(query)
        for cached_embedding, _, cached_result, timestamp in self._semantic_cache:
            similarity = cosine_similarity([query_embedding], [cached_embedding])[0][0]
            if similarity > 0.9 and time.time() - timestamp < self._ttl:
                return cached_result
        
        return None
    
    async def set(self, query: str, result: dict, embedding_model) -> None:
        query_hash = hash(query)
        self._exact_cache[query_hash] = (result, time.time())
        
        query_embedding = await embedding_model.embed_query(query)
        self._semantic_cache.append((query_embedding, query, result, time.time()))
        
        self._cleanup()
    
    def _cleanup(self):
        now = time.time()
        self._exact_cache = {
            k: v for k, v in self._exact_cache.items()
            if now - v[1] < self._ttl
        }
        self._semantic_cache = [
            item for item in self._semantic_cache
            if now - item[3] < self._ttl
        ][-self._max_size:]
```

#### 4. 自适应扩展阈值

**设计变更**：
- 基于查询复杂度动态调整扩展阈值
- 基于历史反馈优化扩展策略

**实现逻辑**：

```python
async def _should_expand(
    query: str,
    query_type: str,
    entities: dict,
    history_stats: dict,
) -> bool:
    if query_type == "search":
        return True
    
    complexity = _calculate_query_complexity(query, entities)
    
    if history_stats.get(query, {}).get("quality", 1.0) < 0.7:
        return True
    
    if complexity < 3:
        return True
    elif complexity < 5:
        return len(query) <= 80
    else:
        return False


def _calculate_query_complexity(query: str, entities: dict) -> int:
    complexity = 1
    
    entity_count = sum(len(v) for v in entities.values() if v)
    complexity += min(entity_count, 3)
    
    if len(query) > 50:
        complexity += 2
    if len(query) > 100:
        complexity += 2
    
    keywords = ["什么", "如何", "为什么", "哪个", "哪些", "是否", "怎样"]
    keyword_count = sum(1 for k in keywords if k in query)
    complexity += keyword_count
    
    return min(complexity, 10)
```

#### 5. 规则模板+LLM 增强的查询分解

**设计变更**：
- 添加预定义的分解规则模板
- LLM仅处理规则无法覆盖的复杂查询
- 添加子查询验证层

**实现逻辑**：

```python
async def _decompose_query(
    query: str,
    entities: dict,
    sources: list[str],
    llm,
) -> list[dict]:
    template_result = _template_based_decomposition(query, entities, sources)
    if template_result:
        return template_result
    
    return await _llm_based_decomposition(query, entities, sources, llm)


def _template_based_decomposition(
    query: str,
    entities: dict,
    sources: list[str],
) -> list[dict] | None:
    sub_queries = []
    
    if "涉及哪些" in query and "和" in query:
        parts = query.split("和")
        if len(parts) == 2:
            for source in sources:
                sub_query = query.replace("和", f"，面向{source}的")
                sub_queries.append({"query": sub_query, "target": source})
            return sub_queries
    
    entity_types_found = []
    for key in ["table_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            entity_types_found.append(key)
    
    if len(entity_types_found) >= 2:
        for source in sources:
            sub_queries.append({"query": query, "target": source})
        return sub_queries
    
    return None
```

### 实施计划

| 阶段 | 任务 | 时间 | 关键文件 |
|------|------|------|---------|
| Phase 1 | 启用 synonym_map，修改调用点 | 1周 | `graph.py`, `query.py` |
| Phase 2 | 实现规则+LLM混合指代消解 | 1周 | `query_rewriter.py` |
| Phase 3 | 添加查询结果缓存 | 1周 | `query_rewriter.py` |
| Phase 4 | 实现自适应扩展阈值 | 1周 | `query_rewriter.py` |
| Phase 5 | 实现规则模板分解 | 1周 | `query_rewriter.py` |

---

## 方案 B：语义缓存+自适应策略

### 设计理念

> **缓存优先，自适应优化**：通过语义级缓存大幅减少LLM调用，基于历史反馈自适应调整重写策略。

### 迁移路径

```
当前状态：无缓存, 固定阈值50, 无反馈机制
         ↓
Phase 1: 激活 synonym_map（同方案A）
         ↓
Phase 2: 实现语义级查询缓存（向量数据库存储）
         ↓
Phase 3: 实现反馈驱动的自适应策略
         ↓
Phase 4: 增强质量评估模块
         ↓
Phase 5: 实现策略进化机制
         ↓
目标状态：语义缓存+自适应策略架构
```

### 核心改进点

#### 1. 语义级查询缓存

**设计变更**：
- 使用向量数据库存储查询向量和重写结果
- 基于Embedding相似度匹配缓存
- 支持缓存预热和定期更新

**借鉴 Dify Query Rewrite**：
- Dify 使用 Step-Back Prompting 技术，将简单问题转化为更通用的问题
- 借鉴其"问题泛化+语义缓存"的思路，对相似查询复用重写结果

**实现逻辑**：

```python
class SemanticQueryCache:
    """语义级查询缓存"""
    
    def __init__(self, vector_store, embedding_model, threshold: float = 0.9):
        self._vector_store = vector_store
        self._embedding_model = embedding_model
        self._threshold = threshold
    
    async def get(self, query: str) -> dict | None:
        query_embedding = await self._embedding_model.embed_query(query)
        results = await self._vector_store.search(query_embedding, top_k=3)
        
        for result in results:
            if result["score"] > self._threshold:
                return result["metadata"]["rewritten_query"]
        
        return None
    
    async def set(self, query: str, rewritten_query: dict) -> None:
        query_embedding = await self._embedding_model.embed_query(query)
        await self._vector_store.add(
            embedding=query_embedding,
            metadata={"query": query, "rewritten_query": rewritten_query},
        )
    
    async def warm_up(self, queries: list[str]) -> None:
        """缓存预热"""
        for query in queries:
            if not await self.get(query):
                pass
```

#### 2. 反馈驱动的自适应策略

**设计变更**：
- 收集重写质量反馈
- 基于反馈动态调整扩展阈值和分解策略
- 实现策略进化机制

**借鉴 QAnything**：
- QAnything 使用多路召回策略，对同一查询生成多种扩展形式
- 借鉴其"多策略并行+质量评估"的思路，动态选择最佳策略

**实现逻辑**：

```python
class AdaptiveRewriteStrategy:
    """自适应重写策略"""
    
    def __init__(self):
        self._stats = {
            "expand_success_rate": 0.7,
            "decompose_success_rate": 0.8,
            "reference_resolution_success_rate": 0.85,
        }
        self._history = []
    
    async def should_expand(self, query: str, query_type: str) -> bool:
        if query_type == "search":
            return True
        
        success_rate = self._stats.get("expand_success_rate", 0.7)
        base_threshold = 50
        adjusted_threshold = base_threshold + (1 - success_rate) * 50
        
        return len(query) <= adjusted_threshold
    
    def record_feedback(self, query: str, strategy: str, quality: float) -> None:
        self._history.append({
            "query": query,
            "strategy": strategy,
            "quality": quality,
            "timestamp": time.time(),
        })
        
        recent = [h for h in self._history[-100:] if h["strategy"] == strategy]
        if recent:
            success_count = sum(1 for h in recent if h["quality"] >= 0.7)
            self._stats[f"{strategy}_success_rate"] = success_count / len(recent)
    
    def evolve_strategy(self) -> None:
        """策略进化"""
        # 根据历史数据优化策略参数
        pass
```

#### 3. 质量评估增强

**设计变更**：
- 多维度质量评估（语义相似度、实体覆盖率、查询长度变化）
- 引入用户反馈作为评估因子
- 实现评估模型的持续优化

**借鉴 RAGAs**：
- RAGAs 提供多维度的检索质量评估指标
- 借鉴其"上下文相关性评分"的思路，构建多维度质量评估体系

**实现逻辑**：

```python
async def _evaluate_quality(
    original: str,
    rewritten: str,
    llm,
    entities: dict,
) -> float:
    scores = []
    
    semantic_score = await _evaluate_semantic_similarity(original, rewritten, llm)
    scores.append(semantic_score * 0.6)
    
    entity_score = _evaluate_entity_coverage(rewritten, entities)
    scores.append(entity_score * 0.3)
    
    length_score = _evaluate_length_change(original, rewritten)
    scores.append(length_score * 0.1)
    
    return sum(scores)


def _evaluate_entity_coverage(rewritten: str, entities: dict) -> float:
    all_entities = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            all_entities.extend(entities[key])
    
    if not all_entities:
        return 1.0
    
    covered_count = sum(1 for e in all_entities if e.lower() in rewritten.lower())
    return covered_count / len(all_entities)


def _evaluate_length_change(original: str, rewritten: str) -> float:
    original_len = len(original)
    rewritten_len = len(rewritten)
    
    if original_len == 0:
        return 1.0
    
    ratio = rewritten_len / original_len
    
    if 0.5 <= ratio <= 3:
        return 1.0
    elif ratio < 0.5:
        return ratio * 2
    else:
        return 3 / ratio
```

### 实施计划

| 阶段 | 任务 | 时间 | 关键文件 |
|------|------|------|---------|
| Phase 1 | 激活 synonym_map | 1周 | `graph.py`, `query.py` |
| Phase 2 | 实现语义级查询缓存 | 1.5周 | `query_rewriter.py` |
| Phase 3 | 实现反馈驱动的自适应策略 | 1.5周 | `query_rewriter.py` |
| Phase 4 | 增强质量评估模块 | 1周 | `query_rewriter.py` |
| Phase 5 | 实现策略进化机制 | 1周 | `query_rewriter.py` |

---

## 方案 C：多策略集成+反馈闭环

### 设计理念

> **多路策略，择优而取**：对同一查询并行应用多种重写策略，通过质量评估选择最优结果，结合离线评估实现策略持续进化。

### 迁移路径

```
当前状态：单一路径重写, 无策略选择机制, 无反馈闭环
         ↓
Phase 1: 激活 synonym_map（同方案A）
         ↓
Phase 2: 实现多策略并行扩展（多路Expander）
         ↓
Phase 3: 实现多路指代消解+投票选择
         ↓
Phase 4: 实现多策略分解+一致性校验
         ↓
Phase 5: 实现离线评估+策略进化
         ↓
目标状态：多策略集成+反馈闭环架构
```

### 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                 Multi-Strategy Query Rewrite                    │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    ┌─────────────────────────────────────┐    │
│  │ 输入查询    │───▶│ 多路指代消解策略                      │    │
│  └─────────────┘    │  ├─ 规则指代消解                     │    │
│                     │  ├─ LLM语义消解                      │    │
│                     │  └─ 实体匹配消解                     │    │
│                     └─────────────┬───────────────────────┘    │
│                                   │                             │
│                                   ▼                             │
│                     ┌─────────────────────────────────────┐    │
│                     │ 多路扩展策略                         │    │
│                     │  ├─ 意图感知扩展                     │    │
│                     │  ├─ 同义词扩展                       │    │
│                     │  ├─ 实体注入扩展                     │    │
│                     │  └─ 上下文感知扩展                   │    │
│                     └─────────────┬───────────────────────┘    │
│                                   │                             │
│                                   ▼                             │
│                     ┌─────────────────────────────────────┐    │
│                     │ 多路分解策略                         │    │
│                     │  ├─ 规则模板分解                     │    │
│                     │  ├─ LLM智能分解                      │    │
│                     │  └─ 实体导向分解                     │    │
│                     └─────────────┬───────────────────────┘    │
│                                   │                             │
│                                   ▼                             │
│                     ┌─────────────────────────────────────┐    │
│                     │ 质量评估与策略选择                   │    │
│                     │  ├─ 语义相似度评分                   │    │
│                     │  ├─ 实体覆盖率评分                   │    │
│                     │  ├─ 结果一致性校验                   │    │
│                     │  └─ 最佳策略选择                     │    │
│                     └─────────────┬───────────────────────┘    │
│                                   │                             │
│                                   ▼                             │
│                     ┌─────────────────────────────────────┐    │
│                     │ 反馈闭环与策略进化                   │    │
│                     │  ├─ 离线评估数据集                   │    │
│                     │  ├─ 策略效果统计                     │    │
│                     │  ├─ 用户反馈收集                     │    │
│                     │  └─ 策略权重调整                     │    │
│                     └─────────────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 核心改进点

#### 1. 多路指代消解策略

**设计变更**：
- 并行应用多种指代消解策略
- 通过投票机制选择最佳结果
- 减少单一策略的局限性

**借鉴 Dify Step-Back Prompting**：
- Dify 将简单问题转化为更通用的问题，再逐步具体化
- 借鉴其"问题泛化→具体化"的思路，实现多层次指代消解

**实现逻辑**：

```python
class MultiStrategyReferenceResolver:
    """多路指代消解策略"""
    
    def __init__(self, llm):
        self._strategies = [
            self._rule_based_resolution,
            self._entity_based_resolution,
            self._llm_semantic_resolution,
        ]
    
    async def resolve(self, query: str, conversation_history: str, entities: dict) -> str:
        """多路指代消解+投票选择"""
        if not conversation_history:
            return query
        
        results = []
        for strategy in self._strategies:
            try:
                result = await strategy(query, conversation_history, entities)
                if result != query:
                    results.append(result)
            except Exception:
                continue
        
        if not results:
            return query
        
        return self._vote_best(query, results)
    
    def _vote_best(self, original: str, candidates: list[str]) -> str:
        """基于语义相似度投票选择最佳结果"""
        if len(candidates) == 1:
            return candidates[0]
        
        return max(candidates, key=lambda c: self._calculate_similarity(original, c))
    
    def _calculate_similarity(self, s1: str, s2: str) -> float:
        s1_tokens = set(s1.lower().split())
        s2_tokens = set(s2.lower().split())
        if not s1_tokens:
            return 1.0
        return len(s1_tokens & s2_tokens) / len(s1_tokens)
```

#### 2. 多路扩展策略

**设计变更**：
- 并行应用多种扩展策略（意图感知、同义词、实体注入、上下文感知）
- 通过质量评估选择最佳扩展结果
- 减少单一扩展策略的不确定性

**借鉴 QAnything Multi-Route Expansion**：
- QAnything 对同一查询生成多种扩展形式，并行检索后融合结果
- 借鉴其"多路扩展+并行检索"的思路，实现多路扩展策略

**实现逻辑**：

```python
class MultiStrategyExpander:
    """多路扩展策略"""
    
    def __init__(self, llm, synonym_map):
        self._strategies = {
            "intent_aware": self._expand_intent_aware,
            "synonym_based": self._expand_synonym_based,
            "entity_injection": self._expand_entity_injection,
            "context_aware": self._expand_context_aware,
        }
        self._llm = llm
        self._synonym_map = synonym_map
    
    async def expand(self, query: str, classification: dict, entities: dict) -> str:
        """多路扩展+质量评估选择"""
        query_type = classification.get("query_type", "search")
        
        if query_type not in ["search", "data_query", "explain", "trace"]:
            return query
        
        candidates = []
        for strategy_name, strategy_func in self._strategies.items():
            try:
                expanded = await strategy_func(query, classification, entities)
                if expanded != query:
                    candidates.append((strategy_name, expanded))
            except Exception:
                continue
        
        if not candidates:
            return query
        
        best_strategy, best_expanded = await self._select_best(query, candidates, entities)
        return best_expanded
    
    async def _select_best(self, original: str, candidates: list[tuple], entities: dict) -> tuple:
        """基于质量评估选择最佳扩展"""
        best_score = 0
        best_candidate = candidates[0]
        
        for strategy_name, expanded in candidates:
            score = await _evaluate_quality(original, expanded, self._llm, entities)
            if score > best_score:
                best_score = score
                best_candidate = (strategy_name, expanded)
        
        return best_candidate
```

#### 3. 多策略分解+一致性校验

**设计变更**：
- 并行应用多种分解策略（规则模板、LLM智能、实体导向）
- 添加一致性校验层，确保子查询覆盖核心意图
- 减少分解结果的不确定性

**借鉴 Cohere Rerank**：
- Cohere Rerank 对检索结果进行重排序，提高相关性
- 借鉴其"查询-文档对重排序"的思路，对分解后的子查询进行一致性校验

**实现逻辑**：

```python
class MultiStrategyDecomposer:
    """多策略分解+一致性校验"""
    
    def __init__(self, llm):
        self._strategies = [
            self._template_based_decomposition,
            self._llm_based_decomposition,
            self._entity_guided_decomposition,
        ]
    
    async def decompose(self, query: str, entities: dict, sources: list[str]) -> list[dict]:
        """多策略分解+一致性校验"""
        results = []
        for strategy in self._strategies:
            try:
                sub_queries = await strategy(query, entities, sources)
                if sub_queries:
                    results.append(sub_queries)
            except Exception:
                continue
        
        if not results:
            return [{"query": query, "target": source} for source in sources]
        
        return self._consensus_check(query, results, sources)
    
    def _consensus_check(self, original: str, results: list[list[dict]], sources: list[str]) -> list[dict]:
        """一致性校验：选择覆盖最广的子查询集合"""
        merged = {}
        for sub_queries in results:
            for sq in sub_queries:
                target = sq["target"]
                if target not in merged:
                    merged[target] = []
                merged[target].append(sq["query"])
        
        final = []
        for source in sources:
            if source in merged and merged[source]:
                final.append({"query": merged[source][0], "target": source})
            else:
                final.append({"query": original, "target": source})
        
        return final
```

#### 4. 离线评估+策略进化

**设计变更**：
- 构建离线评估数据集
- 定期评估各策略效果
- 基于评估结果调整策略权重
- 实现策略持续进化

**实现逻辑**：

```python
class StrategyEvaluator:
    """离线评估+策略进化"""
    
    def __init__(self, llm):
        self._llm = llm
        self._strategy_weights = {
            "rule_based": 0.3,
            "llm_based": 0.4,
            "entity_guided": 0.3,
        }
        self._evaluation_history = []
    
    async def evaluate_strategies(self, test_cases: list[dict]) -> dict:
        """评估各策略效果"""
        results = {}
        
        for strategy_name, weight in self._strategy_weights.items():
            scores = []
            for case in test_cases:
                original = case["original"]
                expected = case["expected"]
                rewritten = case["rewritten"]
                
                score = await _evaluate_quality(original, rewritten, self._llm, {})
                scores.append(score)
            
            avg_score = sum(scores) / len(scores) if scores else 0
            results[strategy_name] = {
                "weight": weight,
                "avg_score": avg_score,
                "count": len(scores),
            }
        
        return results
    
    def update_strategy_weights(self, evaluation_results: dict) -> None:
        """基于评估结果调整策略权重"""
        total_score = sum(
            result["avg_score"] * result["weight"]
            for result in evaluation_results.values()
        )
        
        if total_score == 0:
            return
        
        for strategy_name, result in evaluation_results.items():
            new_weight = result["avg_score"] * result["weight"] / total_score
            self._strategy_weights[strategy_name] = new_weight
        
        self._normalize_weights()
    
    def _normalize_weights(self) -> None:
        """归一化权重"""
        total = sum(self._strategy_weights.values())
        if total > 0:
            for key in self._strategy_weights:
                self._strategy_weights[key] /= total
```

### 实施计划

| 阶段 | 任务 | 时间 | 关键文件 |
|------|------|------|---------|
| Phase 1 | 激活 synonym_map | 1周 | `graph.py`, `query.py` |
| Phase 2 | 实现多路指代消解策略 | 1.5周 | `query_rewriter.py` |
| Phase 3 | 实现多路扩展策略 | 1.5周 | `query_rewriter.py` |
| Phase 4 | 实现多策略分解+一致性校验 | 1.5周 | `query_rewriter.py` |
| Phase 5 | 实现离线评估+策略进化 | 2周 | `query_rewriter.py` |

---

## 三、方案对比与推荐

### 方案对比表

| 维度 | 方案 A | 方案 B | 方案 C |
|------|--------|--------|--------|
| **实施复杂度** | 中 | 中高 | 高 |
| **开发周期** | 5周 | 6周 | 7.5周 |
| **召回率提升** | +15% | +20% | +25% |
| **延迟降低** | -30% | -50% | -40% |
| **系统稳定性** | 高 | 中高 | 中 |
| **可维护性** | 高 | 中 | 低 |
| **技术风险** | 低 | 中 | 中 |

### 推荐方案

> **推荐选择方案 A（规则+LLM混合架构）**

**推荐理由**：

1. **风险可控**：规则层确保核心功能的稳定性，LLM层作为增强
2. **收益明确**：召回率+15%，延迟-30%，开发周期仅5周
3. **可扩展性**：模块化设计便于后续升级到方案B或方案C
4. **与现有架构兼容**：无需大规模重构，可渐进式实施

**实施路径**：

```
Phase 1: 启用 synonym_map → 基础优化完成（高影响，低风险）
         ↓
Phase 2-5: 逐步添加混合指代消解、缓存、自适应阈值、规则分解
         ↓
后续: 根据实际效果评估是否升级到方案B（语义缓存）或方案C（多策略集成）
```

---

## 四、开源RAG项目技术借鉴

### 1. Dify Query Rewrite

**核心技术**：
- **Step-Back Prompting**：将简单问题转化为更通用的问题，再逐步具体化
- **Query Transformation**：支持多种查询转换策略，包括扩展、简化、翻译等
- **HyDE (Hypothetical Document Embeddings)**：生成假设性文档来增强查询语义

**SPMA借鉴点**：
- 实现 Step-Back 风格的问题泛化，对简单查询进行语义扩展
- 添加查询转换策略的可配置性，支持不同场景的策略切换
- 借鉴 HyDE 思路，生成假设性检索结果来优化查询

**具体实现建议**：

```python
async def _step_back_expansion(query: str, llm) -> str:
    """Step-Back风格的问题泛化扩展"""
    prompt = f"""将以下具体问题泛化为更通用的问题，然后再具体化。

原始问题: {query}

步骤1: 泛化——将具体问题转化为更通用的概念
步骤2: 具体化——基于泛化后的概念，生成扩展后的查询

输出扩展后的完整查询:"""
    
    resp_obj = await llm.ainvoke(prompt)
    return resp_obj.content.strip()
```

### 2. QAnything Multi-Route Expansion

**核心技术**：
- **多路召回查询扩展**：对同一查询生成多种扩展形式，并行检索后融合结果
- **Query Decomposition**：将复杂查询分解为多个子查询，分别检索后合并
- **Semantic Reranking**：对检索结果进行语义重排序，提高相关性

**SPMA借鉴点**：
- 实现多路扩展策略，对同一查询并行应用多种扩展方式
- 改进查询分解逻辑，支持更细粒度的子查询生成
- 添加语义重排序层，对分解后的子查询进行质量评估

**具体实现建议**：

```python
async def _multi_route_expansion(query: str, classification: dict, entities: dict, llm) -> str:
    """多路查询扩展"""
    routes = [
        await _expand_with_intent(query, classification, llm),
        await _expand_with_entities(query, entities),
        await _expand_with_synonyms(query, entities),
    ]
    
    best_route = await _semantic_rerank(query, routes, llm)
    return best_route


async def _semantic_rerank(original: str, candidates: list[str], llm) -> str:
    """语义重排序"""
    scores = []
    for candidate in candidates:
        score = await _evaluate_quality(original, candidate, llm, {})
        scores.append((candidate, score))
    
    return max(scores, key=lambda x: x[1])[0]
```

### 3. Cohere Rerank

**核心技术**：
- **Query-Document Pair Reranking**：对查询-文档对进行重排序，提高相关性
- **Cross-Encoder Architecture**：使用交叉编码器模型进行语义匹配
- **Multi-Lingual Support**：支持多种语言的语义理解和重排序

**SPMA借鉴点**：
- 实现查询-子查询对的一致性校验，确保子查询覆盖核心意图
- 添加语义匹配层，对重写结果进行质量评估
- 支持多语言查询重写，适应国际化场景

**具体实现建议**：

```python
async def _query_subquery_consistency_check(
    original: str,
    sub_queries: list[dict],
    llm,
) -> list[dict]:
    """查询-子查询对一致性校验"""
    validated = []
    
    for sq in sub_queries:
        sub_query = sq["query"]
        consistency = await _evaluate_quality(original, sub_query, llm, {})
        
        if consistency >= 0.6:
            validated.append(sq)
        else:
            validated.append({"query": original, "target": sq["target"]})
    
    return validated
```

### 4. RAGAs Quality Assessment

**核心技术**：
- **Context Relevancy**：评估检索上下文与查询的相关性
- **Faithfulness**：评估生成答案与检索上下文的一致性
- **Answer Relevancy**：评估生成答案与查询的相关性

**SPMA借鉴点**：
- 构建多维度的重写质量评估体系
- 添加检索上下文相关性评估
- 实现答案-查询相关性评估，形成完整的质量闭环

**具体实现建议**：

```python
async def _evaluate_rewrite_quality(
    original: str,
    rewritten: str,
    llm,
    entities: dict,
) -> dict:
    """多维度重写质量评估"""
    return {
        "semantic_similarity": await _evaluate_semantic_similarity(original, rewritten, llm),
        "entity_coverage": _evaluate_entity_coverage(rewritten, entities),
        "length_change": _evaluate_length_change(original, rewritten),
        "overall": await _calculate_overall_score(original, rewritten, llm, entities),
    }
```

---

## 五、验证方法论

### 5.1 测试基线

当前单元测试状态：

```
测试文件: tests/unit/agents/supervisor/test_query_rewriter.py
测试用例: 24个
测试结果: 全部通过 (24/24)
测试耗时: 0.21s
```

### 5.2 指标测量方法

#### 重写成功率

**定义**：重写结果成功生成且通过质量评估的比例

**测量方法**：
```python
async def measure_rewrite_success_rate(test_cases: list[dict]) -> float:
    success_count = 0
    for case in test_cases:
        try:
            result = await rewrite_queries(
                query=case["query"],
                classification=case["classification"],
                entities=case.get("entities", {}),
                llm=mock_llm,
                synonym_map=case.get("synonym_map", {}),
                conversation_history=case.get("history", ""),
            )
            if result.get("normalized") and all(
                result.get(source) for source in case["classification"].get("sources", [])
            ):
                success_count += 1
        except Exception:
            pass
    return success_count / len(test_cases)
```

#### 语义保持率

**定义**：重写后查询与原查询的语义相似度

**测量方法**：基于 LLM 的语义相似度评分（0-1），阈值 ≥ 0.7 视为合格

#### 实体覆盖率

**定义**：重写后查询中包含的实体占原始提取实体的比例

**测量方法**：
```python
def measure_entity_coverage(rewritten: str, entities: dict) -> float:
    all_entities = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        if key in entities and entities[key]:
            all_entities.extend(entities[key])
    if not all_entities:
        return 1.0
    covered = sum(1 for e in all_entities if e.lower() in rewritten.lower())
    return covered / len(all_entities)
```

#### 延迟测量

**定义**：单次查询重写的平均耗时

**测量方法**：使用 `time.time()` 记录每次调用的耗时，采样 ≥ 100 次求平均值

#### LLM调用次数

**定义**：单次查询重写过程中调用 LLM 的次数

**测量方法**：通过 Mock LLM 的 `ainvoke.call_count` 属性统计

### 5.3 验证流程

```
Phase 1: 基线测试（当前状态）
         ↓
         记录：成功率、语义保持率、实体覆盖率、延迟、LLM调用次数
         ↓
Phase 2: 实施优化（按方案A逐步实施）
         ↓
         每完成一个子阶段，运行测试并记录指标
         ↓
Phase 3: 对比分析
         ↓
         计算各项指标的变化率，验证是否达到预期目标
         ↓
Phase 4: 回归测试
         ↓
         确保所有原有测试用例仍然通过
```

### 5.4 A/B测试方案

**测试环境**：
- 实验组：部署优化后的查询重写模块
- 对照组：保持当前实现

**测试样本**：
- 每组 ≥ 1000 次真实请求
- 请求类型覆盖：search、data_query、explain、trace

**统计指标**：
- 重写成功率对比
- 下游检索召回率对比
- 整体响应延迟对比
- LLM调用成本对比

### 5.5 验收标准

| 指标 | 当前值 | 方案A验收标准 |
|------|--------|-------------|
| 重写成功率 | ~95% | ≥ 98% |
| 语义保持率 | ~85% | ≥ 92% |
| 实体覆盖率 | ~70% | ≥ 85% |
| 召回率提升 | 0% | +15% |
| 延迟降低 | 0% | -30% |
| LLM调用次数 | 3-4次/请求 | 1-2次/请求 |
| 单元测试通过率 | 100% | 100%（新增测试用例 ≥ 50个） |

---

## 六、关键技术指标

| 指标 | 当前值 | 方案A目标 | 方案B目标 | 方案C目标 |
|------|--------|----------|----------|----------|
| 重写成功率 | ~95% | ≥ 98% | ≥ 99% | ≥ 99% |
| 语义保持率 | ~85% | ≥ 92% | ≥ 94% | ≥ 95% |
| 实体覆盖率 | ~70% | ≥ 85% | ≥ 90% | ≥ 92% |
| 召回率提升 | 0% | +15% | +20% | +25% |
| 延迟降低 | 0% | -30% | -50% | -40% |
| LLM调用次数 | 每次请求3-4次 | 每次请求1-2次 | 缓存命中时0次 | 每次请求2-3次 |

---

## 七、参考资料

1. [SPMA-design-10-query-rewrite.md](SPMA-design-10-query-rewrite.md) — 原有查询重写设计
2. [src/spma/agents/supervisor/query_rewriter.py](../src/spma/agents/supervisor/query_rewriter.py) — 当前实现
3. [src/spma/ingestion/synonym_map.py](../src/spma/ingestion/synonym_map.py) — 同义词映射表
4. [Dify Query Rewrite](https://docs.dify.ai/advanced/query-rewrite) — Dify查询改写技术文档
5. [QAnything Architecture](https://github.com/netease-youdao/QAnything) — QAnything多路召回架构
6. [Cohere Rerank API](https://docs.cohere.com/reference/rerank) — Cohere重排序API
7. [RAGAs Documentation](https://docs.ragas.io/) — RAG质量评估框架
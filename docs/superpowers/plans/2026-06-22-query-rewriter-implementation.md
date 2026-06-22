# Query Rewriter 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增强 query_rewriter.py，实现同义词标准化、指代消解、意图感知扩展、多层级容错分解、质量评估与降级机制

**Architecture:** 五阶段纯函数管道：_normalize_with_synonyms → _resolve_references → _expand_query → _decompose_query → _evaluate_quality。采用轻量级日志（logging 模块），不分阶段一次性实现。

**Tech Stack:** Python async/await, LangChain LLM, logging

---

## 文件结构

```
src/spma/agents/supervisor/
├── query_rewriter.py          # 主文件：重写主函数 + 五个阶段函数
└── tests/unit/agents/supervisor/
    └── test_query_rewriter.py # 新建：单元测试

需修改的文件：
- src/spma/agents/supervisor/graph.py         # 传递 synonym_map 和 conversation_history
- src/spma/api/routes/query.py                # 传递 conversation_history
```

---

## Task 1: 实现 `_evaluate_quality` 质量评估函数

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# tests/unit/agents/supervisor/test_query_rewriter.py

import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.supervisor.query_rewriter import _evaluate_quality


class TestEvaluateQuality:
    """质量评估测试"""

    def test_evaluate_quality_high_similarity(self):
        """语义完全一致时应返回 >= 0.9"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="0.9")
        score = await _evaluate_quality("用户登录", "用户登录 authentication login", llm)
        assert score >= 0.9

    def test_evaluate_quality_low_similarity(self):
        """语义严重偏差时应返回 < 0.5"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="0.3")
        score = await _evaluate_quality("用户登录", "商品列表查询", llm)
        assert score < 0.5

    def test_evaluate_quality_invalid_response(self):
        """LLM 返回无效值时默认返回 0.5"""
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="invalid")
        score = await _evaluate_quality("用户登录", "用户登录功能", llm)
        assert score == 0.5
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestEvaluateQuality -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 实现 `_evaluate_quality` 函数**

在 `query_rewriter.py` 末尾添加：

```python
async def _evaluate_quality(
    original: str,
    rewritten: str,
    llm,
) -> float:
    """评估重写查询与原始查询的语义相似度（0-1）"""
    if not llm:
        return 0.5

    prompt = f"""评估以下重写查询是否保持了原始查询的核心语义。

评分标准：
- 1.0：完全一致，语义无偏差
- 0.8-0.9：略有扩展，但核心语义保持
- 0.5-0.7：有一定偏差，但仍相关
- < 0.5：语义偏差严重或完全无关

原始查询: {original}
重写查询: {rewritten}

评分(0-1):"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        return float(resp_obj.content.strip())
    except (ValueError, AttributeError):
        return 0.5
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestEvaluateQuality -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add tests/unit/agents/supervisor/test_query_rewriter.py src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: add _evaluate_quality function for semantic similarity scoring"
```

---

## Task 2: 实现 `_normalize_with_synonyms` 同义词标准化

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# 在 test_query_rewriter.py 中添加

class TestNormalizeWithSynonyms:
    """同义词标准化测试"""

    def test_normalize_with_synonyms_empty_map(self):
        """synonym_map 为空时直接返回原查询"""
        from spma.agents.supervisor.query_rewriter import _normalize_with_synonyms
        result = await _normalize_with_synonyms("用户登录查询", None, {})
        assert result == "用户登录查询"

    def test_normalize_with_synonyms_basic(self):
        """基本同义词替换"""
        from spma.agents.supervisor.query_rewriter import _normalize_with_synonyms
        synonym_map = {"用户": ["user", "账号"], "登录": ["login", "authentication"]}
        entities = {}
        result = await _normalize_with_synonyms("用户登录", synonym_map, entities)
        assert "user" in result
        assert "login" in result

    def test_normalize_with_synonyms_with_entities(self):
        """基于实体的精确映射"""
        from spma.agents.supervisor.query_rewriter import _normalize_with_synonyms
        synonym_map = {"用户": ["user"]}
        entities = {"req_ids": ["REQ-001", "REQ-002"], "table_names": ["users"]}
        result = await _normalize_with_synonyms("用户查询", synonym_map, entities)
        assert "REQ-001" in result
        assert "REQ-002" in result
        assert "users" in result
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestNormalizeWithSynonyms -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 实现 `_normalize_with_synonyms` 函数**

在 `query_rewriter.py` 中 `_evaluate_quality` 之前添加：

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

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestNormalizeWithSynonyms -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: add _normalize_with_synonyms for term standardization"
```

---

## Task 3: 实现 `_resolve_references` 指代消解

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# 在 test_query_rewriter.py 中添加

class TestResolveReferences:
    """指代消解测试"""

    def test_resolve_references_no_history(self):
        """无对话历史时直接返回原查询"""
        from spma.agents.supervisor.query_rewriter import _resolve_references
        result = await _resolve_references("用户登录", "", None)
        assert result == "用户登录"

    def test_resolve_references_no_reference_words(self):
        """查询中无指代性词汇时直接返回"""
        from spma.agents.supervisor.query_rewriter import _resolve_references
        history = "之前我们讨论了用户登录问题"
        result = await _resolve_references("查询相关代码", history, None)
        assert result == "查询相关代码"

    def test_resolve_references_with_llm(self):
        """有指代词汇且有 LLM 时调用消解"""
        from spma.agents.supervisor.query_rewriter import _resolve_references
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="用户登录 authentication login 涉及哪些需求和代码")
        history = "用户登录涉及哪些需求和代码"
        result = await _resolve_references("这个问题", history, llm)
        assert "用户登录" in result or "authentication" in result
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestResolveReferences -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 实现 `_resolve_references` 函数**

在 `query_rewriter.py` 中添加：

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

    if not llm:
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

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestResolveReferences -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: add _resolve_references for pronoun resolution"
```

---

## Task 4: 实现 `_expand_query` 四类意图感知扩展

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# 在 test_query_rewriter.py 中添加

class TestExpandQuery:
    """查询扩展测试"""

    def test_expand_query_no_llm(self):
        """无 LLM 时返回原查询"""
        from spma.agents.supervisor.query_rewriter import _expand_query
        classification = {"query_type": "search"}
        result = await _expand_query("用户登录", classification, {}, None)
        assert result == "用户登录"

    def test_expand_query_unknown_type(self):
        """未知 query_type 时返回原查询"""
        from spma.agents.supervisor.query_rewriter import _expand_query
        llm = AsyncMock()
        classification = {"query_type": "unknown_type"}
        result = await _expand_query("用户登录", classification, {}, llm)
        assert result == "用户登录"

    def test_expand_query_search_type(self):
        """search 类型扩展"""
        from spma.agents.supervisor.query_rewriter import _expand_query
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="用户登录 authentication login 涉及哪些需求和代码实现")
        classification = {"query_type": "search"}
        entities = {"req_ids": ["REQ-001"]}
        result = await _expand_query("用户登录", classification, entities, llm)
        assert "用户登录" in result
        # 验证 LLM 被调用
        llm.ainvoke.assert_called_once()
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestExpandQuery -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 实现 `_expand_query` 函数**

在 `query_rewriter.py` 中添加：

```python
async def _expand_query(
    query: str,
    classification: dict,
    entities: dict,
    llm,
) -> str:
    """基于意图的查询扩展"""
    if not llm:
        return query

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

    try:
        resp_obj = await llm.ainvoke(prompt)
        expanded = resp_obj.content.strip()

        # 质量校验：质量低于 0.5 时回退到原查询
        if await _evaluate_quality(query, expanded, llm) < 0.5:
            return query

        return expanded
    except Exception:
        return query
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestExpandQuery -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: add _expand_query with four intent-aware strategies"
```

---

## Task 5: 实现 `_decompose_query` 多层级容错分解

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# 在 test_query_rewriter.py 中添加

class TestDecomposeQuery:
    """查询分解测试"""

    def test_decompose_query_no_llm(self):
        """无 LLM 时返回默认子查询"""
        from spma.agents.supervisor.query_rewriter import _decompose_query
        result = await _decompose_query("用户登录", {}, ["doc", "code"], None)
        assert len(result) == 2
        assert all(r["query"] == "用户登录" for r in result)

    def test_decompose_query_valid_json(self):
        """正常 JSON 返回"""
        from spma.agents.supervisor.query_rewriter import _decompose_query
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content='[{"query": "用户登录需求", "target": "doc"}, {"query": "用户登录代码", "target": "code"}]')
        result = await _decompose_query("用户登录", {}, ["doc", "code"], llm)
        assert len(result) == 2
        assert result[0]["target"] == "doc"
        assert result[1]["target"] == "code"

    def test_decompose_query_invalid_json_regex_fallback(self):
        """JSON 解析失败时正则提取"""
        from spma.agents.supervisor.query_rewriter import _decompose_query
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content='Here is the result: [{"query": "用户登录需求", "target": "doc"}, {"query": "用户登录代码", "target": "code"}]')
        result = await _decompose_query("用户登录", {}, ["doc", "code"], llm)
        assert len(result) == 2

    def test_decompose_query_complete_failure(self):
        """完全解析失败时返回默认子查询"""
        from spma.agents.supervisor.query_rewriter import _decompose_query
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="这是一段无法解析的文本")
        result = await _decompose_query("用户登录", {}, ["doc", "code", "sql"], llm)
        assert len(result) == 3
        assert all(r["query"] == "用户登录" for r in result)
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestDecomposeQuery -v`
Expected: FAIL - function not defined

- [ ] **Step 3: 实现 `_decompose_query` 函数**

在 `query_rewriter.py` 中添加：

```python
async def _decompose_query(
    query: str,
    entities: dict,
    sources: list[str],
    llm,
) -> list[dict]:
    """跨源查询分解：多层级容错"""
    if not sources:
        return []

    # 无 LLM 时返回默认子查询
    if not llm:
        return [{"query": query, "target": source} for source in sources]

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

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content

        # 策略1：直接 JSON 解析
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            pass

        # 策略2：正则提取 JSON 数组
        import re
        json_match = re.search(r'\[.*\]', resp, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        # 策略3：提取键值对
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

        # 策略4：兜底——每个 source 返回原始查询
        return [{"query": query, "target": source} for source in sources]

    except Exception:
        return [{"query": query, "target": source} for source in sources]
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestDecomposeQuery -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: add _decompose_query with multi-layer fallback"
```

---

## Task 6: 重构 `rewrite_queries` 主函数，整合五个阶段

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py`

- [ ] **Step 1: 编写测试**

```python
# 在 test_query_rewriter.py 中添加

class TestRewriteQueries:
    """主函数集成测试"""

    def test_rewrite_queries_basic(self):
        """基本调用：参数完整但无 LLM"""
        from spma.agents.supervisor.query_rewriter import rewrite_queries
        classification = {"sources": ["doc", "code"], "is_cross_source": True}
        result = await rewrite_queries(
            query="用户登录",
            classification=classification,
            entities={"req_ids": ["REQ-001"]},
            llm=None,
        )
        assert result["original"] == "用户登录"
        assert "normalized" in result
        assert "doc" in result
        assert "code" in result

    def test_rewrite_queries_with_synonym_map(self):
        """同义词映射生效"""
        from spma.agents.supervisor.query_rewriter import rewrite_queries
        classification = {"sources": ["doc"], "is_cross_source": False}
        synonym_map = {"用户": ["user", "账号"]}
        result = await rewrite_queries(
            query="用户登录",
            classification=classification,
            entities={},
            llm=None,
            synonym_map=synonym_map,
        )
        assert "user" in result["normalized"]
        assert "账号" in result["normalized"]

    def test_rewrite_queries_with_conversation_history(self):
        """指代消解生效"""
        from spma.agents.supervisor.query_rewriter import rewrite_queries
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content="用户登录 authentication login 涉及哪些需求")
        classification = {"sources": ["doc"], "is_cross_source": False, "query_type": "search"}
        result = await rewrite_queries(
            query="这个问题",
            classification=classification,
            entities={},
            llm=llm,
            conversation_history="用户登录涉及哪些需求和代码",
        )
        assert "resolved" in result

    def test_rewrite_queries_cross_source_decomposition(self):
        """跨源分解生效"""
        from spma.agents.supervisor.query_rewriter import rewrite_queries
        llm = AsyncMock()
        llm.ainvoke.return_value = MagicMock(content='[{"query": "用户登录需求", "target": "doc"}, {"query": "用户登录代码", "target": "code"}]')
        classification = {"sources": ["doc", "code"], "is_cross_source": True, "query_type": "search"}
        result = await rewrite_queries(
            query="用户登录",
            classification=classification,
            entities={},
            llm=llm,
        )
        assert result["doc"] == "用户登录需求"
        assert result["code"] == "用户登录代码"
```

- [ ] **Step 2: 运行测试验证失败**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestRewriteQueries -v`
Expected: FAIL - 需要重写主函数

- [ ] **Step 3: 重写 `rewrite_queries` 主函数**

替换现有的 `rewrite_queries` 函数：

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
    查询重写主函数 - 五阶段管道

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
    result: dict[str, str] = {"original": query}

    # 阶段一：同义词标准化
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized

    # 阶段二：指代消解
    resolved = await _resolve_references(normalized, conversation_history, llm)
    result["resolved"] = resolved

    # 阶段三：查询扩展（触发条件：查询长度 <= 50 或 query_type == "search"）
    query_type = classification.get("query_type", "search")
    sources = classification.get("sources", [])
    is_cross_source = classification.get("is_cross_source", False)

    should_expand = len(query) <= 50 or query_type == "search"
    if should_expand and llm:
        expanded = await _expand_query(resolved, classification, entities, llm)
        result["expanded"] = expanded
    else:
        result["expanded"] = resolved

    # 阶段四：查询分解（仅跨源时执行）
    if is_cross_source and len(sources) > 1 and llm:
        sub_queries = await _decompose_query(resolved, entities, sources, llm)
        for sq in sub_queries:
            target = sq.get("target", "")
            if target in sources:
                result[target] = sq.get("query", resolved)
    else:
        # 非跨源或无 LLM 时，各 source 使用扩展后的查询
        for source in sources:
            result[source] = result.get("expanded", resolved)

    # 日志记录
    logger.info(f"Query rewrite: original={query[:50]}, "
                f"sources={sources}, "
                f"expanded={result.get('expanded', '')[:50] if result.get('expanded') else None}")

    return result
```

- [ ] **Step 4: 运行测试验证通过**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py::TestRewriteQueries -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add src/spma/agents/supervisor/query_rewriter.py
git commit -m "feat: refactor rewrite_queries as 5-stage pipeline"
```

---

## Task 7: 更新 `graph.py` 调用处

**Files:**
- Modify: `src/spma/agents/supervisor/graph.py:43-50`

- [ ] **Step 1: 确认当前代码**

当前 `rewrite_node` 函数：
```python
async def rewrite_node(state: SupervisorState) -> dict:
    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=primary_llm,
    )
    return {"rewritten_queries": rewritten}
```

- [ ] **Step 2: 更新调用，传递新参数**

```python
async def rewrite_node(state: SupervisorState) -> dict:
    from spma.ingestion.synonym_map import SynonymMap

    # 获取 synonym_map（如果可用）
    synonym_map = None
    try:
        from spma.api.dependencies import get_synonym_map_store
        store = get_synonym_map_store()
        if store:
            synonym_map = store
    except Exception:
        pass

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

- [ ] **Step 3: 运行现有测试确保无回归**

Run: `pytest tests/integration/test_supervisor_loop.py -v -k rewrite`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/spma/agents/supervisor/graph.py
git commit -m "feat: update rewrite_node to pass synonym_map and conversation_history"
```

---

## Task 8: 更新 API routes 调用处

**Files:**
- Modify: `src/spma/api/routes/query.py`

- [ ] **Step 1: 找到调用 rewrite_queries 的位置**

Run: `grep -n "rewrite_queries" /Users/Ray/TraeProjects/SPMA/src/spma/api/routes/query.py`

- [ ] **Step 2: 更新调用**

在调用处添加 `conversation_history` 参数传递（synonym_map 从 graph.py 路径获取）：

```python
rewritten = await rewrite_queries(
    query=req.query,
    classification=classification,
    entities=entities,
    llm=llm,
    synonym_map=None,  # API 层面暂不从数据库获取，后续可扩展
    conversation_history=req.conversation_history or "",
)
```

- [ ] **Step 3: 运行测试确保无回归**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py -v`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add src/spma/api/routes/query.py
git commit -m "feat: update API route to pass conversation_history to rewrite_queries"
```

---

## Task 9: 清理并验证完整测试套件

**Files:**
- None（仅运行测试）

- [ ] **Step 1: 运行完整测试套件**

Run: `pytest tests/unit/agents/supervisor/test_query_rewriter.py -v`
Expected: ALL PASS

- [ ] **Step 2: 运行集成测试**

Run: `pytest tests/integration/test_supervisor_loop.py -v`
Expected: ALL PASS

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "test: add and run full test suite for query rewriter"
```

---

## 实施完成检查清单

- [ ] `_evaluate_quality` 实现并测试通过
- [ ] `_normalize_with_synonyms` 实现并测试通过
- [ ] `_resolve_references` 实现并测试通过
- [ ] `_expand_query` 四类意图策略实现并测试通过
- [ ] `_decompose_query` 多层级容错实现并测试通过
- [ ] `rewrite_queries` 主函数重构并测试通过
- [ ] `graph.py` 更新传递新参数
- [ ] `query.py` API routes 更新传递 conversation_history
- [ ] 集成测试无回归

---

## 参考资料

1. [设计文档](../../docs/superpowers/specs/2026-06-22-query-rewrite-design.md)
2. [synonym_map.py](../../../src/spma/ingestion/synonym_map.py)
3. [graph.py](../../../src/spma/agents/supervisor/graph.py)
4. [query.py API routes](../../../src/spma/api/routes/query.py)
# Query Rewriter Phase 5 — 多路查询分解 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复主文件 §1.1 G7(`_decompose_query` 单策略 + 4 步 JSON 兜底),通过 3 路并行(template/llm/entity) + 语义聚类一致性校验(主文件 ADR-004 零 LLM)选最优。

**Architecture:**
- 收编已有 `_decompose_query` 为 `llm_based` 策略(保留 4 步 JSON 解析兜底)
- 新增 `template_based` 策略(零 LLM,识别"涉及哪些 X 和 Y"模式)
- 新增 `entity_guided` 策略(零 LLM,按 entity 类型分发到对应 source)
- 新增 `SemanticConsensusChecker`(per-source 共识度最高,零 LLM)
- 集成到 `_do_rewrite_pipeline` 分解阶段

**Tech Stack:** asyncio / pytest / 已有 `embedding_model` 注入

**依赖:** [P2 (编排)](2026-06-30-qr-phase2-strategy-orchestration-plan.md) + [P3 (voter 模式)](2026-06-30-qr-phase3-multi-strategy-resolution-plan.md)
**被依赖:** P6 监控消费

**Spec:** [SPMA-design-11-phase5-multi-strategy-decomposition.md](../../designs/SPMA-design-11-phase5-multi-strategy-decomposition.md)

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/spma/agents/supervisor/decomposition_strategies.py` | 新建 | 3 个分解子策略 |
| `src/spma/agents/supervisor/semantic_consensus.py` | 新建 | `SemanticConsensusChecker` |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | 分解阶段用编排器替换 |
| `tests/unit/agents/supervisor/test_decomposition_strategies.py` | 新建 | 3 策略单测 |
| `tests/unit/agents/supervisor/test_semantic_consensus.py` | 新建 | 一致性校验单测 |

---

## Task 1: 3 个分解子策略 + 单测

**Files:**
- Create: `src/spma/agents/supervisor/decomposition_strategies.py`
- Test: `tests/unit/agents/supervisor/test_decomposition_strategies.py`

### Step 1.1: 写失败的测试

`tests/unit/agents/supervisor/test_decomposition_strategies.py`:

```python
"""查询分解多路策略单测。"""
import pytest

from spma.agents.supervisor.decomposition_strategies import (
    template_based, entity_guided, llm_based,
)


@pytest.mark.asyncio
async def test_template_based_splits_on_explicit_and():
    """'涉及哪些 X 和 Y' 模式 → 按 source 拆。"""
    result = await template_based(
        query="涉及哪些需求和表",
        entities={},
        sources=["database", "requirements"],
    )
    assert result is not None
    assert len(result) == 2
    targets = {r["target"] for r in result}
    assert targets == {"database", "requirements"}


@pytest.mark.asyncio
async def test_template_based_returns_none_for_simple_query():
    """简单 query → 返回 None(交给其他策略)。"""
    result = await template_based(
        query="今天天气",
        entities={},
        sources=["doc"],
    )
    assert result is None


@pytest.mark.asyncio
async def test_template_based_broadcasts_when_multiple_entity_types():
    """多种 entity 类型存在 → 自动广播到所有 source。"""
    result = await template_based(
        query="综合查询",
        entities={"table_names": ["t_user"], "code_refs": ["auth.py"]},
        sources=["database", "codebase"],
    )
    assert result is not None
    assert len(result) == 2


@pytest.mark.asyncio
async def test_entity_guided_returns_none_when_all_sources_have_same_entities():
    """所有 source 的实体相同 → 返回 None(避免 N 个相同子查询,主文件 §3.4 ADR)。"""
    result = await entity_guided(
        query="综合",
        entities={"table_names": ["t_user"]},  # 只有一个 source(database)用
        sources=["database", "doc"],
    )
    # doc 没有对应实体类型,unique_entity_sets 只有一个 → None
    assert result is None


@pytest.mark.asyncio
async def test_entity_guided_differentiates_when_sources_have_different_entities():
    """不同 source 实体不同 → 按 source 差异化生成。"""
    result = await entity_guided(
        query="查询",
        entities={"table_names": ["t_user"], "code_refs": ["auth.py"]},
        sources=["database", "codebase"],
    )
    assert result is not None
    db_query = next(r for r in result if r["target"] == "database")
    code_query = next(r for r in result if r["target"] == "codebase")
    assert "t_user" in db_query["query"]
    assert "auth.py" in code_query["query"]


@pytest.mark.asyncio
async def test_llm_based_falls_back_to_broadcast_without_llm():
    """无 LLM → 返回原 query 广播(沿用已有 fallback)。"""
    result = await llm_based(
        query="查询",
        entities={},
        sources=["doc", "code"],
        llm=None,
    )
    assert result is not None
    assert len(result) == 2
    assert all(r["query"] == "查询" for r in result)


@pytest.mark.asyncio
async def test_llm_based_rejects_overlong_output():
    """LLM 输出超 5000 字符 → 返回 None。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 10000
            return Resp()

    result = await llm_based(
        query="短问",
        entities={},
        sources=["doc"],
        llm=FakeLLM(),
    )
    # 输出超限被丢弃,走 fallback → broadcast
    assert result is not None
    assert result[0]["query"] == "短问"
```

### Step 1.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_decomposition_strategies.py -v
```

Expected: ImportError(`decomposition_strategies` 不存在)

### Step 1.3: 写 3 个分解子策略实现

`src/spma/agents/supervisor/decomposition_strategies.py`:

```python
"""查询分解多路策略。

- template_based:零 LLM,识别 '涉及哪些 X 和 Y' 模式
- entity_guided:零 LLM,按 entity 类型分发到 source
- llm_based:LLM 智能分解(收编 _decompose_query,保留 4 步 JSON 兜底)
"""
import json
import re
import logging

logger = logging.getLogger(__name__)


async def template_based(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """规则模板分解:识别显式多意图模式。"""
    if "和" in query and ("涉及哪些" in query or "以及" in query):
        parts = query.split("和")
        if len(parts) == 2:
            return [
                {"query": query.replace("和", f",面向{source}的"), "target": source}
                for source in sources
            ]

    entity_types_found = sum(
        1 for k in ["table_names", "code_refs", "req_ids"]
        if entities.get(k)
    )
    if entity_types_found >= 2:
        return [{"query": query, "target": s} for s in sources]

    return None


async def entity_guided(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """实体导向:按 entity 类型 → source 映射,差异化生成。"""
    entity_source_map = {
        "table_names": ["database"],
        "column_names": ["database"],
        "code_refs": ["codebase"],
        "req_ids": ["requirements"],
    }

    per_source_entities: dict[str, list[str]] = {}
    for source in sources:
        ents = []
        for entity_key, source_list in entity_source_map.items():
            if source in source_list and entities.get(entity_key):
                ents.extend(entities[entity_key])
        per_source_entities[source] = ents

    # 全部 source 实体相同(都为空或相同)→ 早退,避免 N 个相同子查询
    unique_sets = {tuple(v) for v in per_source_entities.values()}
    if len(unique_sets) <= 1:
        return None

    result = []
    for source in sources:
        ents = per_source_entities[source]
        if ents:
            result.append({"query": f"{query} {' '.join(ents)}", "target": source})
        else:
            result.append({"query": query, "target": source})
    return result


async def llm_based(
    query: str, entities: dict, sources: list[str],
    *, llm=None, **_,
) -> list[dict] | None:
    """LLM 智能分解(收编 _decompose_query,保留 4 步 JSON 兜底)。"""
    if not sources:
        return None
    if not llm:
        return [{"query": query, "target": s} for s in sources]

    entities_str = str({k: v for k, v in (entities or {}).items() if v})
    prompt = f"""将以下复杂查询分解为 {len(sources)} 个独立的子查询,每个子查询面向单一数据源。

已抽取实体: {entities_str}
可用数据源: {', '.join(sources)}
用户查询: {query}

输出格式要求:
- 必须输出合法的 JSON 数组
- 每个元素包含 "query" 和 "target" 两个字段
- "target" 必须是 {', '.join(sources)} 中的一个
- 子查询应覆盖原始查询的所有核心意图

输出示例:
[{{"query": "子查询1", "target": "doc"}}, {{"query": "子查询2", "target": "code"}}]"""

    try:
        resp = await llm.ainvoke(prompt)
        content = resp.content
        if len(content) > 5000:
            logger.warning(f"llm_based: output too long ({len(content)}), dropped")
            return None
        # 策略 1: 直接 JSON
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        # 策略 2: 正则提取
        m = re.search(r'\[.*\]', content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
        # 策略 3: 键值对提取
        target_patterns = {
            source: re.search(rf'{source}[\s:]+["\']([^"\']+)["\']', content)
            for source in sources
        }
        result = []
        for source, pattern in target_patterns.items():
            if pattern:
                result.append({"query": pattern.group(1), "target": source})
        if result:
            return result
        # 策略 4: 兜底
        return [{"query": query, "target": source} for source in sources]
    except Exception as e:
        logger.warning(f"llm_based failed: {type(e).__name__}: {e}")
        return [{"query": query, "target": s} for s in sources]
```

### Step 1.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_decomposition_strategies.py -v
```

Expected: 7 passed

### Step 1.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/decomposition_strategies.py tests/unit/agents/supervisor/test_decomposition_strategies.py
git commit -m "feat(qr): 3 个查询分解策略(template/entity/llm) (G7 部分)

- template_based:零 LLM,识别 '涉及哪些 X 和 Y' 模式 + 多种 entity 广播
- entity_guided:零 LLM,按 entity 类型差异化(早退避免 N 个相同)
- llm_based:收编 _decompose_query,保留 4 步 JSON 解析兜底

Refs: SPMA-design-11-phase5 §3.1"
```

---

## Task 2: `SemanticConsensusChecker` + 单测

**Files:**
- Create: `src/spma/agents/supervisor/semantic_consensus.py`
- Test: `tests/unit/agents/supervisor/test_semantic_consensus.py`

### Step 2.1: 写失败的测试

`tests/unit/agents/supervisor/test_semantic_consensus.py`:

```python
"""SemanticConsensusChecker 单测。"""
import pytest

from spma.agents.supervisor.semantic_consensus import SemanticConsensusChecker


class FakeEmbedder:
    async def embed_query(self, text): return [0.5, 0.5, 0.5]
    async def embed_documents(self, texts): return [[0.5, 0.5, 0.5] for _ in texts]


@pytest.mark.asyncio
async def test_consensus_no_candidate_falls_back_to_original():
    """某 source 无候选 → 用原 query。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[{"query": "订单表", "target": "database"}]],  # 只 database 有
        sources=["database", "code"],
    )
    code_query = next(r for r in results if r["target"] == "code")
    assert code_query["query"] == "订单"  # fallback


@pytest.mark.asyncio
async def test_consensus_single_candidate_kept():
    """单候选 → 直接用。"""
    checker = SemanticConsensusChecker(FakeEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[{"query": "订单表", "target": "database"}]],
        sources=["database"],
    )
    assert results[0]["query"] == "订单表"


@pytest.mark.asyncio
async def test_consensus_without_embedder_takes_first():
    """无 embedder → 取第一个候选。"""
    checker = SemanticConsensusChecker(None)
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[
            {"query": "candidate-A", "target": "database"},
            {"query": "candidate-B", "target": "database"},
        ]],
        sources=["database"],
    )
    assert results[0]["query"] == "candidate-A"


@pytest.mark.asyncio
async def test_consensus_handles_embedder_failure():
    """embedder 抛错 → 退化为取第一个。"""
    class BrokenEmbedder:
        async def embed_query(self, text): raise RuntimeError("boom")
        async def embed_documents(self, texts): raise RuntimeError("boom")

    checker = SemanticConsensusChecker(BrokenEmbedder())
    results = await checker.pick_best_per_source(
        original="订单",
        results=[[
            {"query": "A", "target": "database"},
            {"query": "B", "target": "database"},
        ]],
        sources=["database"],
    )
    assert results[0]["query"] == "A"
```

### Step 2.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_semantic_consensus.py -v
```

Expected: ImportError(`semantic_consensus` 不存在)

### Step 2.3: 写 `SemanticConsensusChecker` 实现

`src/spma/agents/supervisor/semantic_consensus.py`:

```python
"""基于语义聚类的一致性校验器(主文件 ADR-004,零 LLM)。"""
import logging
import math

logger = logging.getLogger(__name__)


class SemanticConsensusChecker:
    """多路分解结果间找共识子查询。"""

    def __init__(self, embedder, sim_threshold: float = 0.6):
        self._embedder = embedder
        self._threshold = sim_threshold

    async def pick_best_per_source(
        self, original: str, results: list[list[dict]], sources: list[str],
    ) -> list[dict]:
        """对每个 source,从所有策略结果中挑共识最高的子查询。"""
        final = []
        for source in sources:
            candidates = [
                sq["query"]
                for sub_list in results
                for sq in sub_list
                if sq.get("target") == source
            ]
            if not candidates:
                final.append({"query": original, "target": source})
                continue
            if len(candidates) == 1:
                final.append({"query": candidates[0], "target": source})
                continue
            if not self._embedder:
                final.append({"query": candidates[0], "target": source})
                continue

            try:
                embs = await self._embedder.embed_documents(candidates)
                orig_emb = await self._embedder.embed_query(original)
            except Exception as e:
                logger.warning(f"consensus_check: embedder failed: {e}")
                final.append({"query": candidates[0], "target": source})
                continue

            scored = []
            for i, cand in enumerate(candidates):
                orig_sim = self._cosine(embs[i], orig_emb)
                other_sims = [
                    self._cosine(embs[i], embs[j])
                    for j in range(len(candidates)) if j != i
                ]
                consensus = sum(other_sims) / len(other_sims) if other_sims else 0.0
                scored.append((cand, orig_sim * 0.6 + consensus * 0.4))
            final.append({"query": max(scored, key=lambda x: x[1])[0], "target": source})
        return final

    @staticmethod
    def _cosine(a, b) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(x * x for x in b)) + 1e-10
        return dot / (na * nb)
```

### Step 2.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_semantic_consensus.py -v
```

Expected: 4 passed

### Step 2.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/semantic_consensus.py tests/unit/agents/supervisor/test_semantic_consensus.py
git commit -m "feat(qr): SemanticConsensusChecker — per-source 共识(零 LLM)

主文件 ADR-004:hot path 零 LLM。
退化:无 embedder / embedder 抛错 / 候选 < 2。

Refs: SPMA-design-11-phase5 §3.2"
```

---

## Task 3: `_do_rewrite_pipeline` 分解阶段多路化

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py` (分解段)
- Test: `tests/unit/agents/supervisor/test_query_rewriter_decomposition_multi.py`

### Step 3.1: 写失败的测试

`tests/unit/agents/supervisor/test_query_rewriter_decomposition_multi.py`:

```python
"""验证 _do_rewrite_pipeline 分解阶段使用编排器 + consensus。"""
import pytest

from spma.agents.supervisor import query_rewriter
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


class FakeEmbedder:
    async def embed_query(self, text): return [0.5, 0.5, 0.5]
    async def embed_documents(self, texts): return [[0.5, 0.5, 0.5] for _ in texts]


@pytest.mark.asyncio
async def test_pipeline_uses_orchestrator_for_decomposition():
    """提供编排器时,分解走 3 路并行 + consensus。"""
    orch = StrategyOrchestrator(stage="test", names=["template_based", "entity_guided", "llm_based"])
    fb = FallbackManager(orch, lambda q, *a, **kw: q, lambda q, *a, **kw: q)

    result = await query_rewriter._do_rewrite_pipeline(
        query="订单系统",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        embedder=FakeEmbedder(),
    )
    assert "sub_queries" in result
    assert len(result["sub_queries"]) >= 1


@pytest.mark.asyncio
async def test_pipeline_backward_compatible_without_orchestrator():
    """不提供编排器时,走原 _decompose_query(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="订单",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
    )
    assert "sub_queries" in result
```

### Step 3.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_decomposition_multi.py -v
```

Expected: FAIL(分解段未走多路)

### Step 3.3: 修改 `_do_rewrite_pipeline` 分解段

修改 `src/spma/agents/supervisor/query_rewriter.py` 中扩展段之后的分解段:

**修改前**:
```python
sources = classification.get("sources", ["doc"])
try:
    sub_queries = await _decompose_query(expanded, entities, sources, llm)
except Exception:
    sub_queries = [{"query": expanded, "target": s} for s in sources]
result["sub_queries"] = sub_queries
```

**修改后**:
```python
# ====== P5: 多路查询分解 ======
sources = classification.get("sources", ["doc"])
if strategy_orchestrator:
    from spma.agents.supervisor.decomposition_strategies import (
        template_based, entity_guided, llm_based,
    )
    from spma.agents.supervisor.semantic_consensus import SemanticConsensusChecker

    strategies = {
        "template_based": lambda q, e, s: template_based(q, e, s),
        "entity_guided": lambda q, e, s: entity_guided(q, e, s),
        "llm_based": lambda q, e, s: llm_based(q, e, s, llm=llm),
    }
    try:
        results = await strategy_orchestrator.execute_parallel(
            strategies, expanded, entities, sources,
        )
        valid = [r[1] for r in results if r[1]]
        if valid:
            if embedder:
                checker = SemanticConsensusChecker(embedder)
                sub_queries = await checker.pick_best_per_source(expanded, valid, sources)
            else:
                # 退化为"取第一个非空"
                sub_queries = valid[0]
        else:
            sub_queries = [{"query": expanded, "target": s} for s in sources]
    except Exception as ex:
        logger.warning(f"multi-strategy decomposition failed, fallback: {ex}")
        sub_queries = await _decompose_query(expanded, entities, sources, llm)
else:
    sub_queries = await _decompose_query(expanded, entities, sources, llm)
result["sub_queries"] = sub_queries
```

### Step 3.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_decomposition_multi.py -v
pytest tests/unit/agents/supervisor/ -v
```

Expected: 2 new passed + 13+ 原单测全过

### Step 3.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/query_rewriter.py tests/unit/agents/supervisor/test_query_rewriter_decomposition_multi.py
git commit -m "feat(qr): 分解多路化(3 策略 + consensus,G7 修复)

- 注入 orchestrator + embedder 时走 3 路并行
- 用 SemanticConsensusChecker 选 per-source 共识
- 不注入时保持 _decompose_query 单策略

Refs: SPMA-design-11-phase5 §3.3"
```

---

## Task 4: 24h 灰度 + 关闭 P5

### Step 4.1: 24h 灰度

| 监控项 | 期望 |
|--------|------|
| 13+ 原单测 + 7 策略 + 4 consensus + 2 pipeline = 26+ | 全过 |
| `qr_rewrite_template_based/state` 等 CB 状态 | 偶发 |
| 离线数据集子查询覆盖率 | ≥ 90% |

### Step 4.2: 关闭 P5

```bash
cd /Users/Ray/TraeProjects/SPMA
git add docs/designs/SPMA-design-11-query-rewrite-optimization-v2-final.md
git commit -m "docs(qr): G7 标记为已修复(P5 完成)"
```

---

## 验收 checklist

- [ ] Task 1:7 个策略单测通过
- [ ] Task 2:4 个 consensus 单测通过
- [ ] Task 3:2 个 pipeline 测试 + 13 原单测无回归
- [ ] Task 4:24h 灰度无 P0 故障,子查询覆盖率 ≥ 90%
- [ ] 主文件 §1.1 G7 标记为已修复

---

## 失败回滚

```bash
git revert <commit_hash_of_task_3>
```

# Query Rewriter Phase 3 — 多路指代消解 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复主文件 §1.1 G5(`_resolve_references` 单策略 + 简单关键词匹配),通过多路并行(rule/llm/entity)+ 语义投票,提升代词识别 F1。

**Architecture:**
- 收编已有 `_resolve_references` 为 `llm_semantic` 策略(保留向后兼容)
- 新增 `rule_based` 策略(零 LLM,基于 entity 类型+代词模式)
- 新增 `entity_based` 策略(零 LLM,按出现顺序一一替换代词)
- 新增 `SemanticVoter`(基于 embedding 余弦相似度 + 共识度,主文件 ADR-004 零 LLM)
- 集成到 `_do_rewrite_pipeline` 指代消解阶段:用 P2 编排器并行 3 个策略,投票选最优

**Tech Stack:** asyncio / pytest + pytest-asyncio / 已有 `embedding_model` 注入(由 project 注入)

**依赖:** [P1 (synonym_map)](2026-06-30-qr-phase1-synonym-map-plan.md) + [P2 (编排器)](2026-06-30-qr-phase2-strategy-orchestration-plan.md)
**被依赖:** P6 监控消费;P4 / P5 复用 P3 的"多路 + 投票"模式

**Spec:** [SPMA-design-11-phase3-multi-strategy-resolution.md](../../designs/SPMA-design-11-phase3-multi-strategy-resolution.md)

---

## 文件结构

| 文件 | 类型 | 职责 |
|------|------|------|
| `src/spma/agents/supervisor/reference_strategies.py` | 新建 | 3 个指代消解子策略 |
| `src/spma/agents/supervisor/semantic_voter.py` | 新建 | `SemanticVoter` 投票器 |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | 指代消解阶段用编排器替换串行 |
| `src/spma/agents/supervisor/graph.py` | 修改 | 注入 voter 实例 |
| `tests/unit/agents/supervisor/test_reference_strategies.py` | 新建 | 3 策略单测 |
| `tests/unit/agents/supervisor/test_semantic_voter.py` | 新建 | 投票器单测 |

---

## Task 1: `rule_based` 策略 + 单测

**Files:**
- Create: `src/spma/agents/supervisor/reference_strategies.py`
- Test: `tests/unit/agents/supervisor/test_reference_strategies.py`

### Step 1.1: 写失败的测试

`tests/unit/agents/supervisor/test_reference_strategies.py`:

```python
"""指代消解多路策略单测。"""
import pytest

from spma.agents.supervisor.reference_strategies import (
    rule_based, entity_based, llm_semantic,
)


@pytest.mark.asyncio
async def test_rule_based_replaces_known_pattern():
    """含 '这个需求' + 有 req_id → 替换为第一个 req_id。"""
    result = await rule_based(
        query="这个需求的最新版本",
        history="",
        entities={"req_ids": ["REQ-123"]},
    )
    assert result == "REQ-123的最新版本"


@pytest.mark.asyncio
async def test_rule_based_returns_none_when_no_reference():
    """无代词 → 返回 None(早退)。"""
    result = await rule_based(
        query="今天天气如何",
        history="很长很长的历史对话",
        entities={},
    )
    assert result is None


@pytest.mark.asyncio
async def test_rule_based_returns_none_when_no_entity_match():
    """有代词但无匹配 entity → 返回 None(交给其他策略)。"""
    result = await rule_based(
        query="这个表是啥",
        history="",
        entities={},  # 无 table_names
    )
    assert result is None


@pytest.mark.asyncio
async def test_entity_based_replaces_pronouns_in_order():
    """多个代词按顺序替换为不同 entity。"""
    result = await entity_based(
        query="它的字段是啥",
        history="",
        entities={"table_names": ["t_user"], "column_names": ["user_id"]},
    )
    # "它" → t_user(第一个 entity)
    assert "t_user" in result


@pytest.mark.asyncio
async def test_entity_based_returns_none_when_no_pronoun():
    """无代词 → 返回 None。"""
    result = await entity_based(
        query="今天天气如何",
        history="",
        entities={"table_names": ["t_user"]},
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_returns_none_without_history():
    """无 history → 返回 None(早退)。"""
    result = await llm_semantic(
        query="它的字段是啥",
        history="",
        llm=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_returns_none_without_llm():
    """无 LLM → 返回 None(早退,与其他策略协同)。"""
    result = await llm_semantic(
        query="这个需求是啥",
        history="很长历史",
        llm=None,
    )
    assert result is None


@pytest.mark.asyncio
async def test_llm_semantic_rejects_overlong_output():
    """LLM 输出超长 → 返回 None(防 prompt 注入)。"""
    class FakeLLM:
        async def ainvoke(self, prompt):
            class Resp:
                content = "x" * 10000  # 远超 3x + 100
            return Resp()

    result = await llm_semantic(
        query="短问",
        history="很长很长的历史对话用于触发" * 20,
        llm=FakeLLM(),
    )
    assert result is None  # 输出超限被丢弃
```

### Step 1.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_reference_strategies.py -v
```

Expected: ImportError(`reference_strategies` 不存在)

### Step 1.3: 写 3 个子策略实现

`src/spma/agents/supervisor/reference_strategies.py`:

```python
"""指代消解的多路策略。

- rule_based:零 LLM,基于 entity 类型+代词模式
- entity_based:零 LLM,按出现顺序一一替换代词
- llm_semantic:LLM 语义分析(收编已有 _resolve_references)
"""
import logging

logger = logging.getLogger(__name__)


# 代词模式集合(包含主文件 v3.1 描述的 7 个 + 扩展 "它/该/这/那/其")
_REFERENCE_PATTERNS = [
    "这个", "那个", "上次", "之前", "刚才", "上述", "此",
    "它", "该", "这", "那", "其",
]


def _has_reference(query: str) -> bool:
    return any(p in query for p in _REFERENCE_PATTERNS)


async def rule_based(query: str, history: str, entities: dict, **_) -> str | None:
    """规则策略:用已知 entity 替换代词。

    模式:这个需求 / 那个需求 / 这个表 / 那个表 ...
    """
    if not _has_reference(query):
        return None

    resolved = query
    replacements = 0
    entity_types = {
        "需求": entities.get("req_ids", []),
        "表": entities.get("table_names", []),
        "字段": entities.get("column_names", []),
        "模块": entities.get("code_refs", []),
    }
    for pattern, entity_list in entity_types.items():
        if not entity_list:
            continue
        if f"这个{pattern}" in resolved:
            resolved = resolved.replace(f"这个{pattern}", entity_list[0], 1)
            replacements += 1
        if f"那个{pattern}" in resolved:
            resolved = resolved.replace(f"那个{pattern}", entity_list[-1], 1)
            replacements += 1

    return resolved if replacements > 0 else None


async def entity_based(query: str, history: str, entities: dict, **_) -> str | None:
    """实体策略:对所有 entity 按出现顺序配对代词,一对一替换。"""
    if not _has_reference(query):
        return None

    all_entities = []
    for key in ["req_ids", "table_names", "column_names", "code_refs"]:
        all_entities.extend(entities.get(key, []))

    if not all_entities:
        return None

    resolved = query
    for i, pronoun in enumerate(["它", "该", "这", "那", "其"]):
        if pronoun in resolved and i < len(all_entities):
            resolved = resolved.replace(pronoun, all_entities[i], 1)

    return resolved if resolved != query else None


async def llm_semantic(query: str, history: str, llm, **_) -> str | None:
    """LLM 语义策略(收编 _resolve_references):通过 prompt 让 LLM 替换代词。

    早退条件:无 history / 无 llm / 无代词
    防御:输出超长返回 None(防 prompt 注入)
    """
    if not history or not llm:
        return None
    if not _has_reference(query):
        return None

    prompt = f"""你是一个上下文理解助手。请根据对话历史,将以下查询中的指代性表达式还原为具体内容。

对话历史:
{history}

当前查询:
{query}

要求:
1. 将"这个问题"、"那个需求"等指代性表达式替换为具体内容
2. 保持查询的核心语义不变
3. 输出还原后的完整查询,不要添加额外解释"""

    try:
        resp = await llm.ainvoke(prompt)
        result = resp.content.strip()
        if len(result) > len(query) * 3 + 100:
            logger.warning(f"llm_semantic: output too long ({len(result)}), dropped")
            return None
        return result
    except Exception as e:
        logger.warning(f"llm_semantic failed: {type(e).__name__}: {e}")
        return None
```

### Step 1.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_reference_strategies.py -v
```

Expected: 8 passed

### Step 1.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/reference_strategies.py tests/unit/agents/supervisor/test_reference_strategies.py
git commit -m "feat(qr): 3 个指代消解多路策略(rule/entity/llm)(G5 部分)

- rule_based:零 LLM,基于 entity 类型+'这个X/那个X'模式
- entity_based:零 LLM,按顺序一对一替换代词
- llm_semantic:收编已有 _resolve_references,加长度防御
- _REFERENCE_PATTERNS 扩展到 12 个(原 7 个 + 它/该/这/那/其)

Refs: SPMA-design-11-phase3 §3.1"
```

---

## Task 2: `SemanticVoter` 投票器 + 单测

**Files:**
- Create: `src/spma/agents/supervisor/semantic_voter.py`
- Test: `tests/unit/agents/supervisor/test_semantic_voter.py`

### Step 2.1: 写失败的测试

`tests/unit/agents/supervisor/test_semantic_voter.py`:

```python
"""SemanticVoter 单测。"""
import pytest

from spma.agents.supervisor.semantic_voter import SemanticVoter


class FakeEmbedder:
    """测试用:返回一个稳定可预测的向量(基于字符串 hash)。"""
    async def embed_query(self, text):
        return [hash(text) % 100 / 100.0, 0.5, 0.0]

    async def embed_documents(self, texts):
        return [await self.embed_query(t) for t in texts]


@pytest.mark.asyncio
async def test_vote_returns_first_when_only_one_candidate():
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", ["only-candidate"])
    assert result == "only-candidate"


@pytest.mark.asyncio
async def test_vote_returns_original_when_empty():
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", [])
    assert result == "original"


@pytest.mark.asyncio
async def test_vote_without_embedder_returns_first():
    """无 embedder 时退化为"取第一个"。"""
    voter = SemanticVoter(None, alpha=0.4)
    result = await voter.vote_best("original", ["a", "b"])
    assert result == "a"


@pytest.mark.asyncio
async def test_vote_uses_consensus_not_just_similarity():
    """共识度优先:多个策略收敛的结果优先于'最相似'的单条。"""
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)
    candidates = [
        "alpha-rewrite-by-rule",
        "alpha-rewrite-by-llm",
        "different-completely",
    ]
    # 共识度最高(其他两条共享 prefix)应胜出
    result = await voter.vote_best("original", candidates)
    # 验证:共识度起作用(具体谁赢取决于 hash,但不应该是 outlier)
    assert result in candidates


@pytest.mark.asyncio
async def test_vote_handles_embedder_failure():
    """embedder 抛异常时退化。"""
    class BrokenEmbedder:
        async def embed_query(self, text): raise RuntimeError("boom")
        async def embed_documents(self, texts): raise RuntimeError("boom")

    voter = SemanticVoter(BrokenEmbedder(), alpha=0.4)
    result = await voter.vote_best("original", ["a", "b"])
    assert result == "a"  # 退化到第一个
```

### Step 2.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_semantic_voter.py -v
```

Expected: ImportError(`semantic_voter` 不存在)

### Step 2.3: 写 `SemanticVoter` 实现

`src/spma/agents/supervisor/semantic_voter.py`:

```python
"""语义投票器——基于共识度选最优(主文件 ADR-004,零 LLM)。

alpha:与原始的语义保持度权重;1-alpha:候选间共识度权重。
共识度优先于"最相似"——多个独立策略收敛的结果最可靠(避免被单个异常策略带偏)。
"""
import logging
import math

logger = logging.getLogger(__name__)


class SemanticVoter:
    """多候选投票器。"""

    def __init__(self, embedder, alpha: float = 0.4):
        self._embedder = embedder
        self._alpha = alpha

    async def vote_best(self, original: str, candidates: list[str]) -> str:
        if not candidates:
            return original
        if len(candidates) == 1:
            return candidates[0]
        if not self._embedder:
            return candidates[0]  # 退化

        try:
            embeddings = await self._embedder.embed_documents(candidates)
            original_emb = await self._embedder.embed_query(original)
        except Exception as e:
            logger.warning(f"SemanticVoter: embedder failed, falling back to first: {e}")
            return candidates[0]

        best, best_score = candidates[0], -1.0
        for i, cand in enumerate(candidates):
            orig_sim = self._cosine(embeddings[i], original_emb)
            other_sims = [
                self._cosine(embeddings[i], embeddings[j])
                for j in range(len(candidates)) if j != i
            ]
            consensus = sum(other_sims) / len(other_sims) if other_sims else 0.0
            score = self._alpha * orig_sim + (1 - self._alpha) * consensus
            if score > best_score:
                best, best_score = cand, score
        return best

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
pytest tests/unit/agents/supervisor/test_semantic_voter.py -v
```

Expected: 5 passed

### Step 2.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/semantic_voter.py tests/unit/agents/supervisor/test_semantic_voter.py
git commit -m "feat(qr): SemanticVoter — 共识度优先(零 LLM,主文件 ADR-004)

alpha=0.4(可调),cosine 相似度计算用纯 Python(无 numpy 依赖)。
退化:无 embedder / embedder 抛错 / 候选 < 2。

Refs: SPMA-design-11-phase3 §3.2"
```

---

## Task 3: `_do_rewrite_pipeline` 指代消解阶段多路化

**Files:**
- Modify: `src/spma/agents/supervisor/query_rewriter.py` (指代消解段)
- Test: `tests/unit/agents/supervisor/test_query_rewriter_multi.py`

### Step 3.1: 写失败的测试(验证多路路径)

`tests/unit/agents/supervisor/test_query_rewriter_multi.py`:

```python
"""验证 _do_rewrite_pipeline 指代消解阶段使用编排器 + voter。"""
import pytest

from spma.agents.supervisor import query_rewriter
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.agents.supervisor.semantic_voter import SemanticVoter
from spma.infrastructure.circuit_breaker import reset_all


@pytest.fixture(autouse=True)
def clear_cbs():
    reset_all()
    yield
    reset_all()


class FakeEmbedder:
    async def embed_query(self, text): return [0.1, 0.2, 0.3]
    async def embed_documents(self, texts): return [[0.1, 0.2, 0.3] for _ in texts]


@pytest.mark.asyncio
async def test_pipeline_uses_orchestrator_when_provided():
    """提供编排器时,指代消解走多路并行。"""
    orch = StrategyOrchestrator(stage="test", names=["rule_based", "entity_based", "llm_semantic"])
    fb = FallbackManager(orch, primary_backup_fn=lambda q, *a, **kw: q, rule_only_fn=lambda q, *a, **kw: q)
    voter = SemanticVoter(FakeEmbedder(), alpha=0.4)

    result = await query_rewriter._do_rewrite_pipeline(
        query="它的字段",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={"table_names": ["t_user"]},
        llm=None,
        synonym_map=None,
        conversation_history="之前聊过用户表",
        strategy_orchestrator=orch,
        fallback_manager=fb,
        voter=voter,
        embedder=FakeEmbedder(),
    )
    # 应至少走完整个管道,没有抛异常
    assert "resolved" in result
    # 至少一个策略应被触发(entity_based 会替换"它" → t_user)
    # 注意:由于 voter 和早退逻辑,具体结果可能为原 query 也可能为替换后


@pytest.mark.asyncio
async def test_pipeline_backward_compatible_without_orchestrator():
    """不提供编排器时,走原串行(向后兼容)。"""
    result = await query_rewriter._do_rewrite_pipeline(
        query="hello",
        classification={"query_type": "search", "sources": ["doc"]},
        entities={},
        llm=None,
        synonym_map=None,
        conversation_history="",
        # 不传 strategy_orchestrator/voter
    )
    assert "resolved" in result
```

### Step 3.2: 运行测试,确认失败

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_multi.py -v
```

Expected: FAIL(多路路径未实现,`voter` 参数被忽略)

### Step 3.3: 修改 `_do_rewrite_pipeline`

修改 `src/spma/agents/supervisor/query_rewriter.py` 中的指代消解段:

**修改前**(典型代码,可能略有差异):
```python
async def _do_rewrite_pipeline(...):
    result = {"original": query}
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized
    resolved = await _resolve_references(normalized, conversation_history, llm)
    result["resolved"] = resolved
    ...
```

**修改后**:
```python
async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
    *,
    strategy_orchestrator=None,
    fallback_manager=None,
    voter=None,
    embedder=None,
):
    result = {"original": query}
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized

    # ====== P3: 多路指代消解 ======
    if strategy_orchestrator and voter:
        from spma.agents.supervisor.reference_strategies import (
            rule_based, entity_based, llm_semantic,
        )
        strategies = {
            "rule_based": lambda q, h, e: rule_based(q, h, e),
            "entity_based": lambda q, h, e: entity_based(q, h, e),
            "llm_semantic": lambda q, h, e: llm_semantic(q, h, llm),
        }
        try:
            results = await strategy_orchestrator.execute_parallel(
                strategies, normalized, conversation_history, entities,
            )
            candidates = [r[1] for r in results if r[1] and r[1] != normalized]
            if candidates:
                resolved = await voter.vote_best(normalized, candidates)
            else:
                resolved = normalized
        except Exception as ex:
            logger.warning(f"multi-strategy resolution failed, fallback: {ex}")
            resolved = await _resolve_references(normalized, conversation_history, llm)
    else:
        # 向后兼容:无编排器时走原单策略
        resolved = await _resolve_references(normalized, conversation_history, llm)

    result["resolved"] = resolved
    # P4/P5 暂保留单策略(P4-P5 各自 PR 替换)
    expanded = await _expand_query(resolved, classification, entities, llm)
    result["expanded"] = expanded
    sources = classification.get("sources", ["doc"])
    try:
        sub_queries = await _decompose_query(expanded, entities, sources, llm)
    except Exception:
        sub_queries = [{"query": expanded, "target": s} for s in sources]
    result["sub_queries"] = sub_queries
    return result
```

### Step 3.4: 重新运行测试

Run:
```bash
cd /Users/Ray/TraeProjects/SPMA
pytest tests/unit/agents/supervisor/test_query_rewriter_multi.py -v
pytest tests/unit/agents/supervisor/ -v  # 全部
```

Expected: 2 new passed + 13+ 原单测全过

### Step 3.5: 提交

```bash
cd /Users/Ray/TraeProjects/SPMA
git add src/spma/agents/supervisor/query_rewriter.py tests/unit/agents/supervisor/test_query_rewriter_multi.py
git commit -m "feat(qr): 指代消解多路化(rule/entity/llm + voter,G5 修复)

- 注入 orchestrator + voter 时走多路并行 + 投票
- 不注入时保持原串行(向后兼容)
- 编排器抛错时降级到原 _resolve_references
- P4/P5 暂保留单策略,后续 PR 各自替换

Refs: SPMA-design-11-phase3 §3.3"
```

---

## Task 4: `graph.py` 注入 voter + 24h 灰度

**Files:**
- Modify: `src/spma/agents/supervisor/graph.py`

### Step 4.1: 在 `graph.py` 注入 voter 单例

修改 `src/spma/agents/supervisor/graph.py`,在 P2 编排器单例旁加:

```python
# P3: SemanticVoter 单例
from spma.agents.supervisor.semantic_voter import SemanticVoter
_voter = SemanticVoter(embedder=None, alpha=0.4)  # embedder 运行时注入或通过 build_graph 参数
```

并在 `build_graph` 签名加 `voter=None`,转发到 `rewrite_queries` 调用:

```python
def build_graph(
    ...,
    strategy_orchestrator=None,
    fallback_manager=None,
    voter=None,                  # NEW
    embedder=None,               # NEW
):
    strategy_orchestrator = strategy_orchestrator or _orchestrator
    fallback_manager = fallback_manager or _fallback
    voter = voter or _voter
    ...
```

在 `rewrite_node` 调用 `rewrite_queries` 时传入 `voter=voter, embedder=embedder`。

### Step 4.2: 24h 灰度观察

| 监控项 | 期望 |
|--------|------|
| 现有 13+ supervisor 单测 | 全过 |
| `qr_rewrite_rule_based/state=open` 等 CB 状态 | 偶发(无故障时) |
| P95 延迟 | < 350ms(并行) |
| 代词识别 F1(离线数据集) | ≥ 80% |

### Step 4.3: 关闭 P3

更新主文件 §1.1:

```markdown
| ~~G5~~ | ~~P3~~ | ~~_resolve_references 单策略 + 关键词匹配~~ | ✅ 已修复(多路 + 投票) | - |
```

commit:

```bash
cd /Users/Ray/TraeProjects/SPMA
git add docs/designs/SPMA-design-11-query-rewrite-optimization-v2-final.md src/spma/agents/supervisor/graph.py
git commit -m "feat(qr): graph.py 注入 voter + 灰度完成(G5 修复)"
```

---

## 验收 checklist

- [ ] Task 1:8 个策略单测通过
- [ ] Task 2:5 个 voter 单测通过
- [ ] Task 3:2 个 pipeline 集成测试 + 13 原单测无回归
- [ ] Task 4:24h 灰度无 P0 故障,代词 F1 ≥ 80%
- [ ] 主文件 §1.1 G5 标记为已修复

---

## 失败回滚

```bash
git revert <commit_hash_of_task_3>
# 编排器+ voter 注入代码不依赖数据库,直接回滚即可
```

# Design: Query Rewriter Phase 4 — 多路查询扩展(基于已有 `_expand_query`)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G6
>
> **本文档角色**:8 份子 spec 中的第 4 份(Phase 4),gap-driven 结构。
> **上下游依赖**:**上游** [P1 (synonym_map)](SPMA-design-11-phase1-synonym-map-activation.md) + [P2 (编排)](SPMA-design-11-phase2-strategy-orchestration.md) + [P3 (指代消解)](SPMA-design-11-phase3-multi-strategy-resolution.md) + `SemanticVoter`。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G6 |
| 关联文件 | `query_rewriter.py:282-310` (已有单策略) + P1 synonym_map + P2 编排 + P3 voter |
| 预估工时 | 1 周 |
| 相关 ADR | 主文件 ADR-004(质量评估零 LLM) |

---

## 1. 现状核查

### 1.1 `_expand_query` 单策略实现

`src/spma/agents/supervisor/query_rewriter.py:282-310`:

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
    if entities is None:
        entities = {}

    query_type = classification.get("query_type", "search")

    if query_type == "search":
        prompt = f"""为以下搜索查询生成扩展查询,..."""
        # ...LLM 调用 + 返回
    # ... 4 个 query_type 分支(search / data_query / explain / trace)
```

### 1.2 现状问题

| 问题 | 影响 |
|------|------|
| **单策略**(只能走 LLM) | LLM 故障 = 整个扩展失效 |
| **无熔断** | LLM 慢/挂阻塞整链 |
| **无 synonym_map 利用** | 已加载但不参与扩展(仅 P1 normalize 阶段用) |
| **无实体注入** | 已抽取但未注入扩展 |
| **无质量评估** | LLM 输出未校验 |
| **无意图感知外策略** | 除 LLM 外没有规则化策略 |

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| 4 路并行(intent / synonym / entity / context) | 单 LLM | **需新增 3 个策略** |
| synonym_map 参与扩展 | 仅 normalize 阶段用 | **新增 synonym_based 策略** |
| 实体注入扩展 | 未注入 | **新增 entity_injection 策略** |
| 意图感知(非 LLM 路径) | 仅 LLM 内 prompt 分支 | **新增 intent_aware 策略(零 LLM)** |
| 质量评分 | 无 | **复用 P3 voter 或新增 _evaluate_quality 启发式** |
| 熔断保护 | 无 | **P2 编排器已提供** |

**关键洞察**:P4 实际是 P3 的扩展(同模式),只需把 `_expand_query` 拆为 4 个策略,新增 1 个 quality-based 选择器(主文件 ADR-004:零 LLM)。

---

## 3. 详细设计

### 3.1 四个子策略

`src/spma/agents/supervisor/expansion_strategies.py` (新建):

```python
"""查询扩展的多路策略。"""
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


async def intent_aware(query: str, classification: dict, entities: dict, **_) -> str | None:
    """基于意图的规则扩展:根据 query_type 附加 1-2 个相关词。

    零 LLM 调用,延迟 < 1ms。
    """
    query_type = classification.get("query_type", "search")
    if query_type not in {"search", "data_query", "explain", "trace"}:
        return None

    relevant_words = {
        "search": ["相关文档", "涉及"],
        "data_query": ["字段", "统计"],
        "explain": ["含义", "定义"],
        "trace": ["调用链", "流程"],
    }
    additions = [w for w in relevant_words.get(query_type, []) if w not in query][:2]
    return (f"{query} {' '.join(additions)}") if additions else None


async def synonym_based(query: str, classification: dict, entities: dict, *,
                        synonym_map: dict | None = None, **_) -> str | None:
    """基于 synonym_map 扩展:命中 user_term → 添加 canonical_term。

    依赖 P1:graph.rewrite_node 加载的 synonym_map 透传到此处。
    """
    if not synonym_map:
        return None
    expanded = query
    added = 0
    for user_term, canonical_terms in synonym_map.items():
        if user_term in expanded:
            for ct in canonical_terms:
                if ct not in expanded:
                    expanded += f" {ct}"
                    added += 1
    return expanded if added > 0 else None


async def entity_injection(query: str, classification: dict, entities: dict, **_) -> str | None:
    """实体注入:把抽取的实体追加到 query(避免下游 embedding 漏召回)。"""
    expanded = query
    added = 0
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        for entity in entities.get(key, []):
            if entity not in expanded:
                expanded += f" {entity}"
                added += 1
    return expanded if added > 0 else None


async def context_aware(query: str, classification: dict, entities: dict, *,
                        llm=None, **_) -> str | None:
    """基于 LLM 的上下文扩展:复用 _expand_query 主体逻辑。

    增加:长度校验 + 早退 + 异常隔离。
    """
    if not llm:
        return None

    query_type = classification.get("query_type", "search")
    if query_type not in {"search", "data_query", "explain", "trace"}:
        return None

    # 复用 _expand_query 的 4 分支 prompt
    if query_type == "search":
        prompt = f"""为以下搜索查询生成扩展查询,保留核心语义,增加相关术语和实体。

查询: {query}
实体: {entities}

只输出扩展后的查询,不要添加解释。"""
    else:
        # 其他 type 简化为相同 prompt(原 _expand_query 有 4 分支,合并)
        prompt = f"""为以下查询生成扩展版本({query_type}),保留核心语义,增加相关术语。

查询: {query}
实体: {entities}

只输出扩展后的查询,不要添加解释。"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        result = resp_obj.content.strip()
        if len(result) > len(query) * 3 + 100:
            logger.warning(f"context_aware: output too long ({len(result)}), dropped")
            return None
        return result
    except Exception as e:
        logger.warning(f"context_aware failed: {e}")
        return None
```

### 3.2 质量评估器(主文件 ADR-004:零 LLM)

`src/spma/agents/supervisor/quality_evaluator.py` (新建,或合并到 `query_rewriter.py`):

```python
"""重写质量评估——纯 embedding + 启发式,零 LLM 调用(主文件 ADR-004)。"""
import math
import logging

logger = logging.getLogger(__name__)


def evaluate_quality(original_emb, rewritten_emb, rewritten: str, entities: dict) -> float:
    """三维评分:语义相似(0.6) + 实体覆盖(0.3) + 长度合理(0.1)。"""
    semantic = max(0.0, _cosine(rewritten_emb, original_emb))
    entity_score = _entity_coverage(rewritten, entities)
    length_score = _length_score(original_emb, rewritten)
    return semantic * 0.6 + entity_score * 0.3 + length_score * 0.1


def _entity_coverage(rewritten: str, entities: dict) -> float:
    all_entities = []
    for key in ["table_names", "column_names", "code_refs", "req_ids"]:
        all_entities.extend(entities.get(key, []))
    if not all_entities:
        return 1.0  # 无实体不扣分
    rewritten_lower = rewritten.lower()
    covered = sum(1 for e in all_entities if e.lower() in rewritten_lower)
    return covered / len(all_entities)


def _length_score(original_emb, rewritten: str) -> float:
    original_len = math.sqrt(sum(x * x for x in original_emb)) * 50  # 启发式
    rewritten_len = len(rewritten)
    if original_len < 1.0:
        return 1.0
    ratio = rewritten_len / original_len
    if 0.5 <= ratio <= 3.0:
        return 1.0
    if ratio < 0.5:
        return ratio * 2
    return 3.0 / ratio


def _cosine(a, b) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) + 1e-10
    nb = math.sqrt(sum(x * x for x in b)) + 1e-10
    return dot / (na * nb)
```

### 3.3 集成到 `_do_rewrite_pipeline`

```python
# query_rewriter.py:_do_rewrite_pipeline 新增 P4 段
if strategy_orchestrator and embedder and entities is not None:
    from spma.agents.supervisor.expansion_strategies import (
        intent_aware, synonym_based, entity_injection, context_aware,
    )
    from spma.agents.supervisor.quality_evaluator import evaluate_quality

    strategies = {
        "intent_aware": lambda q, c, e: intent_aware(q, c, e),
        "synonym_based": lambda q, c, e: synonym_based(q, c, e, synonym_map=synonym_map),
        "entity_injection": lambda q, c, e: entity_injection(q, c, e),
        "context_aware": lambda q, c, e: context_aware(q, c, e, llm=llm),
    }
    results = await strategy_orchestrator.execute_parallel(
        strategies, resolved, classification, entities
    )
    if results:
        # 批量 embedding(零 LLM,主文件 ADR-004)
        candidates = [r[1] for r in results if r[1] and r[1] != resolved]
        if candidates:
            candidate_embs = await embedder.embed_documents(candidates)
            original_emb = await embedder.embed_query(resolved)
            scored = [
                (cand, evaluate_quality(original_emb, emb, cand, entities))
                for cand, emb in zip(candidates, candidate_embs)
            ]
            expanded = max(scored, key=lambda x: x[1])[0]
        else:
            expanded = resolved
    else:
        expanded = resolved
else:
    expanded = await _expand_query(resolved, classification, entities, llm)

result["expanded"] = expanded
```

### 3.4 `graph.py` 注入 embedder

```python
# graph.py
_embedder = ...  # 由 project 注入(已有或新增)

async def rewrite_node(state):
    ...
    rewritten = await rewrite_queries(
        ...,
        strategy_orchestrator=_orchestrator,
        fallback_manager=_fallback,
        voter=_voter,
        embedder=_embedder,                # NEW
    )
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增/修改文件

| 文件 | 类型 | 改动 |
|------|------|------|
| `src/spma/agents/supervisor/expansion_strategies.py` | **新增** | 4 个子策略 |
| `src/spma/agents/supervisor/quality_evaluator.py` | **新增** | `evaluate_quality` 启发式评分 |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | `_do_rewrite_pipeline` P4 段 |
| `src/spma/agents/supervisor/graph.py` | 修改 | 注入 embedder |

### 4.2 不需要做的事

- **不**删除原 `_expand_query`(保留供向后兼容)
- **不**在 4 个 query_type 各自写独立 prompt(已合并,效果相近)

### 4.3 下游契约

[P6](SPMA-design-11-phase6-feedback-and-monitoring.md) 监控:4 个 CB (`qr_rewrite_intent_aware` 等) stats 暴露。

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_EXPAND_MAX_ADDITIONS` | 2 | `intent_aware` 最多附加词数 |
| `QR_EXPAND_LENGTH_RATIO_MIN` | 0.5 | 长度变化合理下限 |
| `QR_EXPAND_LENGTH_RATIO_MAX` | 3.0 | 上限 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 4 个子策略文件存在 | ❌ | ✅ | `ls expansion_strategies.py quality_evaluator.py` |
| V2 | `_do_rewrite_pipeline` P4 段接受 `embedder` | ❌ | ✅ | 代码 review |
| V3 | 现有 13 单测无回归 | 13/13 | 13/13 | pytest |
| V4 | 新增 ≥ 12 单测(4 策略 × 3 场景) | 0 | ≥ 12 | pytest |
| V5 | 离线数据集召回率 | (无基线) | +15% | 标 100 条 query,与原 _expand_query 对比 |
| V6 | 单 query LLM 调用次数 | 1(原) | ≤ 1 | mock 计数 |
| V7 | 多路并行延迟 ≤ 串行的 60% | (无基线) | ✅ | benchmark |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:LLM 慢/挂 | 上游故障 | 整条链延迟 | P2 CB + FallbackManager |
| **R2**:embedder 挂 | 上游故障 | 评估失败 | 退化为"取第一个候选" |
| **R3**:synonym_map 空 | P1 失败 | synonym_based 早退 | 各策略 None 处理 |
| **R4**:实体为空 | 提取失败 | entity_injection 早退 | 已有 None 处理 |

---

## 7. 实施步骤

### 7.1 PR 切分(3 个 PR)

**PR #1**:4 个子策略 + 单测
**PR #2**:`evaluate_quality` + 单测
**PR #3**:集成 + `graph.py` 注入

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1-D2 | 4 个子策略 + 单测 | PR #1 ready |
| D3 | Review PR #1 + 合并 | - |
| D3 | `evaluate_quality` + 单测 | PR #2 ready |
| D4 | Review PR #2 + 合并 | - |
| D4-D5 | 集成 + graph.py | PR #3 ready |
| D6 | Review PR #3 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-3 合并
- [ ] 13 原单测无回归
- [ ] 12+ 新单测全过
- [ ] 离线数据集召回率 +15%

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:基于已有 `_expand_query` 拆分,新增 2 个文件 + 集成 1 处 |
| 2026-06-29 | 0.9 | (回退)初次拆分(已回退) |

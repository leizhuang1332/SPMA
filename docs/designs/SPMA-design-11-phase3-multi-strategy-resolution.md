# Design: Query Rewriter Phase 3 — 多路指代消解(基于已有 `_resolve_references`)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G5
>
> **本文档角色**:8 份子 spec 中的第 3 份(Phase 3),gap-driven 结构。
> **上下游依赖**:**上游** [P2 (编排器)](SPMA-design-11-phase2-strategy-orchestration.md) `StrategyOrchestrator`;**下游** P6 监控消费。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G5 |
| 关联文件 | `query_rewriter.py:243-279` (已有单策略) + `strategy_orchestrator.py` (P2 新建) |
| 预估工时 | 1 周 |
| 相关 ADR | 借鉴 Dify Step-Back Prompting(主文件 §4.1) |

---

## 1. 现状核查(实际代码)

### 1.1 `_resolve_references` 单策略实现

`src/spma/agents/supervisor/query_rewriter.py:243-279`:

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

    prompt = f"""你是一个上下文理解助手。请根据对话历史,..."""

    try:
        resp_obj = await llm.ainvoke(prompt)
        return resp_obj.content.strip()
    except Exception as e:
        logger.warning(f"指代消解失败: {e}")
        return query  # Fallback to original query on error
```

### 1.2 现状问题

| 问题 | 影响 |
|------|------|
| **关键词触发**(`["这个", "那个", ...]` 7 个词) | 漏判("其"、"该" 未覆盖);误判(陈述句含"这个"也会触发) |
| **单策略**(只能走 LLM) | LLM 故障 = 整个指代消解失效 |
| **无熔断** | LLM 慢/挂会阻塞整条 rewrite 链 |
| **无质量评估** | LLM 输出未校验(长度未限) |
| **异常仅 logger.warning** | 异常被吞,无 metric 上报 |

---

## 2. 差距分析(目标 vs 现实)

| 目标 | 现实 | 差距 |
|------|------|------|
| 多路并行(rule/llm/entity) | 单 LLM | **需新增 2 个策略** |
| 关键词触发 + 实体触发 + LLM 触发 | 仅关键词 | **需多触发源** |
| 输出合法性校验(长度限) | 无校验 | **需加 3x + 100 长度限** |
| 投票器选最优(共识度) | 直接用 LLM 结果 | **需 SimpleVoter** |
| 熔断保护 | 无 | **P2 编排器已提供** |
| 失败降级 | 仅 logger | **P2 FallbackManager 已提供** |

**关键洞察**:P3 实际只需:
1. 拆分现有 `_resolve_references` 为 3 个子策略(rule / entity / llm)
2. 新增 `SemanticVoter` 投票器
3. 在 `_do_rewrite_pipeline` 用 P2 `StrategyOrchestrator.execute_parallel()` 调度

**不**需要全删重写现有函数,**保留**为"LLM 策略"的实现。

---

## 3. 详细设计

### 3.1 三个子策略

`src/spma/agents/supervisor/reference_strategies.py` (新建):

```python
"""指代消解的多路策略(基于已有 _resolve_references 拆分)。"""
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)


async def rule_based(query: str, history: str, entities: dict, **_) -> str | None:
    """规则策略:用已知 entity 替换代词("这个需求" → "需求 #123")。

    零 LLM 调用,延迟 < 1ms。
    Returns None 表示本策略无能为力(交给其他策略)。
    """
    reference_patterns = ["这个", "那个", "上次", "之前", "刚才", "上述", "此", "其", "该"]
    if not any(p in query for p in reference_patterns):
        return None  # 无代词,跳过

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

    if replacements == 0:
        return None  # 有代词但没匹配上
    return resolved


async def entity_based(query: str, history: str, entities: dict, **_) -> str | None:
    """实体策略:对所有 entity 按出现顺序配对代词,一对一替换。"""
    reference_patterns = ["它", "该", "这", "那", "其"]
    if not any(p in query for p in reference_patterns):
        return None

    all_entities = []
    for key in ["req_ids", "table_names", "column_names", "code_refs"]:
        all_entities.extend(entities.get(key, []))

    if not all_entities:
        return None

    resolved = query
    for i, pronoun in enumerate(reference_patterns):
        if pronoun in resolved and i < len(all_entities):
            resolved = resolved.replace(pronoun, all_entities[i], 1)

    return resolved if resolved != query else None


async def llm_semantic(query: str, history: str, llm, **_) -> str | None:
    """LLM 语义策略:复用已有 _resolve_references 主体逻辑。

    增加:
    - 长度校验(防 prompt 注入撑爆)
    - 显式返回 None(便于其他策略并行)
    """
    if not history:
        return None
    if not llm:
        return None

    # 复用已有触发判断 + prompt
    reference_patterns = ["这个", "那个", "上次", "之前", "刚才", "上述", "此"]
    if not any(p in query for p in reference_patterns):
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
        resp_obj = await llm.ainvoke(prompt)
        result = resp_obj.content.strip()
        # 防御:输出长度不超过原文 3 倍 + 100
        if len(result) > len(query) * 3 + 100:
            logger.warning(f"llm_semantic: output too long ({len(result)} chars), dropped")
            return None
        return result
    except Exception as e:
        logger.warning(f"llm_semantic failed: {e}")
        return None
```

### 3.2 `SemanticVoter` 投票器

`src/spma/agents/supervisor/semantic_voter.py` (新建):

```python
"""语义投票器——基于共识度选最优。

主文件 §3.2 ADR:共识度优先,非"最相似于原始"。
"""
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)


class SemanticVoter:
    """多候选投票器。alpha:与原始的相似度权重;1-alpha:候选间共识度。"""

    def __init__(self, embedder, alpha: float = 0.4):
        self._embedder = embedder
        self._alpha = alpha

    async def vote_best(self, original: str, candidates: list[str]) -> str:
        if not candidates:
            return original
        if len(candidates) == 1:
            return candidates[0]
        if not self._embedder:
            # 无 embedder:退化为"取第一个"(向后兼容)
            return candidates[0]

        try:
            embeddings = await self._embedder.embed_documents(candidates)
            original_emb = await self._embedder.embed_query(original)
        except Exception as e:
            logger.warning(f"SemanticVoter: embedder failed, falling back to first: {e}")
            return candidates[0]

        # 计算 (alpha*orig_sim + (1-alpha)*consensus),取最高
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
        import math
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a)) + 1e-10
        nb = math.sqrt(sum(x * x for x in b)) + 1e-10
        return dot / (na * nb)
```

### 3.3 集成到 `_do_rewrite_pipeline`

修改 `query_rewriter.py:84-130`(P2 已预留接口):

```python
async def _do_rewrite_pipeline(
    query, classification, entities, llm, synonym_map, conversation_history,
    *, strategy_orchestrator=None, fallback_manager=None, voter=None, embedder=None,
):
    result = {"original": query}
    normalized = await _normalize_with_synonyms(query, synonym_map, entities)
    result["normalized"] = normalized

    # ====== P3: 多路指代消解 ======
    if strategy_orchestrator and voter:
        from spma.agents.supervisor.reference_strategies import (
            rule_based, entity_based, llm_semantic
        )
        # 每个策略注入自己需要的额外参数
        async def _llm_wrapped(q, h, e):
            return await llm_semantic(q, h, llm)

        strategies = {
            "rule_based": lambda q, h, e: rule_based(q, h, e),
            "entity_based": lambda q, h, e: entity_based(q, h, e),
            "llm_semantic": _llm_wrapped,
        }
        results = await strategy_orchestrator.execute_parallel(
            strategies, normalized, conversation_history, entities
        )
        candidates = [r[1] for r in results if r[1] and r[1] != normalized]
        if candidates:
            resolved = await voter.vote_best(normalized, candidates)
        else:
            resolved = normalized
    else:
        # 向后兼容:无编排器时走原单策略
        resolved = await _resolve_references(normalized, conversation_history, llm)

    result["resolved"] = resolved

    # P4/P5 类似模式,留待对应 PR
    if strategy_orchestrator:
        # 临时:单策略 + FallbackManager
        try:
            expanded = await _expand_query(resolved, classification, entities, llm)
        except Exception:
            expanded = resolved
    else:
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

### 3.4 `graph.py` 注入 voter

```python
# graph.py 启动时
from spma.agents.supervisor.semantic_voter import SemanticVoter
# 假设已有 embedder 实例(由 project 注入)
_voter = SemanticVoter(embedder=embedder, alpha=0.4)

async def rewrite_node(state):
    ...
    rewritten = await rewrite_queries(
        ...,
        strategy_orchestrator=_orchestrator,
        fallback_manager=_fallback,
        voter=_voter,                    # NEW
    )
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增/修改文件

| 文件 | 类型 | 改动 |
|------|------|------|
| `src/spma/agents/supervisor/reference_strategies.py` | **新增** | 3 个子策略(`rule_based` / `entity_based` / `llm_semantic`) |
| `src/spma/agents/supervisor/semantic_voter.py` | **新增** | `SemanticVoter` 类 |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | `_do_rewrite_pipeline` 多路分支 |
| `src/spma/agents/supervisor/graph.py` | 修改 | 注入 voter |

### 4.2 不需要做的事

- **不**删除原 `_resolve_references`(保留为 `llm_semantic` 策略的引用,或完全删除并仅依赖多路版本)
- **不**在 P3 引入新熔断器(由 P2 编排器统一管)

### 4.3 下游契约

[P6](SPMA-design-11-phase6-feedback-and-monitoring.md) 监控:
- 3 个子策略的成功率通过 `get_circuit_breaker("qr_rewrite_rule_based").stats` 暴露
- 投票器决策通过 `voter.vote_best()` 日志(本 Phase 留 TODO,P6 引入结构化日志)

### 4.4 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_RESOLVER_ALPHA` | 0.4 | `SemanticVoter` 中"与原始相似度"权重 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 3 个子策略文件存在 | ❌ | ✅ | `ls reference_strategies.py semantic_voter.py` |
| V2 | `_do_rewrite_pipeline` 接受 `voter` 参数 | ❌ | ✅ | 代码 review |
| V3 | 现有 13 个单测全过(无回归) | 13/13 | 13/13 | pytest |
| V4 | 新增 ≥ 8 单测(3 个策略 × 多场景) | 0 | ≥ 8 | pytest |
| V5 | 代词识别 F1(离线数据集) | (无数据集) | ≥ 80% | 标 50 条测试集,跑 P3 + P0 对比 |
| V6 | 多路并行延迟 ≤ 串行的 60% | (无基线) | ✅ | benchmark |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:LLM 慢/挂 | 上游故障 | 整条链延迟 | P2 熔断器 + FallbackManager |
| **R2**:3 个策略输出相同 | 输入无代词 | 浪费 3x LLM 调用 | 各策略早退(`if not has_reference: return None`) |
| **R3**:投票器嵌入失败 | embedder 挂 | 退化为"取第一个" | `_cosine` 内置 try/except |
| **R4**:实体为空 | 提取失败 | entity_based 早退 | 已有 None 处理 |

---

## 7. 实施步骤

### 7.1 PR 切分(3 个 PR)

**PR #1**:3 个子策略 + 单测
- 新增 `reference_strategies.py` + `tests/test_reference_strategies.py` (≥ 6 case)
- 合并标准:V4 中 6 个 case 全过

**PR #2**:`SemanticVoter` + 单测
- 新增 `semantic_voter.py` + `tests/test_semantic_voter.py` (≥ 4 case)
- 合并标准:共识度算法正确(可手动验证)

**PR #3**:集成到 `_do_rewrite_pipeline` + `graph.py` 注入
- 修改 `query_rewriter.py` 多路分支
- 修改 `graph.py` 注入 voter
- 合并标准:13 个原单测无回归 + 端到端跑通

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1-D2 | 3 个子策略 + 单测 | PR #1 ready |
| D3 | Review PR #1 + 合并 | - |
| D3 | `SemanticVoter` + 单测 | PR #2 ready |
| D4 | Review PR #2 + 合并 | - |
| D4-D5 | 集成 + graph.py | PR #3 ready |
| D6 | Review PR #3 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-3 合并
- [ ] 13 原单测无回归
- [ ] 8+ 新单测全过
- [ ] 离线数据集 F1 ≥ 80%
- [ ] 监控:`qr_rewrite_*` CB 状态在 Prometheus 可查

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:基于已有 `_resolve_references` 拆分,新增 2 个文件 + 集成 1 处 |
| 2026-06-29 | 0.9 | (回退)初次拆分,假设 _resolve_references 不存在(已回退) |

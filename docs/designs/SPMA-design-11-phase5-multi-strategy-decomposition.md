# Design: Query Rewriter Phase 5 — 多路查询分解(基于已有 `_decompose_query`)

> **总览与索引**:[SPMA-design-11-query-rewrite-optimization-v2-final.md](SPMA-design-11-query-rewrite-optimization-v2-final.md) §1.1 中 G7
>
> **本文档角色**:8 份子 spec 中的第 5 份(Phase 5),gap-driven 结构。
> **上下游依赖**:**上游** [P2 (编排)](SPMA-design-11-phase2-strategy-orchestration.md) + [P3 voter](SPMA-design-11-phase3-multi-strategy-resolution.md)。
> **预估工时**:1 周

---

## 0. 元信息

| 字段 | 值 |
|------|---|
| 状态 | 待开始 |
| 负责人 | TBD |
| 优先级 | 🟡 P1 |
| 关联缺陷 | G7 |
| 关联文件 | `query_rewriter.py:137-209` (已有单策略) |
| 预估工时 | 1 周 |
| 相关 ADR | 借鉴 Cohere Rerank(主文件 §4.3) |

---

## 1. 现状核查

### 1.1 `_decompose_query` 单策略实现

`src/spma/agents/supervisor/query_rewriter.py:137-209`(73 行):

```python
async def _decompose_query(
    query: str, entities: dict, sources: list[str], llm,
) -> list[dict]:
    """跨源查询分解:多层级容错"""
    if not sources:
        return []
    if not llm:
        return [{"query": query, "target": source} for source in sources]
    if entities is None:
        entities = {}

    # 单一 prompt + 4 步解析兜底(JSON 解析 → 正则 → 键值对 → 原 query 广播)
    prompt = f"""将以下复杂查询分解为 {len(sources)} 个独立的子查询,..."""
    try:
        resp_obj = await llm.ainvoke(prompt)
        # 策略1: 直接 JSON 解析
        try: return json.loads(resp)
        except: pass
        # 策略2: 正则提取 JSON 数组
        # 策略3: 键值对提取
        # 策略4: 兜底——每个 source 返回原始查询
    except Exception:
        return [{"query": query, "target": source} for source in sources]
```

### 1.2 现状(实际已经很稳健)

| 已有能力 | 评估 |
|---------|------|
| 4 步解析兜底(JSON / 正则 / 键值对 / 原 query) | ✅ 良好 |
| 无 LLM 降级(返回原 query 广播) | ✅ 良好 |
| 异常隔离(try/except 整体) | ✅ 良好 |
| **但**:单策略(只能走 LLM) | 🟡 P1 待多路化 |
| **但**:无熔断(LLM 挂 = 整链挂) | 🟡 P1 待 P2 接入 |
| **但**:无 entity-guided 规则路径 | 🟡 P1 待新增 |

---

## 2. 差距分析

| 目标 | 现实 | 差距 |
|------|------|------|
| 3 路并行(template / llm / entity) | 单 LLM | **需新增 2 个策略** |
| 规则模板触发(零 LLM) | 无 | **新增 template_based** |
| 实体导向(零 LLM,按 entity 类型分发) | 无 | **新增 entity_guided** |
| 语义聚类一致性校验 | 无 | **新增 SemanticConsensusChecker** |
| 熔断保护 | 无 | **P2 已提供** |
| 失败降级 | 已有(原 query 广播) | **保留** |

**关键洞察**:P5 实际只新增 2 个零 LLM 策略 + 1 个一致性校验器,把现有 `_decompose_query` 收编为 `llm_based` 策略。

---

## 3. 详细设计

### 3.1 三个子策略

`src/spma/agents/supervisor/decomposition_strategies.py` (新建):

```python
"""查询分解的多路策略。"""
import json
import re
import logging

logger = logging.getLogger(__name__)


async def template_based(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """规则模板分解:零 LLM,识别"涉及哪些 X 和 Y"模式。

    模式 1: 显式 "和" 拆分("X 和 Y")
    模式 2: 多种 entity 类型(table + code + req)→ 自动按 source 分发
    """
    if "和" in query and ("涉及哪些" in query or "以及" in query):
        parts = query.split("和")
        if len(parts) == 2:
            return [
                {"query": query.replace("和", f",面向{source}的"), "target": source}
                for source in sources
            ]

    # 多种 entity 类型存在 → 自动分发给所有 source
    entity_types_found = sum(
        1 for k in ["table_names", "code_refs", "req_ids"]
        if entities.get(k)
    )
    if entity_types_found >= 2:
        return [{"query": query, "target": s} for s in sources]

    return None  # 规则不匹配,交给其他策略


async def entity_guided(query: str, entities: dict, sources: list[str], **_) -> list[dict] | None:
    """实体导向分解:按 entity 类型 → source 的映射,差异化生成子查询。

    主文件 §3.4 ADR:仅当各 source 的实体**真正不同**时生成 N 个子查询,否则返回 None。
    """
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

    # 全部 source 实体相同(或都为空)→ 早退,避免生成 N 个相同子查询
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


async def llm_based(query: str, entities: dict, sources: list[str], *,
                    llm=None, **_) -> list[dict] | None:
    """LLM 智能分解:复用 _decompose_query 主体逻辑 + 异常隔离。

    保留 4 步解析兜底(主文件 §3.4 防御 prompt 注入)。
    """
    if not sources:
        return None
    if not llm:
        return [{"query": query, "target": s} for s in sources]
    if entities is None:
        entities = {}

    entities_str = str({k: v for k, v in entities.items() if v})
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
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content

        # 防御:输出长度限制(防 prompt 注入撑爆)
        if len(resp) > 5000:
            logger.warning(f"llm_based: output too long ({len(resp)}), dropped")
            return None

        # 策略1: 直接 JSON 解析
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            pass

        # 策略2: 正则提取 JSON 数组
        m = re.search(r'\[.*\]', resp, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass

        # 策略3: 键值对提取
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

        # 策略4: 兜底——每个 source 返回原始查询
        return [{"query": query, "target": source} for source in sources]

    except Exception as e:
        logger.warning(f"llm_based failed: {e}")
        return [{"query": query, "target": s} for s in sources]
```

### 3.2 `SemanticConsensusChecker` 一致性校验

`src/spma/agents/supervisor/semantic_consensus.py` (新建):

```python
"""基于语义聚类的一致性校验器。"""
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

### 3.3 集成到 `_do_rewrite_pipeline`

```python
# query_rewriter.py:_do_rewrite_pipeline P5 段
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
    results = await strategy_orchestrator.execute_parallel(
        strategies, expanded, entities, sources
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
else:
    sub_queries = await _decompose_query(expanded, entities, sources, llm)

result["sub_queries"] = sub_queries
```

---

## 4. 与上游/下游 spec 的接口契约

### 4.1 新增/修改文件

| 文件 | 类型 | 改动 |
|------|------|------|
| `src/spma/agents/supervisor/decomposition_strategies.py` | **新增** | 3 个子策略 |
| `src/spma/agents/supervisor/semantic_consensus.py` | **新增** | `SemanticConsensusChecker` |
| `src/spma/agents/supervisor/query_rewriter.py` | 修改 | `_do_rewrite_pipeline` P5 段 |

### 4.2 不需要做的事

- **不**删除原 `_decompose_query`(保留供向后兼容)
- **不**实现"跨源结果融合"(由下游 Retrieval 负责)

### 4.3 配置 Key

| Key | 默认 | 说明 |
|-----|------|------|
| `QR_DECOMPOSE_LLM_MAX_OUTPUT` | 5000 | LLM 输出长度上限 |
| `QR_DECOMPOSE_SIM_THRESHOLD` | 0.6 | 共识阈值 |

---

## 5. 验收标准

| ID | 指标 | 当前 | 验收 | 测量 |
|----|------|------|------|------|
| V1 | 2 个新文件存在 | ❌ | ✅ | `ls decomposition_strategies.py semantic_consensus.py` |
| V2 | 现有 13 单测无回归 | 13/13 | 13/13 | pytest |
| V3 | 新增 ≥ 10 单测(3 策略 + 校验器) | 0 | ≥ 10 | pytest |
| V4 | 离线数据集子查询覆盖率 | (无基线) | ≥ 90% | 标 50 条多源 query |
| V5 | 单 query LLM 调用 | 1(原) | ≤ 1 | mock 计数 |

---

## 6. 风险与降级

| 风险 | 触发 | 影响 | 缓解 |
|------|------|------|------|
| **R1**:3 策略全 None | 输入极简 | 走原 query 广播 | `valid` 为空时降级 |
| **R2**:LLM 输出爆炸 | prompt 注入 | 资源耗尽 | 5000 字符限制 |
| **R3**:embedder 挂 | 上游故障 | 校验器退化为"取第一个" | 已有 try/except |
| **R4**:JSON 解析失败 | LLM 输出格式异常 | 4 步兜底 | 复用现有 4 步逻辑 |

---

## 7. 实施步骤

### 7.1 PR 切分(3 个 PR)

**PR #1**:3 个子策略 + 单测
**PR #2**:`SemanticConsensusChecker` + 单测
**PR #3**:集成到 `_do_rewrite_pipeline`

### 7.2 时间表

| 工作日 | 任务 | 产出 |
|--------|------|------|
| D1-D2 | 3 个子策略 + 单测 | PR #1 ready |
| D3 | Review PR #1 + 合并 | - |
| D3 | `SemanticConsensusChecker` + 单测 | PR #2 ready |
| D4 | Review PR #2 + 合并 | - |
| D4-D5 | 集成 | PR #3 ready |
| D6 | Review PR #3 + 合并 | - |

### 7.3 上线 checklist

- [ ] PR #1-3 合并
- [ ] 13 原单测无回归
- [ ] 10+ 新单测全过
- [ ] 子查询覆盖率 ≥ 90%

---

## 8. 变更日志

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-29 | 1.0 | **gap-driven 重写**:基于已有 `_decompose_query` 收编为 `llm_based` 策略,新增 2 个零 LLM 策略 + 校验器 |
| 2026-06-29 | 0.9 | (回退)初次拆分(已回退) |

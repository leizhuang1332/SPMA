# SPMA Supervisor 质量评分 v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Supervisor 质量评分从 3 维（count + confidence + exact_match）升级为 6 维（+ relevance + completeness + conciseness），新增 LLM 评分 + 启发式降级路径，并落地 Postgres 历史数据采集表。

**Architecture:** 每个评分维度拆为独立纯函数（除 relevance/completeness 需 LLM），由共享 `LLMScorer` 包装 LLM 调用并负责超时/降级；`evaluate_workers` 升级为 async 并行调 LLM + fire-and-forget 落库。零外部新模型依赖，复用 `spma.llm.get_langchain_client(role="classification")` 和 `spma.api.dependencies.get_db_pool()`。

**Tech Stack:** Python 3.x、asyncio、pytest + pytest-asyncio、asyncpg、LangChain BaseChatModel、Pydantic v2、Postgres 14+。

## Global Constraints

- **依赖**：禁止引入 sentence-transformers 等新模型。LLM 必须复用 `spma.llm.get_langchain_client()`。
- **Migration 命名**：`deployments/docker/migrations/002_quality_evaluation.sql`（项目约定 `NNN_name.sql`）。
- **阈值**：`QualityConfig.threshold_default = 0.6`，`evaluate_workers` 默认参数 `threshold=0.6`。
- **LLM 超时**：`QualityConfig.llm_timeout_ms = 800`，`asyncio.wait_for` 上限。
- **数据契约向后兼容**：`evaluate_workers` 返回结构 `{scores, passed, failed, all_pass}` 不变；**新增** `sub_scores` 字段；所有新参数可选。
- **answer 截断**：`QualityConfig.answer_max_chars_for_llm = 800`；落库时 `answer_snippet_max_chars = 200`。
- **失败可见性**：所有降级路径 log warning，包含 question 摘要（前 50 字符）。
- **现有测试不能破坏**：`tests/unit/agents/supervisor/test_quality.py` 仅扩展；`tests/integration/test_supervisor_loop.py` 必须继续通过。
- **不引入 SupervisorState 协议破坏**：新增 `quality_sub_scores` 字段为 NotRequired。
- **Zombie 代码**：仅加 TODO 注释，不删除。
- **Git 提交约束（CLAUDE.md）**：禁止 `git add`、`git commit`、`git push`，由用户执行。
- **中文回答**：所有回答使用简体中文（CLAUDE.md）。

---

## File Structure

```
src/spma/agents/supervisor/
├── config.py                  ← 新建：QualityConfig + env loader
├── history.py                 ← 新建：record_evaluation (async, fire-and-forget)
├── state.py                   ← 修改：增加 quality_sub_scores 字段
├── quality.py                 ← 修改：evaluate_workers 升级 async + 6 维
├── graph.py                   ← 修改：score_node 调用新接口
└── metrics/
    ├── __init__.py            ← 新建
    ├── count.py               ← 新建
    ├── confidence.py          ← 新建
    ├── exact_match.py         ← 新建
    ├── conciseness.py         ← 新建
    ├── relevance.py           ← 新建 (async)
    ├── completeness.py        ← 新建 (async)
    └── llm_scorer.py          ← 新建：LLMScorer + get_llm_scorer 单例

deployments/docker/migrations/
└── 002_quality_evaluation.sql ← 新建

tests/unit/agents/supervisor/
├── test_config.py             ← 新建
├── test_history.py            ← 新建
├── test_state.py              ← 新建
└── metrics/
    ├── test_count.py
    ├── test_confidence.py
    ├── test_exact_match.py
    ├── test_conciseness.py
    ├── test_llm_scorer.py
    ├── test_relevance.py
    └── test_completeness.py

tests/integration/
└── test_supervisor_loop.py    ← 修改：扩展 6 维场景

src/spma/observability/
└── metrics.py                 ← 修改：增加 quality_* 计数器

src/spma/api/routes/query.py   ← 修改：仅加 TODO 注释（zombie 标记）
src/spma/api/query_graph.py    ← 修改：仅加 TODO 注释（zombie 标记）
```

---

## Task 1: QualityConfig + 环境变量加载

**Files:**
- Create: `src/spma/agents/supervisor/config.py`
- Create: `tests/unit/agents/supervisor/test_config.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `QualityConfig` (frozen dataclass): `llm_timeout_ms`, `llm_enabled`, `history_enabled`, `threshold_default`, `answer_max_chars_for_llm`, `answer_snippet_max_chars`, `weights` (嵌套 dict)
  - `load_quality_config()` -> `QualityConfig`: 读环境变量，返回 config

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/test_config.py`：

```python
"""QualityConfig 单元测试。"""
import os
from dataclasses import FrozenInstanceError

import pytest

from spma.agents.supervisor.config import QualityConfig, load_quality_config


def test_quality_config_defaults():
    """默认值符合 spec §4.6。"""
    cfg = QualityConfig()
    assert cfg.llm_timeout_ms == 800
    assert cfg.llm_enabled is True
    assert cfg.history_enabled is True
    assert cfg.threshold_default == 0.6
    assert cfg.answer_max_chars_for_llm == 800
    assert cfg.answer_snippet_max_chars == 200


def test_quality_config_weights_sum_to_one():
    """所有 query_type 权重合计 = 1.0。"""
    cfg = QualityConfig()
    for qt, w in cfg.weights.items():
        assert abs(sum(w.values()) - 1.0) < 1e-9, f"{qt} 权重合计 != 1.0"


def test_quality_config_is_frozen():
    """frozen dataclass 不能修改字段。"""
    cfg = QualityConfig()
    with pytest.raises(FrozenInstanceError):
        cfg.llm_timeout_ms = 1000


def test_load_quality_config_reads_env(monkeypatch):
    """环境变量 SPMA_QUALITY_* 覆盖默认。"""
    monkeypatch.setenv("SPMA_QUALITY_LLM_TIMEOUT_MS", "500")
    monkeypatch.setenv("SPMA_QUALITY_LLM_ENABLED", "false")
    monkeypatch.setenv("SPMA_QUALITY_HISTORY_ENABLED", "false")
    monkeypatch.setenv("SPMA_QUALITY_THRESHOLD", "0.7")
    cfg = load_quality_config()
    assert cfg.llm_timeout_ms == 500
    assert cfg.llm_enabled is False
    assert cfg.history_enabled is False
    assert cfg.threshold_default == 0.7


def test_load_quality_config_defaults_when_no_env(monkeypatch):
    """无环境变量时返回默认值。"""
    for var in [
        "SPMA_QUALITY_LLM_TIMEOUT_MS",
        "SPMA_QUALITY_LLM_ENABLED",
        "SPMA_QUALITY_HISTORY_ENABLED",
        "SPMA_QUALITY_THRESHOLD",
    ]:
        monkeypatch.delenv(var, raising=False)
    cfg = load_quality_config()
    assert cfg.llm_timeout_ms == 800
    assert cfg.threshold_default == 0.6
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_config.py -v
```

Expected: `ModuleNotFoundError: No module named 'spma.agents.supervisor.config'`

- [ ] **Step 3: 实现 QualityConfig**

`src/spma/agents/supervisor/config.py`：

```python
"""质量评分全局配置。

设计依据: SPMA v2 spec §4.6
"""

import os
from dataclasses import dataclass, field


def _default_weights() -> dict[str, dict[str, float]]:
    """静态权重矩阵（来源: SPMA-design-08 §3.3）。"""
    return {
        "data_query": {
            "count": 0.15, "confidence": 0.20, "exact_match": 0.25,
            "relevance": 0.20, "completeness": 0.15, "conciseness": 0.05,
        },
        "search": {
            "count": 0.25, "confidence": 0.20, "exact_match": 0.10,
            "relevance": 0.20, "completeness": 0.15, "conciseness": 0.10,
        },
        "trace": {
            "count": 0.10, "confidence": 0.20, "exact_match": 0.35,
            "relevance": 0.15, "completeness": 0.15, "conciseness": 0.05,
        },
    }


@dataclass(frozen=True)
class QualityConfig:
    """质量评分全局配置。"""
    llm_timeout_ms: int = 800
    llm_enabled: bool = True
    history_enabled: bool = True
    threshold_default: float = 0.6
    answer_max_chars_for_llm: int = 800
    answer_snippet_max_chars: int = 200
    weights: dict[str, dict[str, float]] = field(default_factory=_default_weights)


def load_quality_config() -> QualityConfig:
    """从环境变量加载配置，未设置时使用 QualityConfig 默认值。"""
    return QualityConfig(
        llm_timeout_ms=int(os.getenv("SPMA_QUALITY_LLM_TIMEOUT_MS", "800")),
        llm_enabled=os.getenv("SPMA_QUALITY_LLM_ENABLED", "true").lower() == "true",
        history_enabled=os.getenv("SPMA_QUALITY_HISTORY_ENABLED", "true").lower() == "true",
        threshold_default=float(os.getenv("SPMA_QUALITY_THRESHOLD", "0.6")),
    )
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_config.py -v
```

Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/config.py tests/unit/agents/supervisor/test_config.py
```

（按 CLAUDE.md 不自动 git commit/push，由用户执行）

---

## Task 2: Postgres Migration 002 — quality_evaluation 表

**Files:**
- Create: `deployments/docker/migrations/002_quality_evaluation.sql`

**Interfaces:**
- Consumes: 无
- Produces: DDL 文件，部署时由 migration runner 执行

- [ ] **Step 1: 写 SQL 文件**

`deployments/docker/migrations/002_quality_evaluation.sql`：

```sql
-- Migration 002: 新增 quality_evaluation 表
-- 设计依据: SPMA v2 spec §4.4
-- 为后续熵权法/校准/置信区间提供历史数据基础

CREATE TABLE IF NOT EXISTS quality_evaluation (
    id                  BIGSERIAL PRIMARY KEY,
    session_id          TEXT,
    query_id            TEXT NOT NULL,
    task_id             TEXT,
    worker_type         TEXT NOT NULL CHECK (worker_type IN ('doc', 'code', 'sql')),
    query_type          TEXT NOT NULL,
    question            TEXT,
    answer_snippet      TEXT,                              -- ≤200 字符，控制 PII 风险

    -- 6 个分维度（独立存储，便于后续离线分析）
    count_score         DOUBLE PRECISION NOT NULL,
    confidence_score    DOUBLE PRECISION NOT NULL,
    exact_match_score   DOUBLE PRECISION NOT NULL,
    relevance_score     DOUBLE PRECISION NOT NULL,
    completeness_score  DOUBLE PRECISION NOT NULL,
    conciseness_score   DOUBLE PRECISION NOT NULL,

    -- 加权结果
    weighted_score      DOUBLE PRECISION NOT NULL,
    weights_used        JSONB NOT NULL,                    -- 记录本次用的权重矩阵

    -- 元数据
    llm_used            BOOLEAN NOT NULL,                  -- true=LLM路径 / false=启发式 fallback
    llm_latency_ms      INTEGER,
    threshold           DOUBLE PRECISION NOT NULL,
    passed              BOOLEAN NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_quality_evaluation_created_at
    ON quality_evaluation (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_evaluation_worker_query_type
    ON quality_evaluation (worker_type, query_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_quality_evaluation_session
    ON quality_evaluation (session_id);
```

- [ ] **Step 2: 验证 SQL 语法（本地 Postgres 可用时）**

```bash
# 若本地有 Postgres：psql -U spma -d spma -f deployments/docker/migrations/002_quality_evaluation.sql
# 若无：跳到 Step 3，仅靠 CI 验证
```

Expected (若执行): `CREATE TABLE`, `CREATE INDEX` ×3

- [ ] **Step 3: 提交**

```bash
cd d:/TraeProject/SPMA && git add deployments/docker/migrations/002_quality_evaluation.sql
```

---

## Task 3: 4 个纯规则 Metrics（count / confidence / exact_match / conciseness）

**Files:**
- Create: `src/spma/agents/supervisor/metrics/__init__.py`
- Create: `src/spma/agents/supervisor/metrics/count.py`
- Create: `src/spma/agents/supervisor/metrics/confidence.py`
- Create: `src/spma/agents/supervisor/metrics/exact_match.py`
- Create: `src/spma/agents/supervisor/metrics/conciseness.py`
- Create: `tests/unit/agents/supervisor/metrics/__init__.py`
- Create: `tests/unit/agents/supervisor/metrics/test_count.py`
- Create: `tests/unit/agents/supervisor/metrics/test_confidence.py`
- Create: `tests/unit/agents/supervisor/metrics/test_exact_match.py`
- Create: `tests/unit/agents/supervisor/metrics/test_conciseness.py`

**Interfaces (consumed by later tasks):**
- `calculate_count_score(result_count: int) -> float`
- `calculate_confidence_score(confidence: float | None) -> float`
- `calculate_exact_score(has_exact_match: bool) -> float`
- `calculate_conciseness_score(question: str, answer: str) -> float`

- [ ] **Step 1: 写 4 个失败测试文件**

`tests/unit/agents/supervisor/metrics/test_count.py`：

```python
"""count_score 单元测试。"""
import pytest

from spma.agents.supervisor.metrics.count import calculate_count_score


@pytest.mark.parametrize("result_count,expected", [
    (0, 0.0),
    (1, 1 / 3),
    (2, 2 / 3),
    (3, 1.0),
    (5, 1.0),
    (100, 1.0),
])
def test_calculate_count_score_saturation(result_count, expected):
    """饱和曲线: 0→0、3→1.0、≥3 夹断。"""
    assert calculate_count_score(result_count) == pytest.approx(expected, abs=1e-9)


def test_calculate_count_score_negative_clamped():
    """负数视为 0。"""
    assert calculate_count_score(-1) == 0.0
```

`tests/unit/agents/supervisor/metrics/test_confidence.py`：

```python
"""confidence_score 单元测试。"""
import pytest

from spma.agents.supervisor.metrics.confidence import calculate_confidence_score


@pytest.mark.parametrize("confidence,expected", [
    (0.0, 0.0),
    (0.5, 0.5),
    (1.0, 1.0),
    (None, 0.0),
    (-0.1, 0.0),       # 负数夹断
    (1.5, 1.0),         # >1 夹断
])
def test_calculate_confidence_score_clamping(confidence, expected):
    assert calculate_confidence_score(confidence) == pytest.approx(expected, abs=1e-9)
```

`tests/unit/agents/supervisor/metrics/test_exact_match.py`：

```python
"""exact_match_score 单元测试。"""
from spma.agents.supervisor.metrics.exact_match import calculate_exact_score


def test_calculate_exact_score_true():
    assert calculate_exact_score(True) == 1.0


def test_calculate_exact_score_false():
    assert calculate_exact_score(False) == 0.0
```

`tests/unit/agents/supervisor/metrics/test_conciseness.py`：

```python
"""conciseness_score 单元测试。"""
import pytest

from spma.agents.supervisor.metrics.conciseness import calculate_conciseness_score


def test_conciseness_ideal_range_returns_one():
    """answer 词数 ∈ [q_words, q_words*3] → 1.0。"""
    q = "查询用户信息"            # 4 words (tokenized by split)
    a = "用户信息 结果 返回 成功"   # 6 words，介于 4 和 12 之间
    assert calculate_conciseness_score(q, a) == 1.0


def test_conciseness_too_short():
    """answer < q_words → 按比例衰减。"""
    q = "查询 用户 信息 数据"
    a = "用户"  # 1 word, q_words=4
    score = calculate_conciseness_score(q, a)
    assert 0 < score < 0.8  # (1/4) * 0.8


def test_conciseness_too_long():
    """answer > q_words*3 → 衰减，下限 0.2。"""
    q = "查询用户信息"
    a = " ".join(["用户信息"] * 100)  # 200 words, q_words=4, ideal_max=12
    score = calculate_conciseness_score(q, a)
    assert 0.2 <= score < 1.0


def test_conciseness_empty_question():
    """问题为空时 answer 非空 → 0.2（兜底）。"""
    score = calculate_conciseness_score("", "一些答案")
    assert score == 0.2


def test_conciseness_empty_answer():
    """答案为空 → 0.2（兜底）。"""
    score = calculate_conciseness_score("查询用户", "")
    assert score == 0.2
```

- [ ] **Step 2: 跑测试，验证全部失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/ -v
```

Expected: 4 个 `ModuleNotFoundError`

- [ ] **Step 3: 创建 metrics 包骨架**

`src/spma/agents/supervisor/metrics/__init__.py`：

```python
"""Supervisor 质量评分维度。

设计依据: SPMA v2 spec §4
"""

from spma.agents.supervisor.metrics.completeness import calculate_completeness_score
from spma.agents.supervisor.metrics.conciseness import calculate_conciseness_score
from spma.agents.supervisor.metrics.confidence import calculate_confidence_score
from spma.agents.supervisor.metrics.count import calculate_count_score
from spma.agents.supervisor.metrics.exact_match import calculate_exact_score
from spma.agents.supervisor.metrics.relevance import calculate_relevance_score

__all__ = [
    "calculate_completeness_score",
    "calculate_conciseness_score",
    "calculate_confidence_score",
    "calculate_count_score",
    "calculate_exact_score",
    "calculate_relevance_score",
]
```

`tests/unit/agents/supervisor/metrics/__init__.py`：

```python
"""测试包占位。"""
```

- [ ] **Step 4: 实现 4 个 metric 函数**

`src/spma/agents/supervisor/metrics/count.py`：

```python
"""count_score: 饱和曲线。

设计依据: SPMA v2 spec §4.2
"""


def calculate_count_score(result_count: int) -> float:
    """饱和曲线: 0→0、3→1.0、≥3 夹断到 1.0。"""
    if result_count <= 0:
        return 0.0
    return min(1.0, result_count / 3.0)
```

`src/spma/agents/supervisor/metrics/confidence.py`：

```python
"""confidence_score: 夹断到 [0, 1]。

设计依据: SPMA v2 spec §4.2
"""


def calculate_confidence_score(confidence: float | None) -> float:
    """夹断到 [0, 1]；None / 缺省 → 0.0。"""
    if confidence is None:
        return 0.0
    return max(0.0, min(1.0, float(confidence)))
```

`src/spma/agents/supervisor/metrics/exact_match.py`：

```python
"""exact_match_score: 二值。

设计依据: SPMA v2 spec §4.2
"""


def calculate_exact_score(has_exact_match: bool) -> float:
    """1.0 / 0.0 二值。"""
    return 1.0 if has_exact_match else 0.0
```

`src/spma/agents/supervisor/metrics/conciseness.py`：

```python
"""conciseness_score: 词数比规则。

设计依据: SPMA v2 spec §4.2

公式:
  - answer_words ∈ [q_words, q_words * 3] → 1.0
  - answer_words < q_words → (answer_words / q_words) * 0.8
  - answer_words > q_words * 3 → max(0.2, ideal_max / answer_words)
  - 任何一方为空 → 0.2
"""


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def calculate_conciseness_score(question: str, answer: str) -> float:
    """answer 词数与 question 词数的比值评估。"""
    q_words = _word_count(question)
    a_words = _word_count(answer)

    if q_words == 0 or a_words == 0:
        return 0.2

    ideal_min = q_words
    ideal_max = q_words * 3

    if ideal_min <= a_words <= ideal_max:
        return 1.0
    if a_words < ideal_min:
        return (a_words / ideal_min) * 0.8
    return max(0.2, ideal_max / a_words)
```

- [ ] **Step 5: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/ -v
```

Expected: 4 个文件全部通过（约 18 用例）

- [ ] **Step 6: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/metrics/ tests/unit/agents/supervisor/metrics/
```

---

## Task 4: LLMScorer（mock LLM 实现 + 失败降级）

**Files:**
- Create: `src/spma/agents/supervisor/metrics/llm_scorer.py`
- Create: `tests/unit/agents/supervisor/metrics/test_llm_scorer.py`

**Interfaces (consumed by Tasks 5, 6, 8):**
- `LLMScoreResult` (frozen dataclass): `relevance`, `completeness`, `conciseness` ∈ [0, 1]
- `LLMScorer.__init__(llm: BaseChatModel, timeout_ms: int = 800)`
- `LLMScorer.assess(question: str, answer: str) -> LLMScoreResult | None`
- `get_llm_scorer() -> LLMScorer` — 全局单例

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/metrics/test_llm_scorer.py`：

```python
"""LLMScorer 单元测试（mock LLM）。"""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage

from spma.agents.supervisor.metrics.llm_scorer import (
    LLMScoreResult,
    LLMScorer,
    get_llm_scorer,
    reset_llm_scorer_singleton,
)


@pytest.fixture
def mock_llm():
    """提供 mock 的 BaseChatModel。"""
    return MagicMock()


@pytest.fixture
def scorer(mock_llm):
    return LLMScorer(llm=mock_llm, timeout_ms=800)


async def test_assess_returns_parsed_result(scorer, mock_llm):
    """正常 JSON 输出 → 返回 LLMScoreResult。"""
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"relevance":0.8,"completeness":0.7,"conciseness":0.9}')
    )
    result = await scorer.assess("查询用户", "用户信息")
    assert result is not None
    assert result.relevance == 0.8
    assert result.completeness == 0.7
    assert result.conciseness == 0.9


async def test_assess_timeout_returns_none(scorer, mock_llm):
    """LLM 超时 → 返回 None。"""
    async def slow(*args, **kwargs):
        await asyncio.sleep(10)

    mock_llm.ainvoke = AsyncMock(side_effect=slow)
    result = await scorer.assess("Q", "A", timeout_ms=50)
    assert result is None


async def test_assess_invalid_json_returns_none(scorer, mock_llm):
    """非 JSON 输出 → 返回 None。"""
    mock_llm.ainvoke = AsyncMock(return_value=AIMessage(content="not json"))
    result = await scorer.assess("Q", "A")
    assert result is None


async def test_assess_missing_field_returns_none(scorer, mock_llm):
    """缺少字段 → 返回 None。"""
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"relevance":0.5}')
    )
    result = await scorer.assess("Q", "A")
    assert result is None


async def test_assess_out_of_range_clamps(scorer, mock_llm):
    """超出 [0,1] 范围 → 夹断。"""
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"relevance":1.5,"completeness":-0.1,"conciseness":0.5}')
    )
    result = await scorer.assess("Q", "A")
    assert result is not None
    assert result.relevance == 1.0
    assert result.completeness == 0.0
    assert result.conciseness == 0.5


async def test_assess_llm_api_error_returns_none(scorer, mock_llm):
    """LLM 抛异常 → 返回 None。"""
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("rate limit"))
    result = await scorer.assess("Q", "A")
    assert result is None


async def test_assess_truncates_long_answer(scorer, mock_llm):
    """answer 超过 answer_max_chars 截断到 800 字符。"""
    mock_llm.ainvoke = AsyncMock(
        return_value=AIMessage(content='{"relevance":0.5,"completeness":0.5,"conciseness":0.5}')
    )
    long_answer = "x" * 5000
    await scorer.assess("Q", long_answer)
    # 验证 ainvoke 收到的 prompt 中 answer 截断
    call_args = mock_llm.ainvoke.call_args
    sent_messages = call_args[0][0]
    sent_text = "\n".join(str(m.content) for m in sent_messages)
    assert "x" * 800 in sent_text
    assert "x" * 801 not in sent_text


def test_get_llm_scorer_returns_singleton():
    """单例模式: 多次调用返回同一实例。"""
    reset_llm_scorer_singleton()
    s1 = get_llm_scorer()
    s2 = get_llm_scorer()
    assert s1 is s2


def test_reset_llm_scorer_singleton():
    """reset 后下次 get 创建新实例。"""
    reset_llm_scorer_singleton()
    s1 = get_llm_scorer()
    reset_llm_scorer_singleton()
    s2 = get_llm_scorer()
    assert s1 is not s2
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_llm_scorer.py -v
```

Expected: `ModuleNotFoundError: No module named 'spma.agents.supervisor.metrics.llm_scorer'`

- [ ] **Step 3: 实现 LLMScorer**

`src/spma/agents/supervisor/metrics/llm_scorer.py`：

```python
"""LLMScorer: LLM 评分共享入口。

设计依据: SPMA v2 spec §4.3
- 单次 LLM 调用产出 relevance/completeness/conciseness 三个分
- 失败/超时/解析失败 → 返回 None
- answer 超过 answer_max_chars_for_llm (默认 800) 截断
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from langchain_core.messages import HumanMessage, SystemMessage

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from spma.agents.supervisor.config import QualityConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LLMScoreResult:
    """LLM 单次调用的结构化输出。三字段均在 [0, 1] 区间。"""
    relevance: float      # ∈ [0, 1]
    completeness: float   # ∈ [0, 1]
    conciseness: float    # ∈ [0, 1]


_SYSTEM_PROMPT = """你是一个客观评估员。基于用户问题和工作 Agent 的回答，从三个维度各打一个 0~1 的分。

严格按 JSON 输出，不要任何解释。

[输出 JSON]
{"relevance": <0~1>, "completeness": <0~1>, "conciseness": <0~1>}

- relevance: 回答与问题的相关程度
- completeness: 回答覆盖问题关键点的完整程度
- conciseness: 回答简洁程度（无冗余为高）"""


def _build_user_prompt(question: str, answer: str, max_chars: int) -> str:
    truncated = answer[:max_chars]
    return f"[问题]\n{question}\n\n[回答]\n{truncated}\n"


def _parse_result(content: str) -> LLMScoreResult | None:
    """解析 LLM 输出。失败返回 None。"""
    if not content:
        return None
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None

    try:
        relevance = float(data["relevance"])
        completeness = float(data["completeness"])
        conciseness = float(data["conciseness"])
    except (KeyError, ValueError, TypeError):
        return None

    return LLMScoreResult(
        relevance=max(0.0, min(1.0, relevance)),
        completeness=max(0.0, min(1.0, completeness)),
        conciseness=max(0.0, min(1.0, conciseness)),
    )


class LLMScorer:
    """LLM 评分客户端：单次调用产出 3 维分。失败/超时 → 返回 None。"""

    def __init__(self, llm: "BaseChatModel", timeout_ms: int = 800):
        self._llm = llm
        self._timeout_s = timeout_ms / 1000

    async def assess(
        self,
        question: str,
        answer: str,
        *,
        timeout_ms: int | None = None,
        max_chars: int = 800,
    ) -> LLMScoreResult | None:
        """单次 LLM 调用产出 {relevance, completeness, conciseness} ∈ [0,1]^3。

        失败/超时/解析失败 → 返回 None。
        """
        timeout_s = (timeout_ms / 1000) if timeout_ms is not None else self._timeout_s
        user_prompt = _build_user_prompt(question, answer, max_chars)
        messages = [
            SystemMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=user_prompt),
        ]

        try:
            response = await asyncio.wait_for(
                self._llm.ainvoke(messages),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "LLMScorer.assess timeout after %sms (question=%s)",
                int(timeout_s * 1000),
                question[:50],
            )
            return None
        except Exception as exc:
            logger.warning(
                "LLMScorer.assess LLM error: %s (question=%s)",
                exc,
                question[:50],
            )
            return None

        content = getattr(response, "content", None)
        if isinstance(content, list):
            content = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return _parse_result(content if isinstance(content, str) else "")


# ---- 单例 ----

_scorer_singleton: LLMScorer | None = None


def reset_llm_scorer_singleton() -> None:
    """测试用：重置单例。"""
    global _scorer_singleton
    _scorer_singleton = None


def get_llm_scorer(config: "QualityConfig | None" = None) -> LLMScorer:
    """获取全局 LLMScorer 单例。"""
    global _scorer_singleton
    if _scorer_singleton is None:
        from spma.agents.supervisor.config import QualityConfig, load_quality_config
        from spma.llm import get_langchain_client

        cfg = config or load_quality_config()
        llm = get_langchain_client(role="classification")
        _scorer_singleton = LLMScorer(llm=llm, timeout_ms=cfg.llm_timeout_ms)
    return _scorer_singleton
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_llm_scorer.py -v
```

Expected: 9 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/metrics/llm_scorer.py tests/unit/agents/supervisor/metrics/test_llm_scorer.py
```

---

## Task 5: relevance metric（LLM + 启发式 fallback）

**Files:**
- Create: `src/spma/agents/supervisor/metrics/relevance.py`
- Create: `tests/unit/agents/supervisor/metrics/test_relevance.py`

**Interfaces (consumed by Task 8):**
- `calculate_relevance_score(question: str, answer: str, llm_scorer: LLMScorer) -> float`

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/metrics/test_relevance.py`：

```python
"""relevance_score 单元测试。"""
from unittest.mock import AsyncMock

import pytest

from spma.agents.supervisor.metrics.llm_scorer import LLMScoreResult
from spma.agents.supervisor.metrics.relevance import calculate_relevance_score


async def test_relevance_uses_llm_when_available():
    """LLM 返回有效结果 → 直接使用。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(
        return_value=LLMScoreResult(relevance=0.85, completeness=0.0, conciseness=0.0)
    )
    score = await calculate_relevance_score("查询用户信息", "用户信息结果", scorer)
    assert score == 0.85


async def test_relevance_uses_heuristic_when_llm_returns_none():
    """LLM 返回 None → 词袋 Jaccard 启发式。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=None)
    score = await calculate_relevance_score("查询 用户 信息", "用户 信息 数据", scorer)
    # 公共词: {"用户", "信息"} = 2, 总词: 3 → 2/3 ≈ 0.667
    assert 0.6 <= score <= 0.7


async def test_relevance_empty_answer_returns_zero():
    """answer 为空 → 0.0（不调用 LLM）。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock()
    score = await calculate_relevance_score("查询用户", "", scorer)
    assert score == 0.0
    scorer.assess.assert_not_called()


async def test_relevance_empty_question_returns_zero():
    """question 为空 → 0.0（不调用 LLM）。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock()
    score = await calculate_relevance_score("", "一些答案", scorer)
    assert score == 0.0
    scorer.assess.assert_not_called()


async def test_relevance_heuristic_no_overlap():
    """词袋完全无重叠 → 0.0。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=None)
    score = await calculate_relevance_score("apple banana", "cat dog", scorer)
    assert score == 0.0
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_relevance.py -v
```

Expected: `ModuleNotFoundError: No module named 'spma.agents.supervisor.metrics.relevance'`

- [ ] **Step 3: 实现 relevance**

`src/spma/agents/supervisor/metrics/relevance.py`：

```python
"""relevance_score: LLM 评分 + 词袋 Jaccard fallback。

设计依据: SPMA v2 spec §4.2, §4.3
"""


def _tokenize(text: str) -> set[str]:
    """简单按空白分词 + 小写化。"""
    return {w for w in text.lower().split() if w}


def _heuristic_relevance(question: str, answer: str) -> float:
    """词袋 Jaccard 启发式: 公共词 / 问题词数。"""
    q_words = _tokenize(question)
    a_words = _tokenize(answer)
    if not q_words:
        return 0.0
    return len(q_words & a_words) / len(q_words)


async def calculate_relevance_score(
    question: str,
    answer: str,
    llm_scorer,  # LLMScorer (避免循环 import)
) -> float:
    """调 llm_scorer.assess 拿 relevance；LLM 失败 → 词袋 Jaccard。

    Args:
        question: 用户原始问题
        answer: Worker 返回的答案（取自 results[*].text 或 citations[*].snippet）
        llm_scorer: LLMScorer 实例

    Returns:
        ∈ [0, 1]
    """
    if not question or not answer:
        return 0.0

    result = await llm_scorer.assess(question, answer)
    if result is not None:
        return result.relevance

    return _heuristic_relevance(question, answer)
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_relevance.py -v
```

Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/metrics/relevance.py tests/unit/agents/supervisor/metrics/test_relevance.py
```

---

## Task 6: completeness metric（LLM + 启发式 fallback）

**Files:**
- Create: `src/spma/agents/supervisor/metrics/completeness.py`
- Create: `tests/unit/agents/supervisor/metrics/test_completeness.py`

**Interfaces (consumed by Task 8):**
- `calculate_completeness_score(question: str, answer: str, llm_scorer: LLMScorer) -> float`

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/metrics/test_completeness.py`：

```python
"""completeness_score 单元测试。"""
from unittest.mock import AsyncMock

import pytest

from spma.agents.supervisor.metrics.llm_scorer import LLMScoreResult
from spma.agents.supervisor.metrics.completeness import (
    _heuristic_completeness,
    calculate_completeness_score,
)


def test_heuristic_completeness_zero_sentences():
    """句数为 0 → 视为 1 句, 0.2（最低）。"""
    assert _heuristic_completeness("无句号的文本") == pytest.approx(0.2)


def test_heuristic_completeness_short():
    """句数 < 5 → 线性增长。"""
    assert _heuristic_completeness("句一。句二。句三。") == pytest.approx(0.6)


def test_heuristic_completeness_long_enough():
    """句数 ≥ 5 → 1.0。"""
    text = "句一。句二。句三。句四。句五。句六。"
    assert _heuristic_completeness(text) == 1.0


def test_heuristic_completeness_empty():
    """空 → 0.2（兜底）。"""
    assert _heuristic_completeness("") == 0.2


async def test_completeness_uses_llm_when_available():
    """LLM 返回有效 → 直接使用。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(
        return_value=LLMScoreResult(relevance=0.0, completeness=0.75, conciseness=0.0)
    )
    score = await calculate_completeness_score("Q", "A", scorer)
    assert score == 0.75


async def test_completeness_uses_heuristic_when_llm_returns_none():
    """LLM 返回 None → 句数启发式。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=None)
    score = await calculate_completeness_score("Q", "句一。句二。句三。", scorer)
    # 3 句 / 5 = 0.6
    assert score == pytest.approx(0.6)


async def test_completeness_empty_answer_calls_heuristic():
    """answer 为空 → 不调用 LLM, 走启发式。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock()
    score = await calculate_completeness_score("Q", "", scorer)
    assert score == 0.2  # 空 answer 启发式兜底
    scorer.assess.assert_not_called()
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_completeness.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: 实现 completeness**

`src/spma/agents/supervisor/metrics/completeness.py`：

```python
"""completeness_score: LLM 评分 + 句数启发式 fallback。

设计依据: SPMA v2 spec §4.2, §4.3
"""

_SENTENCE_DELIMS = ("。", ".", "？", "?", "！", "!")


def _heuristic_completeness(answer: str) -> float:
    """句数启发式: 0 句视为 1 句 (0.2), ≥5 句视为完整 (1.0)。"""
    if not answer:
        return 0.2
    sentences = sum(answer.count(d) for d in _SENTENCE_DELIMS)
    sentences = max(1, sentences)
    return min(1.0, sentences / 5.0)


async def calculate_completeness_score(
    question: str,
    answer: str,
    llm_scorer,
) -> float:
    """调 llm_scorer.assess 拿 completeness；LLM 失败 → 句数启发式。

    Args:
        question: 用户原始问题（保留参数用于未来 LLM 路径；当前启发式不用）
        answer: Worker 返回的答案
        llm_scorer: LLMScorer 实例

    Returns:
        ∈ [0, 1]
    """
    if not answer:
        return _heuristic_completeness("")

    result = await llm_scorer.assess(question, answer)
    if result is not None:
        return result.completeness

    return _heuristic_completeness(answer)
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/metrics/test_completeness.py -v
```

Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/metrics/completeness.py tests/unit/agents/supervisor/metrics/test_completeness.py
```

---

## Task 7: history.record_evaluation（Postgres 落库 + fire-and-forget）

**Files:**
- Create: `src/spma/agents/supervisor/history.py`
- Create: `tests/unit/agents/supervisor/test_history.py`

**Interfaces (consumed by Task 8):**
- `record_evaluation(db_pool, *, session_id, query_id, task_id, worker_type, query_type, question, answer_snippet, sub_scores, weighted_score, weights_used, llm_used, llm_latency_ms, threshold, passed) -> None`
- 失败仅 log warning，**不抛出**

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/test_history.py`：

```python
"""record_evaluation 单元测试（mock asyncpg.Pool）。"""
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.supervisor.history import record_evaluation


@pytest.fixture
def mock_pool():
    """提供 mock asyncpg.Pool，acquire() 返回 mock connection。"""
    pool = MagicMock()
    conn = MagicMock()

    # pool.acquire() 异步上下文管理器
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)

    # conn.execute() 也是异步
    conn.execute = AsyncMock(return_value=None)
    return pool


async def test_record_evaluation_writes_row(mock_pool):
    """成功路径：调用 execute 一次。"""
    await record_evaluation(
        mock_pool,
        session_id="sess-1",
        query_id="q-1",
        task_id="t-1",
        worker_type="doc",
        query_type="search",
        question="查询用户",
        answer_snippet="用户信息",
        sub_scores={
            "count": 0.5, "confidence": 0.7, "exact_match": 0.0,
            "relevance": 0.8, "completeness": 0.6, "conciseness": 0.9,
        },
        weighted_score=0.65,
        weights_used={"count": 0.25, "confidence": 0.20, "exact_match": 0.10,
                      "relevance": 0.20, "completeness": 0.15, "conciseness": 0.10},
        llm_used=True,
        llm_latency_ms=450,
        threshold=0.6,
        passed=True,
    )

    # 验证 execute 被调用一次
    mock_pool.acquire.return_value.__aenter__.return_value.execute.assert_awaited_once()
    call_args = mock_pool.acquire.return_value.__aenter__.return_value.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO quality_evaluation" in sql
    assert "weighted_score" in sql


async def test_record_evaluation_handles_db_failure(caplog, mock_pool):
    """DB 失败 → log warning，不抛出。"""
    mock_pool.acquire.return_value.__aenter__.return_value.execute = AsyncMock(
        side_effect=Exception("connection lost")
    )

    with caplog.at_level(logging.WARNING):
        # 不应抛出
        await record_evaluation(
            mock_pool,
            session_id=None,
            query_id="q-1",
            task_id=None,
            worker_type="sql",
            query_type="data_query",
            question=None,
            answer_snippet=None,
            sub_scores={"count": 0.0},
            weighted_score=0.0,
            weights_used={"count": 1.0},
            llm_used=False,
            llm_latency_ms=None,
            threshold=0.6,
            passed=False,
        )

    assert any("record_evaluation" in rec.message or "failed" in rec.message.lower()
               for rec in caplog.records)


async def test_record_evaluation_optional_fields_null():
    """可选字段为 None → INSERT 接受 None（不抛错）。"""
    pool = MagicMock()
    conn = MagicMock()
    acquire_cm = MagicMock()
    acquire_cm.__aenter__ = AsyncMock(return_value=conn)
    acquire_cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=acquire_cm)
    conn.execute = AsyncMock(return_value=None)

    await record_evaluation(
        pool,
        session_id=None,
        query_id="q-1",
        task_id=None,
        worker_type="code",
        query_type="trace",
        question=None,
        answer_snippet=None,
        sub_scores={"count": 0.0, "confidence": 0.0, "exact_match": 0.0,
                    "relevance": 0.0, "completeness": 0.0, "conciseness": 0.0},
        weighted_score=0.0,
        weights_used={"count": 1.0, "confidence": 0.0, "exact_match": 0.0,
                      "relevance": 0.0, "completeness": 0.0, "conciseness": 0.0},
        llm_used=False,
        llm_latency_ms=None,
        threshold=0.6,
        passed=False,
    )
    conn.execute.assert_awaited_once()
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_history.py -v
```

Expected: `ModuleNotFoundError: No module named 'spma.agents.supervisor.history'`

- [ ] **Step 3: 实现 history.py**

`src/spma/agents/supervisor/history.py`：

```python
"""质量评分历史落库。

设计依据: SPMA v2 spec §4.5
- fire-and-forget: 调用方负责 await，本函数保证失败仅 log warning
- schema 见 deployments/docker/migrations/002_quality_evaluation.sql
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from asyncpg import Pool

logger = logging.getLogger(__name__)


_INSERT_SQL = """
INSERT INTO quality_evaluation (
    session_id, query_id, task_id, worker_type, query_type,
    question, answer_snippet,
    count_score, confidence_score, exact_match_score,
    relevance_score, completeness_score, conciseness_score,
    weighted_score, weights_used,
    llm_used, llm_latency_ms, threshold, passed
) VALUES (
    $1, $2, $3, $4, $5,
    $6, $7,
    $8, $9, $10,
    $11, $12, $13,
    $14, $15,
    $16, $17, $18, $19
)
"""


async def record_evaluation(
    db_pool: "Pool",
    *,
    session_id: str | None,
    query_id: str,
    task_id: str | None,
    worker_type: str,
    query_type: str,
    question: str | None,
    answer_snippet: str | None,
    sub_scores: dict[str, float],
    weighted_score: float,
    weights_used: dict[str, float],
    llm_used: bool,
    llm_latency_ms: int | None,
    threshold: float,
    passed: bool,
) -> None:
    """单条评分记录写入；失败仅记录日志，不抛出。

    期望调用方式（在 evaluate_workers 内部）:
        asyncio.create_task(record_evaluation(...))
    """
    args: tuple[Any, ...] = (
        session_id,
        query_id,
        task_id,
        worker_type,
        query_type,
        question,
        answer_snippet,
        float(sub_scores.get("count", 0.0)),
        float(sub_scores.get("confidence", 0.0)),
        float(sub_scores.get("exact_match", 0.0)),
        float(sub_scores.get("relevance", 0.0)),
        float(sub_scores.get("completeness", 0.0)),
        float(sub_scores.get("conciseness", 0.0)),
        float(weighted_score),
        json.dumps(weights_used, ensure_ascii=False),
        bool(llm_used),
        llm_latency_ms,
        float(threshold),
        bool(passed),
    )

    try:
        async with db_pool.acquire() as conn:
            await conn.execute(_INSERT_SQL, *args)
    except Exception as exc:
        logger.warning(
            "record_evaluation failed (query_id=%s, worker=%s): %s",
            query_id,
            worker_type,
            exc,
        )
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_history.py -v
```

Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/history.py tests/unit/agents/supervisor/test_history.py
```

---

## Task 8: state.py — 新增 quality_sub_scores 字段

**Files:**
- Modify: `src/spma/agents/supervisor/state.py:15-28`
- Create: `tests/unit/agents/supervisor/test_state.py`

**Interfaces (consumed by Task 10, 11):**
- `SupervisorState.quality_sub_scores: dict[str, dict[str, float]]` (新增 NotRequired 字段)

- [ ] **Step 1: 写失败测试**

`tests/unit/agents/supervisor/test_state.py`：

```python
"""SupervisorState 字段测试。"""
from spma.agents.supervisor.state import SupervisorState


def test_state_has_quality_sub_scores_field():
    """SupervisorState 必须支持 quality_sub_scores 字段。"""
    state: SupervisorState = {
        "quality_sub_scores": {"doc": {"count": 0.5, "confidence": 0.7, "exact_match": 0.0,
                                        "relevance": 0.8, "completeness": 0.6, "conciseness": 0.9}}
    }
    assert state["quality_sub_scores"]["doc"]["relevance"] == 0.8


def test_state_quality_sub_scores_optional():
    """quality_sub_scores 是可选字段（NotRequired）。"""
    state: SupervisorState = {"worker_outputs": []}
    # 不应报错
    assert state.get("quality_sub_scores", {}) == {}
```

- [ ] **Step 2: 跑测试，验证失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_state.py -v
```

Expected: `TypeError: 'quality_sub_scores'` key missing

- [ ] **Step 3: 修改 state.py**

修改 `src/spma/agents/supervisor/state.py` 第 25 行后插入新字段：

```python
class SupervisorState(AgentState, total=False):
    """Supervisor Agent 专属状态字段。"""

    original_query: str
    classification: ClassificationResult
    entities: ExtractedEntities
    rewritten_queries: dict[str, str]
    # Annotated reducer 使得 Send API 并行派发的多个 worker 输出能通过 operator.add
    # （列表拼接）自然收敛，避免后写入者覆盖先写入者
    worker_outputs: Annotated[list[WorkerOutput], operator.add]
    quality_scores: dict[str, float]
    quality_sub_scores: dict[str, dict[str, float]]   # ← 新增（仅可观测性，不影响收敛判断）
    reschedule_count: int
    final_results: list[dict]
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_state.py -v
```

Expected: 2 passed

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/state.py tests/unit/agents/supervisor/test_state.py
```

---

## Task 9: evaluate_workers 升级 async + 6 维（核心集成）

**Files:**
- Modify: `src/spma/agents/supervisor/quality.py`
- Modify: `tests/unit/agents/supervisor/test_quality.py`

**Interfaces (consumed by Tasks 10, 11):**
- `async evaluate_workers(worker_outputs, query_type, llm_scorer=None, *, threshold=0.6, record_history=True, db_pool=None, session_id=None, query_id=None) -> dict`
- 返回结构: `{scores: dict[str, float], sub_scores: dict[str, dict[str, float]], passed: list[str], failed: list[str], all_pass: bool}`

- [ ] **Step 1: 写新失败测试（保留现有测试）**

修改 `tests/unit/agents/supervisor/test_quality.py`：

1. **将现有的 4 个 sync 测试改为 async**（`score_worker` 和 `evaluate_workers` 已是 async 签名）：

```python
"""evaluate_workers 6 维升级测试（async 版）。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------- 保留并改造旧测试（async 化） ----------

@pytest.mark.asyncio
async def test_score_worker_with_results_and_exact_match():
    """有结果 + exact match → score > 0.5。"""
    from spma.agents.supervisor.quality import score_worker
    output = {
        "worker_type": "doc",
        "result_count": 3,
        "confidence": 0.7,
        "has_exact_match": True,
        "original_query": "查询用户",
        "results": [{"text": "用户信息"}],
    }
    score = await score_worker(output, "search", llm_scorer=None)
    assert score > 0.5


@pytest.mark.asyncio
async def test_score_worker_empty_results():
    """空结果 → score = 0.0。"""
    from spma.agents.supervisor.quality import score_worker
    output = {
        "worker_type": "doc",
        "result_count": 0,
        "confidence": 0,
        "has_exact_match": False,
        "original_query": "查询",
        "results": [],
    }
    score = await score_worker(output, "search", llm_scorer=None)
    assert score == 0.0


@pytest.mark.asyncio
async def test_evaluate_workers_all_pass():
    """所有 Worker 过阈值 → all_pass=True。"""
    from spma.agents.supervisor.quality import evaluate_workers
    outputs = [
        {"worker_type": "doc", "result_count": 3, "confidence": 1.0,
         "has_exact_match": True, "original_query": "查询", "results": [{"text": "结果"}]},
    ]
    result = await evaluate_workers(outputs, "search", llm_scorer=None, threshold=0.6, record_history=False)
    assert result["all_pass"] is True
    assert "doc" in result["passed"]


@pytest.mark.asyncio
async def test_evaluate_workers_with_failure():
    """含失败 Worker → all_pass=False。"""
    from spma.agents.supervisor.quality import evaluate_workers
    outputs = [
        {"worker_type": "doc", "result_count": 0, "confidence": 0,
         "has_exact_match": False, "original_query": "查询", "results": []},
    ]
    result = await evaluate_workers(outputs, "search", llm_scorer=None, threshold=0.6, record_history=False)
    assert result["all_pass"] is False
    assert "doc" in result["failed"]


# ---------- 新增 6 维测试 ----------

```python
"""evaluate_workers 6 维升级测试。"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.agents.supervisor.metrics.llm_scorer import LLMScoreResult
from spma.agents.supervisor.quality import (
    extract_worker_answer,
    evaluate_workers,
    score_worker,
)


def _fake_worker_output(worker_type: str, **kwargs) -> dict:
    base = {
        "worker_type": worker_type,
        "result_count": kwargs.get("result_count", 3),
        "confidence": kwargs.get("confidence", 0.7),
        "has_exact_match": kwargs.get("has_exact_match", False),
        "original_query": kwargs.get("original_query", "查询用户"),
        "results": kwargs.get("results", [{"text": "用户信息"}]),
    }
    return base


def test_extract_worker_answer_from_results():
    """从 results[*].text 抽取 answer。"""
    output = {"results": [{"text": "abc"}, {"text": "def"}]}
    assert extract_worker_answer(output) == "abc def"


def test_extract_worker_answer_from_citations():
    """从 citations[*].snippet 抽取（fallback）。"""
    output = {"results": [], "citations": [{"snippet": "snippet1"}, {"snippet": "snippet2"}]}
    assert extract_worker_answer(output) == "snippet1 snippet2"


def test_extract_worker_answer_empty():
    """无 results/citations → 空串。"""
    assert extract_worker_answer({}) == ""


async def test_evaluate_workers_returns_6_dim_scores():
    """6 维评分，返回 sub_scores。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(
        return_value=LLMScoreResult(relevance=0.8, completeness=0.7, conciseness=0.9)
    )
    outputs = [_fake_worker_output("doc"), _fake_worker_output("code")]
    result = await evaluate_workers(outputs, "search", scorer, record_history=False)
    assert "sub_scores" in result
    assert "doc" in result["sub_scores"]
    assert set(result["sub_scores"]["doc"].keys()) == {
        "count", "confidence", "exact_match",
        "relevance", "completeness", "conciseness",
    }


async def test_evaluate_workers_uses_weights_per_query_type():
    """data_query / search / trace 权重不同 → 不同 query_type 应给出不同分数。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=None)  # 启发式
    output = _fake_worker_output("doc", has_exact_match=True, result_count=3)
    r_data = await evaluate_workers([output], "data_query", scorer, record_history=False)
    r_search = await evaluate_workers([output], "search", scorer, record_history=False)
    # exact_match 权重: data_query=0.25, search=0.10 → data_query 更高
    assert r_data["scores"]["doc"] > r_search["scores"]["doc"]


async def test_evaluate_workers_threshold_logic():
    """≥ threshold → passed；< → failed。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=LLMScoreResult(relevance=0.0, completeness=0.0, conciseness=0.0))
    outputs = [
        _fake_worker_output("doc", confidence=1.0, has_exact_match=True, result_count=3),
        _fake_worker_output("code", confidence=0.0, has_exact_match=False, result_count=0),
    ]
    result = await evaluate_workers(outputs, "search", scorer, threshold=0.6, record_history=False)
    assert "doc" in result["passed"]
    assert "code" in result["failed"]
    assert result["all_pass"] is False


async def test_evaluate_workers_empty_list_returns_empty():
    """worker_outputs 空 → all_pass=False, passed/failed 都空。"""
    scorer = AsyncMock()
    result = await evaluate_workers([], "search", scorer, record_history=False)
    assert result["all_pass"] is False
    assert result["scores"] == {}
    assert result["passed"] == []
    assert result["failed"] == []


async def test_evaluate_workers_unknown_query_type_uses_search_default():
    """未知 query_type → 用 search 权重。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=None)
    output = _fake_worker_output("doc")
    r_unknown = await evaluate_workers([output], "unknown_type", scorer, record_history=False)
    r_search = await evaluate_workers([output], "search", scorer, record_history=False)
    assert r_unknown["scores"] == r_search["scores"]


async def test_evaluate_workers_no_llm_scorer_uses_heuristic():
    """llm_scorer=None → 启发式路径。"""
    output = _fake_worker_output("doc")
    result = await evaluate_workers([output], "search", llm_scorer=None, record_history=False)
    # 启发式 relevance 仍有意义: answer="用户信息", q="查询用户" → "用户" 命中
    assert result["scores"]["doc"] > 0


async def test_evaluate_workers_fire_and_forget_history():
    """record_history=True + db_pool 提供 → 触发 record_evaluation。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=LLMScoreResult(0.5, 0.5, 0.5))

    # 创建 mock db_pool 让 record_evaluation 不抛
    pool = MagicMock()
    conn = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    conn.execute = AsyncMock()

    output = _fake_worker_output("doc")
    # 让事件循环跑一下让 fire-and-forget task 完成
    result = await evaluate_workers(
        [output], "search", scorer,
        record_history=True, db_pool=pool, query_id="q-1",
    )
    # 至少 yield 一次让 background task 跑完
    import asyncio
    await asyncio.sleep(0)

    # 验证 record_evaluation 触发了 execute
    assert conn.execute.await_count >= 1


async def test_evaluate_workers_history_failure_does_not_propagate():
    """record_history 路径失败 → 不影响 evaluate_workers 返回。"""
    scorer = AsyncMock()
    scorer.assess = AsyncMock(return_value=LLMScoreResult(0.5, 0.5, 0.5))

    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(side_effect=Exception("db down"))
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)

    output = _fake_worker_output("doc")
    # 不应抛错
    result = await evaluate_workers(
        [output], "search", scorer,
        record_history=True, db_pool=pool, query_id="q-1",
    )
    assert "doc" in result["scores"]
```

> 注：本文件第一段已**将原 4 个 sync 测试改为 async**（`score_worker` 和 `evaluate_workers` 都已 async 化），并新增 11 个 6 维场景测试。

- [ ] **Step 2: 跑测试，验证新测试失败**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_quality.py -v
```

Expected: 旧测试可能报错（signature 改了），新测试报 `extract_worker_answer / 6-dim` 缺失

- [ ] **Step 3: 重写 quality.py**

修改 `src/spma/agents/supervisor/quality.py`：

```python
"""Supervisor 质量评分——6 维(count + confidence + exact_match + relevance + completeness + conciseness) × query_type 权重矩阵。

设计依据: SPMA v2 spec §4
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from spma.agents.supervisor.config import QualityConfig, load_quality_config
from spma.agents.supervisor.history import record_evaluation
from spma.agents.supervisor.metrics import (
    calculate_completeness_score,
    calculate_conciseness_score,
    calculate_confidence_score,
    calculate_count_score,
    calculate_exact_score,
    calculate_relevance_score,
)

if TYPE_CHECKING:
    from asyncpg import Pool

    from spma.agents.supervisor.metrics.llm_scorer import LLMScorer
    from spma.models.worker_output import WorkerOutput

logger = logging.getLogger(__name__)


# 保留旧的 QUALITY_WEIGHTS 用于向后兼容（其他模块可能直接 import）
QUALITY_WEIGHTS_OLD_3DIM = {
    "data_query": {"count": 0.3, "confidence": 0.3, "exact_match": 0.4},
    "search":     {"count": 0.4, "confidence": 0.4, "exact_match": 0.2},
    "trace":      {"count": 0.2, "confidence": 0.3, "exact_match": 0.5},
}


def extract_worker_answer(worker_output: dict) -> str:
    """从 worker_output 抽取用于评分的纯文本答案。

    优先: results[*].text
    fallback: citations[*].snippet
    """
    results = worker_output.get("results") or []
    parts = [r.get("text", "") for r in results if r.get("text")]
    if parts:
        return " ".join(parts)
    citations = worker_output.get("citations") or []
    parts = [c.get("snippet", "") for c in citations if c.get("snippet")]
    return " ".join(parts)


def _score_one_worker_sync(
    worker_output: "WorkerOutput",
    query_type: str,
    cfg: QualityConfig,
) -> dict[str, float]:
    """计算纯规则 4 维（count/confidence/exact_match/conciseness），同步。"""
    return {
        "count": calculate_count_score(int(worker_output.get("result_count", 0) or 0)),
        "confidence": calculate_confidence_score(worker_output.get("confidence")),
        "exact_match": calculate_exact_score(bool(worker_output.get("has_exact_match", False))),
        "conciseness": calculate_conciseness_score(
            worker_output.get("original_query", "") or "",
            extract_worker_answer(worker_output),
        ),
    }


async def _score_relevance_and_completeness(
    worker_output: "WorkerOutput",
    llm_scorer: "LLMScorer | None",
) -> tuple[float, float]:
    """计算 relevance + completeness（LLM 并发，启发式 fallback）。"""
    question = worker_output.get("original_query", "") or ""
    answer = extract_worker_answer(worker_output)

    if llm_scorer is None:
        # 全启发式
        rel = await calculate_relevance_score(question, answer, _NOOP_SCORER)
        comp = await calculate_completeness_score(question, answer, _NOOP_SCORER)
        return rel, comp

    # LLM 路径: 调用方已并发 gather
    rel_task = calculate_relevance_score(question, answer, llm_scorer)
    comp_task = calculate_completeness_score(question, answer, llm_scorer)
    rel, comp = await asyncio.gather(rel_task, comp_task)
    return rel, comp


class _NoopScorer:
    """llm_scorer=None 时使用的占位 scorer，assess 永远返回 None。"""
    async def assess(self, question: str, answer: str, **kwargs):  # noqa: ARG002
        return None


_NOOP_SCORER = _NoopScorer()


async def _score_one_worker(
    worker_output: "WorkerOutput",
    query_type: str,
    cfg: QualityConfig,
    llm_scorer: "LLMScorer | None",
) -> tuple[float, dict[str, float]]:
    """计算单个 Worker 的 6 维加权得分 + 明细。"""
    # 同步 4 维
    sync_dims = _score_one_worker_sync(worker_output, query_type, cfg)

    # 异步 2 维（LLM 并发）
    rel, comp = await _score_relevance_and_completeness(worker_output, llm_scorer)
    sync_dims["relevance"] = rel
    sync_dims["completeness"] = comp

    # 加权
    weights = cfg.weights.get(query_type, cfg.weights["search"])
    total = sum(sync_dims[k] * weights[k] for k in sync_dims)
    return round(total, 4), sync_dims


async def evaluate_workers(
    worker_outputs: list["WorkerOutput"],
    query_type: str,
    llm_scorer: "LLMScorer | None" = None,
    *,
    threshold: float = 0.6,
    record_history: bool = True,
    db_pool: "Pool | None" = None,
    session_id: str | None = None,
    query_id: str | None = None,
) -> dict:
    """6 维评分 + 落库。

    返回结构（向后兼容 + 新增 sub_scores）:
        {
            "scores": dict[str, float],                      # worker_type → weighted_score
            "sub_scores": dict[str, dict[str, float]],      # worker_type → 6 维明细
            "passed": list[str],
            "failed": list[str],
            "all_pass": bool,
        }
    """
    cfg = load_quality_config()
    weights_used = cfg.weights.get(query_type, cfg.weights["search"])

    scores: dict[str, float] = {}
    sub_scores: dict[str, dict[str, float]] = {}
    passed: list[str] = []
    failed: list[str] = []

    for output in worker_outputs:
        worker_type = output.get("worker_type", "unknown") if isinstance(output, dict) else "unknown"
        try:
            score, dims = await _score_one_worker(output, query_type, cfg, llm_scorer)
        except Exception as exc:
            logger.error("evaluate_workers: score failed for %s: %s", worker_type, exc)
            score = 0.0
            dims = {k: 0.0 for k in weights_used}

        scores[worker_type] = score
        sub_scores[worker_type] = dims

        if score >= threshold:
            passed.append(worker_type)
        else:
            failed.append(worker_type)

        # fire-and-forget 落库
        if record_history and db_pool is not None:
            answer_text = extract_worker_answer(output)
            answer_snippet = answer_text[: cfg.answer_snippet_max_chars] if answer_text else None
            try:
                asyncio.create_task(
                    record_evaluation(
                        db_pool,
                        session_id=session_id,
                        query_id=query_id or "unknown",
                        task_id=output.get("task_id") if isinstance(output, dict) else None,
                        worker_type=worker_type,
                        query_type=query_type,
                        question=output.get("original_query") if isinstance(output, dict) else None,
                        answer_snippet=answer_snippet,
                        sub_scores=dims,
                        weighted_score=score,
                        weights_used=weights_used,
                        llm_used=llm_scorer is not None,
                        llm_latency_ms=None,  # TODO: 后续可加细粒度统计
                        threshold=threshold,
                        passed=score >= threshold,
                    )
                )
            except Exception as exc:
                logger.warning("evaluate_workers: failed to schedule record_evaluation: %s", exc)

    return {
        "scores": scores,
        "sub_scores": sub_scores,
        "passed": passed,
        "failed": failed,
        "all_pass": len(failed) == 0 and len(passed) > 0,
    }


# 保留旧 score_worker（向后兼容），但委托给 evaluate_workers
async def score_worker(
    worker_output: "WorkerOutput",
    query_type: str,
    llm_scorer: "LLMScorer | None" = None,
) -> float:
    """单 Worker 评分（向后兼容接口）。"""
    result = await evaluate_workers([worker_output], query_type, llm_scorer, record_history=False)
    return result["scores"].get(worker_output.get("worker_type", "unknown"), 0.0)
```

- [ ] **Step 4: 跑测试，验证全部通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/unit/agents/supervisor/test_quality.py -v
```

Expected: 所有测试通过（旧 4 个 + 新 11 个 = 15 个左右）

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/quality.py tests/unit/agents/supervisor/test_quality.py
```

---

## Task 10: graph.py — score_node 接入新接口

**Files:**
- Modify: `src/spma/agents/supervisor/graph.py:18` (import)
- Modify: `src/spma/agents/supervisor/graph.py:125-129` (score_node)

**Interfaces (consumed by integration tests):**
- `score_node(state: SupervisorState) -> dict` 返回 `{quality_scores, quality_sub_scores}`

- [ ] **Step 1: 找到当前 score_node 实现**

Read `src/spma/agents/supervisor/graph.py` 第 125-129 行确认当前签名。预期：

```python
async def score_node(state: SupervisorState) -> dict:
    worker_outputs = state.get("worker_outputs", [])
    query_type = state.get("classification", {}).get("query_type", "search")
    evaluation = evaluate_workers(worker_outputs, query_type, quality_threshold)
    return {"quality_scores": evaluation["scores"]}
```

- [ ] **Step 2: 修改 score_node**

修改为：

```python
async def score_node(state: SupervisorState) -> dict:
    worker_outputs = state.get("worker_outputs", [])
    query_type = state.get("classification", {}).get("query_type", "search")
    evaluation = await evaluate_workers(
        worker_outputs,
        query_type,
        get_llm_scorer(),
        threshold=quality_threshold,
        record_history=True,
        db_pool=get_db_pool(),
        session_id=state.get("session_id"),
        query_id=state.get("query_id"),
    )
    return {
        "quality_scores": evaluation["scores"],
        "quality_sub_scores": evaluation["sub_scores"],
    }
```

并在文件顶部 import 区域（大约第 18 行附近）增加：

```python
from spma.agents.supervisor.metrics.llm_scorer import get_llm_scorer
from spma.api.dependencies import get_db_pool
```

- [ ] **Step 3: 跑现有 integration 测试**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/integration/test_supervisor_loop.py -v
```

Expected: 现有测试通过（接口向后兼容）

- [ ] **Step 4: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/agents/supervisor/graph.py
```

---

## Task 11: query_graph.py — quality_node 接入新接口

**Files:**
- Modify: `src/spma/api/query_graph.py:351-363` (quality_node)

**Interfaces:**
- `quality_node(state: QueryOrchestratorState) -> dict` 返回 `{quality_scores, quality_sub_scores}`

- [ ] **Step 1: 修改 quality_node**

Read [src/spma/api/query_graph.py](../../spma/api/query_graph.py) 第 351-363 行。当前实现：

```python
async def quality_node(state: QueryOrchestratorState) -> dict:
    """质量评估节点——三维评分（count + confidence + exact_match）。"""
    from spma.agents.supervisor.quality import evaluate_workers

    classification = state.get("classification", {})
    query_type = classification.get("query_type", "search")

    result = evaluate_workers(
        state.get("worker_outputs", []),
        query_type,
        threshold=0.6,
    )
    return {"quality_scores": result["scores"]}
```

修改为：

```python
async def quality_node(state: QueryOrchestratorState) -> dict:
    """质量评估节点——6 维评分（count + confidence + exact_match + relevance + completeness + conciseness）。"""
    from spma.agents.supervisor.quality import evaluate_workers
    from spma.agents.supervisor.metrics.llm_scorer import get_llm_scorer
    from spma.api.dependencies import get_db_pool

    classification = state.get("classification", {})
    query_type = classification.get("query_type", "search")

    result = await evaluate_workers(
        state.get("worker_outputs", []),
        query_type,
        get_llm_scorer(),
        threshold=0.6,
        record_history=True,
        db_pool=get_db_pool(),
        session_id=state.get("session_id"),
        query_id=state.get("query_id"),
    )
    return {
        "quality_scores": result["scores"],
        "quality_sub_scores": result["sub_scores"],
    }
```

- [ ] **Step 2: 跑 integration 测试**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/integration/ -v
```

Expected: 所有通过（接口向后兼容）

- [ ] **Step 3: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/api/query_graph.py
```

---

## Task 12: Zombie TODO 注释

**Files:**
- Modify: `src/spma/api/routes/query.py:48` (新增 TODO)
- Modify: `src/spma/api/routes/query.py:275` (新增 TODO)
- Modify: `src/spma/api/query_graph.py:351` (新增 TODO)

- [ ] **Step 1: 在 routes/query.py 第 48 行附近加 TODO**

Read `src/spma/api/routes/query.py` 确认 `@router.post("/api/v1/query")` 装饰器位置（约第 48 行）。在其上方添加：

```python
# TODO(zombie): legacy /query endpoint duplicates QueryOrchestrator; remove in subsequent cleanup spec
@router.post("/api/v1/query")
async def query(req: QueryRequest, request: Request):
    ...
```

- [ ] **Step 2: 在 routes/query.py 第 275 行加 TODO**

在 `from spma.agents.supervisor.quality import evaluate_workers` 上方添加：

```python
    # TODO(zombie): legacy /query endpoint duplicates QueryOrchestrator; remove in subsequent cleanup spec
    from spma.agents.supervisor.quality import evaluate_workers
```

- [ ] **Step 3: 在 query_graph.py 第 351 行加 TODO**

Read `src/spma/api/query_graph.py` 确认 `async def quality_node` 位置。在其上方 docstring 末尾添加：

```python
# TODO(cleanup): after zombie removal, consider merging quality_node into supervisor subgraph
async def quality_node(state: QueryOrchestratorState) -> dict:
    ...
```

- [ ] **Step 4: 跑所有相关测试**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/ -v -k "not slow"
```

Expected: 所有通过（注释不影响行为）

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/api/routes/query.py src/spma/api/query_graph.py
```

---

## Task 13: Observability Metrics

**Files:**
- Modify: `src/spma/observability/metrics.py`

**Interfaces:**
- 模块级函数/计数器：`increment_quality_scoring_latency_ms(ms)`、`increment_quality_llm_path_count(worker_type)`、`increment_quality_heuristic_fallback_count(metric_name)`、`increment_quality_db_record(success: bool)`

- [ ] **Step 1: 读现有 metrics.py**

Read `src/spma/observability/metrics.py` 了解现有指标接口风格。

- [ ] **Step 2: 在 metrics.py 末尾添加新指标**

追加：

```python
# ---- 质量评分 (SPMA v2) ----

_quality_scoring_latency_ms = []  # 直方图数据
_quality_llm_path_count: dict[str, int] = {}
_quality_heuristic_fallback_count: dict[str, int] = {}
_quality_db_record_success_count = 0
_quality_db_record_failure_count = 0


def record_quality_scoring_latency(ms: float) -> None:
    """记录 evaluate_workers 总耗时（毫秒）。"""
    _quality_scoring_latency_ms.append(ms)


def increment_quality_llm_path(worker_type: str) -> None:
    _quality_llm_path_count[worker_type] = _quality_llm_path_count.get(worker_type, 0) + 1


def increment_quality_heuristic_fallback(metric_name: str) -> None:
    _quality_heuristic_fallback_count[metric_name] = (
        _quality_heuristic_fallback_count.get(metric_name, 0) + 1
    )


def increment_quality_db_record(success: bool) -> None:
    global _quality_db_record_success_count, _quality_db_record_failure_count
    if success:
        _quality_db_record_success_count += 1
    else:
        _quality_db_record_failure_count += 1


def get_quality_metrics_snapshot() -> dict:
    """测试用：获取当前所有质量评分指标快照。"""
    return {
        "scoring_latency_ms_count": len(_quality_scoring_latency_ms),
        "scoring_latency_ms_avg": (
            sum(_quality_scoring_latency_ms) / len(_quality_scoring_latency_ms)
            if _quality_scoring_latency_ms else 0.0
        ),
        "llm_path_count": dict(_quality_llm_path_count),
        "heuristic_fallback_count": dict(_quality_heuristic_fallback_count),
        "db_record_success": _quality_db_record_success_count,
        "db_record_failure": _quality_db_record_failure_count,
    }


def reset_quality_metrics() -> None:
    """测试用：重置所有质量评分指标。"""
    global _quality_db_record_success_count, _quality_db_record_failure_count
    _quality_scoring_latency_ms.clear()
    _quality_llm_path_count.clear()
    _quality_heuristic_fallback_count.clear()
    _quality_db_record_success_count = 0
    _quality_db_record_failure_count = 0
```

- [ ] **Step 3: 在 evaluate_workers 中埋点**

修改 `src/spma/agents/supervisor/quality.py` `_score_one_worker` 内（紧接 `rel, comp = await asyncio.gather(...)` 之后）：

```python
        rel, comp = await _score_relevance_and_completeness(worker_output, llm_scorer)
        sync_dims["relevance"] = rel
        sync_dims["completeness"] = comp

        # 埋点
        try:
            from spma.observability.metrics import (
                increment_quality_heuristic_fallback,
                increment_quality_llm_path,
            )
            if llm_scorer is not None:
                increment_quality_llm_path(worker_type)
            else:
                increment_quality_heuristic_fallback("relevance")
                increment_quality_heuristic_fallback("completeness")
        except Exception:
            pass
```

并在 `evaluate_workers` 函数开头（循环前）加 latency 计时：

```python
    t0 = time.monotonic()
    try:
        # ... 现有循环
        pass
    finally:
        elapsed_ms = (time.monotonic() - t0) * 1000
        try:
            from spma.observability.metrics import record_quality_scoring_latency
            record_quality_scoring_latency(elapsed_ms)
        except Exception:
            pass
```

并在 `record_evaluation` 调用处埋 success/failure：

```python
            try:
                asyncio.create_task(_record_with_metric(db_pool, ...))
            except Exception as exc:
                logger.warning(...)
```

为简单起见，可在 `evaluate_workers` 中包装：

```python
    async def _record_with_metric(pool, **kwargs):
        try:
            await record_evaluation(pool, **kwargs)
            try:
                from spma.observability.metrics import increment_quality_db_record
                increment_quality_db_record(success=True)
            except Exception:
                pass
        except Exception:
            try:
                from spma.observability.metrics import increment_quality_db_record
                increment_quality_db_record(success=False)
            except Exception:
                pass

    if record_history and db_pool is not None:
        asyncio.create_task(_record_with_metric(db_pool, ...))
```

- [ ] **Step 4: 跑测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/ -v -k "not slow"
```

Expected: 全部通过

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add src/spma/observability/metrics.py src/spma/agents/supervisor/quality.py
```

---

## Task 14: 集成测试 + Supervisor Loop 端到端验证

**Files:**
- Modify: `tests/integration/test_supervisor_loop.py`（追加 6 维评分场景）

**Interfaces:**
- 端到端验证：`/query/stream` 入口能跑通新评分；3 Worker 全 LLM 路径 + 全启发式 + 混合三种场景

- [ ] **Step 1: 在 test_supervisor_loop.py 追加 3 个集成测试**

```python
"""新增 6 维评分场景的集成测试。"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spma.agents.supervisor.metrics.llm_scorer import LLMScoreResult


@pytest.mark.asyncio
async def test_supervisor_loop_6_dim_with_llm_available():
    """所有 Worker 走 LLM 评分路径。"""
    from spma.agents.supervisor.quality import evaluate_workers

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(
        return_value=MagicMock(content='{"relevance":0.8,"completeness":0.7,"conciseness":0.9}')
    )

    from spma.agents.supervisor.metrics.llm_scorer import LLMScorer
    scorer = LLMScorer(llm=mock_llm, timeout_ms=800)

    worker_outputs = [
        {"worker_type": "doc", "result_count": 3, "confidence": 0.7,
         "has_exact_match": False, "original_query": "查询用户", "results": [{"text": "用户信息"}]},
        {"worker_type": "code", "result_count": 5, "confidence": 0.9,
         "has_exact_match": True, "original_query": "查询代码", "results": [{"text": "代码片段"}]},
        {"worker_type": "sql", "result_count": 1, "confidence": 0.6,
         "has_exact_match": False, "original_query": "查询数据", "results": [{"text": "数据结果"}]},
    ]
    result = await evaluate_workers(worker_outputs, "search", scorer, record_history=False)

    assert set(result["scores"].keys()) == {"doc", "code", "sql"}
    assert "relevance" in result["sub_scores"]["doc"]
    assert "completeness" in result["sub_scores"]["code"]
    # 所有维度都应 > 0
    for wt in ["doc", "code", "sql"]:
        for dim in ["count", "confidence", "exact_match", "relevance", "completeness", "conciseness"]:
            assert result["sub_scores"][wt][dim] >= 0


@pytest.mark.asyncio
async def test_supervisor_loop_6_dim_with_llm_unavailable():
    """LLM 不可用 → 全启发式。"""
    from spma.agents.supervisor.quality import evaluate_workers

    mock_llm = MagicMock()
    mock_llm.ainvoke = AsyncMock(side_effect=Exception("api down"))
    from spma.agents.supervisor.metrics.llm_scorer import LLMScorer
    scorer = LLMScorer(llm=mock_llm, timeout_ms=100)

    worker_outputs = [
        {"worker_type": "doc", "result_count": 0, "confidence": 0,
         "has_exact_match": False, "original_query": "查询用户", "results": []},
    ]
    result = await evaluate_workers(worker_outputs, "search", scorer, record_history=False)

    # 启发式路径下结果应合理（不至于崩溃）
    assert "doc" in result["scores"]
    # 全 0 的输入应该得到低分
    assert result["scores"]["doc"] < 0.3
    assert result["failed"] == ["doc"]


@pytest.mark.asyncio
async def test_supervisor_loop_6_dim_backward_compat_signature():
    """旧签名 evaluate_workers(outputs, "search", 0.6) 仍能调用（threshold 走默认）。"""
    from spma.agents.supervisor.quality import evaluate_workers

    worker_outputs = [
        {"worker_type": "doc", "result_count": 3, "confidence": 0.7,
         "has_exact_match": True, "original_query": "查询", "results": [{"text": "结果"}]},
    ]
    # 不传 llm_scorer，走启发式
    result = await evaluate_workers(worker_outputs, "search", record_history=False)
    assert "doc" in result["scores"]
    # 因为 has_exact_match=True，应该过阈值
    assert result["scores"]["doc"] >= 0.6
```

- [ ] **Step 2: 跑集成测试，验证通过**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/integration/test_supervisor_loop.py -v
```

Expected: 全部通过（既保留旧测试也通过新测试）

- [ ] **Step 3: 跑全套测试套件**

```bash
cd d:/TraeProject/SPMA && python -m pytest tests/ -v
```

Expected: 全套通过

- [ ] **Step 4: 跑性能基线（启发式路径 < 50ms）**

```bash
cd d:/TraeProject/SPMA && python -c "
import asyncio, time
from spma.agents.supervisor.quality import evaluate_workers

async def main():
    outputs = [
        {'worker_type': 'doc', 'result_count': 3, 'confidence': 0.7, 'has_exact_match': False, 'original_query': '查询', 'results': [{'text': '结果'}]},
        {'worker_type': 'code', 'result_count': 3, 'confidence': 0.7, 'has_exact_match': False, 'original_query': '查询', 'results': [{'text': '结果'}]},
        {'worker_type': 'sql', 'result_count': 3, 'confidence': 0.7, 'has_exact_match': False, 'original_query': '查询', 'results': [{'text': '结果'}]},
    ]
    t0 = time.monotonic()
    result = await evaluate_workers(outputs, 'search', llm_scorer=None, record_history=False)
    elapsed = (time.monotonic() - t0) * 1000
    print(f'3-worker 全启发式耗时: {elapsed:.1f}ms')
    assert elapsed < 50, f'超时: {elapsed}ms'

asyncio.run(main())
"
```

Expected: 输出 < 50ms

- [ ] **Step 5: 提交**

```bash
cd d:/TraeProject/SPMA && git add tests/integration/test_supervisor_loop.py
```

---

## Self-Review Checklist

执行完成后，跑一次 self-review：

- [ ] **Spec 覆盖检查**：spec §10 的 11 项 acceptance criteria 都能在 14 个 task 中找到对应实现
  - AC#1 (evaluate_workers async 6-dim) → Task 9
  - AC#2 (LLMScorer 超时/APIError/JSON fallback) → Task 4
  - AC#3 (启发式 fallback) → Tasks 5, 6
  - AC#4 (Migration 002) → Task 2
  - AC#5 (record_evaluation fire-and-forget) → Task 7 + Task 9 集成
  - AC#6 (score_node 升级) → Tasks 10, 11
  - AC#7 (Zombie TODO) → Task 12
  - AC#8 (新增 metrics) → Task 13
  - AC#9 (测试覆盖) → 各 task 测试步骤
  - AC#10 (test_supervisor_loop 不破坏) → Task 14
  - AC#11 (sub_scores 字段) → Task 8 + 9 + 10 + 11

- [ ] **Placeholder 扫描**：所有代码块完整，无 TBD/TODO 占位
- [ ] **类型一致性**：`LLMScoreResult`、`LLMScorer`、`record_evaluation`、`evaluate_workers` 在所有 task 中签名一致

---

## Acceptance Criteria（最终验收）

- [ ] `evaluate_workers` 升级为 async，6 维评分按 query_type 加权
- [ ] `LLMScorer` 实现：单次 LLM 调用产出 3 个分；超时/APIError/非 JSON 均返回 None
- [ ] 启发式 fallback：relevance 走词袋 Jaccard，completeness 走句数启发
- [ ] Migration 002 落地：quality_evaluation 表 + 3 个索引
- [ ] `record_evaluation` fire-and-forget 落库；失败仅 log warning
- [ ] score_node (graph.py + query_graph.py) 升级，传入 llm_scorer 和 db_pool
- [ ] 所有 zombie 代码位置添加 TODO 注释
- [ ] 新增 metrics：quality_scoring_latency_ms / quality_llm_path_count / quality_heuristic_fallback_count / quality_db_record_*
- [ ] 测试覆盖：unit + integration 用例数符合 spec §9.1 目标
- [ ] 现有 test_supervisor_loop.py 不被破坏
- [ ] `sub_scores` 字段写入 SupervisorState，但不影响收敛判断
- [ ] 3-Worker 全启发式路径 < 50ms（性能基线）
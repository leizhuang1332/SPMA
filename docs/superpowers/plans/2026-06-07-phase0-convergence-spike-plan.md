# Phase 0 收敛判断 Spike 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 搭建独立的 Spike 实验环境，验证 LLM 能否可靠判断检索结果"够了"（精确率 ≥ 80%）。

**Architecture:** 独立的 `spike/` 目录，自包含 BM25+embedding 检索管线 + Agent 多轮循环模拟器 + 双人标注 golden 数据集 + 三种 Prompt 在 5 折 CV 框架下的消融实验，最终产出精确率评估报告和 Go/No-Go 决策。

**Tech Stack:** Python 3.11+, sentence-transformers, numpy, scikit-learn, anthropic (Haiku API), rank-bm25

**Spec:** [2026-06-07-phase0-convergence-spike-design.md](../specs/2026-06-07-phase0-convergence-spike-design.md)

---

## 文件结构概览

```
spike/
├── README.md
├── config.yaml
├── pyproject.toml
├── data/
│   ├── corpus/
│   │   ├── docs_sample.json
│   │   ├── code_sample.json
│   │   └── sql_schema_sample.json
│   ├── queries/
│   │   ├── raw_queries_shadowing.txt
│   │   ├── raw_queries_historical.txt
│   │   ├── raw_queries_synthetic.txt
│   │   └── approved_100_queries.json
│   ├── golden_eval_dataset.json
│   └── annotation_rubric.md
├── retrieval/
│   ├── __init__.py
│   ├── corpus_loader.py
│   ├── indexer.py
│   └── searcher.py
├── simulation/
│   ├── __init__.py
│   ├── multi_round_simulator.py
│   └── round_config.yaml
├── prompts/
│   ├── prompt_a_simple.md
│   ├── prompt_b_structured.md
│   ├── prompt_c_scoring.md
│   ├── prompt_final.md
│   └── ablation_results.csv
├── eval/
│   ├── __init__.py
│   ├── convergence_eval.py
│   ├── fold_splitter.py
│   ├── metrics.py
│   ├── fp_analyzer.py
│   └── report_generator.py
├── annotation/
│   ├── __init__.py
│   ├── annotation_tool.py
│   └── iaa.py
└── reports/
    ├── iaa_report.md
    ├── ablation_report.md
    ├── evaluation_report.md
    └── plan_b_design.md
```

---

### Task 1: 项目脚手架与配置

**Files:**
- Create: `spike/pyproject.toml`
- Create: `spike/config.yaml`
- Create: `spike/README.md`

- [ ] **Step 1: 创建 spike/pyproject.toml**

```toml
[project]
name = "spma-convergence-spike"
version = "0.1.0"
description = "Phase 0: LLM Convergence Judgment Spike"
requires-python = ">=3.11"
dependencies = [
    "sentence-transformers>=3.0.0",
    "numpy>=1.26.0",
    "scikit-learn>=1.5.0",
    "anthropic>=0.34.0",
    "rank-bm25>=0.2.2",
    "pyyaml>=6.0",
    "rich>=13.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-cov>=5.0",
]
```

- [ ] **Step 2: 创建 spike/config.yaml**

```yaml
# Spike 配置文件
api:
  provider: anthropic
  model: claude-haiku-4-5-20251001
  max_tokens: 512
  temperature: 0.0

retrieval:
  embedding_model: all-MiniLM-L6-v2
  # 中文备选: BAAI/bge-small-zh-v1.5
  bm25_weight: 0.3
  embedding_weight: 0.7
  top_k: 10

simulation:
  max_rounds: 3
  doc_top_k_per_round: [5, 8, 10]
  code_top_k_per_round: [3, 5, 7]
  sql_top_k_per_round: [3, 5, 7]
  golden_injection_ratio: 0.6
  sufficiency_coverage_threshold: 0.8

evaluation:
  folds: 5
  random_seed: 42
  precision_threshold: 0.80

paths:
  corpus_dir: data/corpus
  queries_dir: data/queries
  golden_dataset: data/golden_eval_dataset.json
  prompts_dir: prompts
  reports_dir: reports
```

- [ ] **Step 3: 创建 spike/README.md**

```markdown
# SPMA Phase 0: Convergence Judgment Spike

验证 LLM 能否可靠判断检索结果"够了"。

## 快速开始

```bash
cd spike
uv sync
```

## 流程

1. 数据准备: corpus → retrieval → simulation → annotation
2. Prompt 工程: 3 方案 × 5 折 CV → 选优 → FP 分析 → 改进
3. 最终评估: 5 折 CV → 分层分析 → Go/No-Go 报告

详见设计文档: `docs/superpowers/specs/2026-06-07-phase0-convergence-spike-design.md`
```

- [ ] **Step 4: 安装依赖并验证**

```bash
cd spike && uv sync
```
Expected: 所有依赖安装成功，无报错。

- [ ] **Step 5: Commit**

```bash
git add spike/pyproject.toml spike/config.yaml spike/README.md
git commit -m "feat(spike): add project scaffold and config"
```

---

### Task 2: 语料加载器

**Files:**
- Create: `spike/retrieval/__init__.py`
- Create: `spike/retrieval/corpus_loader.py`

- [ ] **Step 1: 创建 spike/retrieval/__init__.py**

```python
"""Spike 简易检索管线。"""
```

- [ ] **Step 2: 创建 spike/retrieval/corpus_loader.py**

```python
"""从 JSON 文件加载三类检索语料。"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SourceType = Literal["doc", "code", "sql"]


@dataclass
class Document:
    """单个可检索的文档片段。"""
    id: str
    source_type: SourceType
    content: str
    metadata: dict = field(default_factory=dict)


class CorpusLoader:
    """加载并管理 spike 本地语料。"""

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)

    def load_docs(self) -> list[Document]:
        """加载 Confluence 文档语料。"""
        return self._load_file("docs_sample.json", "doc")

    def load_code(self) -> list[Document]:
        """加载代码文件语料。"""
        return self._load_file("code_sample.json", "code")

    def load_sql_schema(self) -> list[Document]:
        """加载 SQL schema 语料。"""
        return self._load_file("sql_schema_sample.json", "sql")

    def load_all(self) -> dict[SourceType, list[Document]]:
        """加载全部三类语料。

        Returns:
            {"doc": [...], "code": [...], "sql": [...]}
        """
        return {
            "doc": self.load_docs(),
            "code": self.load_code(),
            "sql": self.load_sql_schema(),
        }

    def _load_file(self, filename: str, source_type: SourceType) -> list[Document]:
        filepath = self.corpus_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(
                f"语料文件不存在: {filepath}\n"
                f"请先将数据源导出为 JSON 放入 {self.corpus_dir}/"
            )
        with open(filepath, encoding="utf-8") as f:
            raw = json.load(f)

        documents = []
        for item in raw:
            doc = Document(
                id=item["id"],
                source_type=source_type,
                content=item.get("content", ""),
                metadata=item.get("metadata", {}),
            )
            documents.append(doc)
        return documents
```

- [ ] **Step 3: Commit**

```bash
git add spike/retrieval/__init__.py spike/retrieval/corpus_loader.py
git commit -m "feat(spike): add corpus loader"
```

---

### Task 3: BM25 + Embedding 索引构建器

**Files:**
- Create: `spike/retrieval/indexer.py`

- [ ] **Step 1: 创建 spike/retrieval/indexer.py**

```python
"""BM25 + embedding 索引构建。"""

import pickle
from pathlib import Path

import numpy as np
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from spike.retrieval.corpus_loader import Document


class HybridIndexer:
    """构建 BM25 和 embedding 双索引。"""

    def __init__(self, embedding_model_name: str = "all-MiniLM-L6-v2") -> None:
        self.embedding_model = SentenceTransformer(embedding_model_name)
        self.bm25: BM25Okapi | None = None
        self.embeddings: np.ndarray | None = None  # shape: (n_docs, dim)
        self.documents: list[Document] = []

    def index(self, documents: list[Document]) -> None:
        """对文档列表构建双索引。

        Args:
            documents: 待索引的文档列表
        """
        self.documents = documents
        contents = [doc.content for doc in documents]

        # BM25 索引
        tokenized = [content.split() for content in contents]
        self.bm25 = BM25Okapi(tokenized)

        # Embedding 索引
        self.embeddings = self.embedding_model.encode(
            contents, show_progress_bar=True, convert_to_numpy=True
        )

    def save(self, dir_path: str | Path) -> None:
        """持久化索引到磁盘。"""
        dir_path = Path(dir_path)
        dir_path.mkdir(parents=True, exist_ok=True)

        with open(dir_path / "bm25.pkl", "wb") as f:
            pickle.dump(self.bm25, f)

        np.save(dir_path / "embeddings.npy", self.embeddings)

        with open(dir_path / "documents.pkl", "wb") as f:
            pickle.dump(self.documents, f)

    def load(self, dir_path: str | Path) -> None:
        """从磁盘加载索引。"""
        dir_path = Path(dir_path)

        with open(dir_path / "bm25.pkl", "rb") as f:
            self.bm25 = pickle.load(f)

        self.embeddings = np.load(dir_path / "embeddings.npy")

        with open(dir_path / "documents.pkl", "rb") as f:
            self.documents = pickle.load(f)
```

- [ ] **Step 2: Commit**

```bash
git add spike/retrieval/indexer.py
git commit -m "feat(spike): add hybrid indexer (BM25 + embeddings)"
```

---

### Task 4: 混合检索器

**Files:**
- Create: `spike/retrieval/searcher.py`

- [ ] **Step 1: 创建 spike/retrieval/searcher.py**

```python
"""BM25 + embedding 混合检索。"""

import numpy as np
from sentence_transformers import SentenceTransformer

from spike.retrieval.corpus_loader import Document
from spike.retrieval.indexer import HybridIndexer


class HybridSearcher:
    """执行混合检索：加权融合 BM25 和 embedding 相似度分数。"""

    def __init__(
        self,
        indexer: HybridIndexer,
        embedding_model_name: str = "all-MiniLM-L6-v2",
        bm25_weight: float = 0.3,
        embedding_weight: float = 0.7,
    ) -> None:
        self.indexer = indexer
        self.embedding_model = SentenceTransformer(embedding_model_name)
        self.bm25_weight = bm25_weight
        self.embedding_weight = embedding_weight

    def search(self, query: str, top_k: int = 5) -> list[tuple[Document, float]]:
        """混合检索。

        Args:
            query: 查询文本
            top_k: 返回结果数量

        Returns:
            [(document, score), ...] 按分数降序排列
        """
        if self.indexer.bm25 is None or self.indexer.embeddings is None:
            raise RuntimeError("索引未构建，请先调用 indexer.index()")

        # BM25 分数
        tokenized_query = query.split()
        bm25_scores = np.array(self.indexer.bm25.get_scores(tokenized_query))

        # BM25 分数归一化
        bm25_max = bm25_scores.max()
        if bm25_max > 0:
            bm25_scores = bm25_scores / bm25_max

        # Embedding 相似度
        query_embedding = self.embedding_model.encode([query], convert_to_numpy=True)
        cosine_scores = np.dot(self.indexer.embeddings, query_embedding.T).flatten()

        # 混合分数
        combined_scores = (
            self.bm25_weight * bm25_scores + self.embedding_weight * cosine_scores
        )

        # 取 top_k
        top_indices = np.argsort(combined_scores)[::-1][:top_k]

        results = []
        for idx in top_indices:
            doc = self.indexer.documents[idx]
            score = float(combined_scores[idx])
            results.append((doc, score))

        return results
```

- [ ] **Step 2: Commit**

```bash
git add spike/retrieval/searcher.py
git commit -m "feat(spike): add hybrid searcher (BM25 + embedding fusion)"
```

---

### Task 5: 语料数据采集脚本

**Files:**
- Create: `spike/data/corpus/.gitkeep`

- [ ] **Step 1: 创建语料数据模板和采集说明**

创建 `spike/data/corpus/README.md`:

```markdown
# 语料数据目录

## 文件格式

### docs_sample.json
```json
[
  {
    "id": "doc_001:chunk_0",
    "content": "用户登录模块PRD v2.3 —— 新增OAuth 2.0支持...",
    "metadata": {
      "title": "用户登录模块PRD",
      "doc_id": "doc_001",
      "chunk_index": 0,
      "module": "用户登录",
      "req_ids": ["REQ-2024-0187"],
      "updated_at": "2024-06-15"
    }
  }
]
```

### code_sample.json
```json
[
  {
    "id": "src/auth/oauth.py",
    "content": "def authenticate_user(token: str) -> User: ...",
    "metadata": {
      "file_path": "src/auth/oauth.py",
      "language": "python",
      "module": "auth",
      "functions": ["authenticate_user", "refresh_token"],
      "imports": ["jwt", "requests"],
      "updated_at": "2024-06-10"
    }
  }
]
```

### sql_schema_sample.json
```json
[
  {
    "id": "table:users",
    "content": "CREATE TABLE users (id INT PRIMARY KEY, email VARCHAR(255), created_at TIMESTAMP); -- 用户表，存储所有注册用户信息",
    "metadata": {
      "table_name": "users",
      "columns": ["id", "email", "created_at"],
      "module": "user_management",
      "row_count": 150000
    }
  }
]
```

## 采集要求

- docs_sample.json: ~200 页 Confluence 文档，覆盖不同业务模块，每页拆为 chunks
- code_sample.json: ~300 个核心模块代码文件
- sql_schema_sample.json: 完整 table DDL + 字段注释

收集完成后替换此 README。
```

- [ ] **Step 2: Commit**

```bash
git add spike/data/corpus/README.md spike/data/corpus/.gitkeep
git commit -m "docs(spike): add corpus data format specification"
```

---

### Task 6: 多轮 Agent 循环模拟器

**Files:**
- Create: `spike/simulation/__init__.py`
- Create: `spike/simulation/multi_round_simulator.py`
- Create: `spike/simulation/round_config.yaml`

- [ ] **Step 1: 创建 spike/simulation/__init__.py**

```python
"""Agent 多轮循环模拟器。"""
```

- [ ] **Step 2: 创建 spike/simulation/round_config.yaml**

```yaml
# 多轮模拟器配置
doc:
  max_rounds: 3
  top_k_per_round: [5, 8, 10]
  injection_strategy: golden_random
  injection_ratio_per_round: [0.0, 0.6, 1.0]

code:
  max_rounds: 3
  top_k_per_round: [3, 5, 7]
  injection_strategy: golden_random
  injection_ratio_per_round: [0.0, 0.6, 1.0]

sql:
  max_rounds: 3
  top_k_per_round: [3, 5, 7]
  injection_strategy: golden_random
  injection_ratio_per_round: [0.0, 0.6, 1.0]
```

- [ ] **Step 3: 创建 spike/simulation/multi_round_simulator.py**

```python
"""Agent 多轮循环模拟器 —— 用规则驱动的方式模拟各 Agent 的多轮检索行为。

核心思路:
  第 1 轮: 用真实检索结果（体现真实分布）
  第 2+ 轮: 逐步从 golden 结果中注入未命中项（模拟 Agent 多轮改善）

不写真实 Agent 代码，不依赖 src/spma。
"""

import copy
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from spike.retrieval.corpus_loader import Document, SourceType
from spike.retrieval.searcher import HybridSearcher


@dataclass
class RoundResult:
    """单轮模拟结果。"""

    round_num: int
    results: list[dict]  # [{id, source_type, content, score}]
    coverage: float  # 本轮命中 golden 的比例
    expected_label: str  # "sufficient" | "insufficient"（由覆盖率规则计算，仅用于构造轮次）


@dataclass
class SimulationOutput:
    """完整的多轮模拟输出。"""

    query: dict
    source_type: SourceType
    rounds: list[RoundResult]


class MultiRoundSimulator:
    """模拟 Doc/Code/SQL Agent 的多轮检索行为。

    用法:
        simulator = MultiRoundSimulator(searcher, golden_results, config_path)
        output = simulator.simulate(query)
        # output.rounds 包含每一轮的检索结果
    """

    def __init__(
        self,
        searcher: HybridSearcher,
        golden_results: dict[str, list[str]],
        config_path: str | Path = "simulation/round_config.yaml",
        sufficiency_threshold: float = 0.8,
        random_seed: int = 42,
    ) -> None:
        """
        Args:
            searcher: 混合检索器
            golden_results: {"query_id": ["doc_001:chunk_0", "src/auth/oauth.py", ...]}
            config_path: 轮次配置路径
            sufficiency_threshold: 覆盖率阈值，≥此值视为 sufficient
            random_seed: 随机种子
        """
        self.searcher = searcher
        self.golden_results = golden_results
        self.sufficiency_threshold = sufficiency_threshold
        self.rng = random.Random(random_seed)

        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

    def simulate(self, query: dict) -> SimulationOutput:
        """对单条 query 执行多轮模拟。

        Args:
            query: {"id": "q001", "text": "...", "query_type": "cross_source", "source_types": ["doc", "code"]}

        Returns:
            SimulationOutput 含每轮检索结果
        """
        source_type = self._primary_source_type(query)
        agent_config = self.config[source_type]
        max_rounds = agent_config["max_rounds"]
        top_k_per_round = agent_config["top_k_per_round"]
        injection_ratios = agent_config["injection_ratio_per_round"]

        golden_ids = set(self.golden_results.get(query["id"], []))
        seen_ids: set[str] = set()
        rounds: list[RoundResult] = []

        for r in range(max_rounds):
            round_num = r + 1
            top_k = top_k_per_round[r]
            injection_ratio = injection_ratios[r]

            # 真实检索
            real_results = self._search(query["text"], source_type, top_k)

            # 记录已见 ID
            for item in real_results:
                seen_ids.add(item["id"])

            # 从 golden 中注入未命中项
            num_to_inject = int(len(golden_ids) * injection_ratio)
            still_missing = golden_ids - seen_ids
            num_to_inject = min(num_to_inject, len(still_missing))

            if num_to_inject > 0:
                injected = self.rng.sample(sorted(still_missing), num_to_inject)
                for gid in injected:
                    real_results.append(self._make_golden_result(gid))
                    seen_ids.add(gid)

            # 计算覆盖率
            coverage = len(seen_ids & golden_ids) / max(len(golden_ids), 1)
            expected_label = "sufficient" if coverage >= self.sufficiency_threshold else "insufficient"

            rounds.append(
                RoundResult(
                    round_num=round_num,
                    results=real_results,
                    coverage=coverage,
                    expected_label=expected_label,
                )
            )

        return SimulationOutput(query=query, source_type=source_type, rounds=rounds)

    def simulate_batch(self, queries: list[dict]) -> list[SimulationOutput]:
        """批量模拟。"""
        return [self.simulate(q) for q in queries]

    def to_judgment_points(
        self, outputs: list[SimulationOutput]
    ) -> list[dict]:
        """将模拟输出转换为评估用的判断点列表。

        Returns:
            [
              {
                "query_id": "q001",
                "query_text": "...",
                "query_type": "cross_source",
                "source_type": "doc",
                "round": 1,
                "results": [...],
                "expected_label": "insufficient",
              },
              ...
            ]
        """
        points = []
        for output in outputs:
            for rd in output.rounds:
                points.append(
                    {
                        "query_id": output.query["id"],
                        "query_text": output.query["text"],
                        "query_type": output.query.get("query_type", "unknown"),
                        "source_type": output.source_type,
                        "round": rd.round_num,
                        "results": rd.results,
                        "expected_label": rd.expected_label,
                    }
                )
        return points

    def _primary_source_type(self, query: dict) -> SourceType:
        """从 query 的 source_types 中确定主数据源类型。"""
        types = query.get("source_types", ["doc"])
        if len(types) == 1:
            return types[0]  # type: ignore[return-value]
        return "doc"

    def _search(
        self, query_text: str, source_type: SourceType, top_k: int
    ) -> list[dict]:
        """执行混合检索并序列化结果。"""
        docs = self.searcher.search(query_text, top_k=top_k)
        return [
            {
                "id": doc.id,
                "source_type": doc.source_type,
                "content": doc.content[:500],
                "score": round(score, 4),
            }
            for doc, score in docs
        ]

    def _make_golden_result(self, golden_id: str) -> dict:
        """为 golden ID 构造一个占位结果（模拟重搜命中）。
        
        实际标注时，这些由注入产生的条目会被替换为真实内容。
        """
        return {
            "id": golden_id,
            "source_type": "doc",
            "content": f"[GOLDEN INJECTED: {golden_id}]",
            "score": 1.0,
        }
```

- [ ] **Step 4: Commit**

```bash
git add spike/simulation/
git commit -m "feat(spike): add multi-round agent simulator"
```

---

### Task 7: Query 采集与审核

**Files:**
- Create: `spike/data/queries/.gitkeep`

- [ ] **Step 1: 创建 query 采集模板和分类矩阵说明**

创建 `spike/data/queries/README.md`:

```markdown
# Query 数据目录

## 文件说明

- `raw_queries_shadowing.txt` — Shadowing 观察采集（50条），直接从 PM/开发对话中记录，一行一条
- `raw_queries_historical.txt` — 历史日志提取（30条），从 Confluence/Git/DB 日志提取
- `raw_queries_synthetic.txt` — 人工构造边界 case（20条），按分类矩阵补齐

## 分类矩阵

|                | 短查询 (<15字)  | 中查询 (15-50字) | 长查询 (>50字) |
|----------------|----------------|------------------|----------------|
| 单源 Doc       | ≥1条           | ≥1条             | ≥1条           |
| 单源 Code      | ≥1条           | ≥1条             | ≥1条           |
| 单源 SQL       | ≥1条           | ≥1条             | ≥1条           |
| 跨源双源       | ≥1条           | ≥1条             | ≥1条           |
| 跨源三源       | ≥1条           | ≥1条             | ≥1条           |

## 边界 case 类型（必须覆盖）

- 精确 ID 查询: "REQ-2024-0187 相关的代码文件"
- 模糊自然语言: "退款咋做的"
- 时间范围: "上周改了哪些跟登录有关的代码"
- 否定/反事实: "有哪些模块没有对应的 PRD 文档"

## approved_100_queries.json 格式

```json
[
  {
    "id": "q001",
    "text": "用户登录模块的PRD改了哪些内容？影响了哪些代码文件和数据库表？",
    "query_type": "cross_source",
    "source_types": ["doc", "code", "sql"],
    "source": "shadowing",
    "collected_at": "2026-06-10",
    "notes": ""
  }
]
```
```

- [ ] **Step 2: 创建 approved_100_queries.json 的 schema 验证脚本**

不创建单独脚本，此验证逻辑在 Task 13 的评估脚本中作为 `--validate-data` 标志实现。

- [ ] **Step 3: Commit**

```bash
git add spike/data/queries/
git commit -m "docs(spike): add query collection specification"
```

---

### Task 8: 标注规范文档

**Files:**
- Create: `spike/data/annotation_rubric.md`

- [ ] **Step 1: 创建标注规范**

```markdown
# 标注规范：信息充足判定 Rubric

## 核心判定原则

> **"有了这些检索结果，一个熟悉企业业务的人能否回答用户的问题？"**

不要求"回答得完美"或"覆盖所有细节"，而是"能回答核心问题，不会因为关键信息缺失而答错或答不了"。

## Rubric

| 等级 | 标签 | 定义 |
|------|------|------|
| S3 | 完全充足 | 所有子问题都有直接覆盖，关键实体全部命中，结果之间有足够多样性（不重复） |
| S2 | 基本充足 | 核心问题能回答，但次要细节可能缺失或部分结果不够精确 |
| S1 | 勉强充足 | 核心问题能大致回答但信息有 gap，回答会不完整或需要推断 |
| I1 | 轻度不足 | 缺少某个关键实体或子问题的覆盖，直接回答会出错 |
| I2 | 明显不足 | 多个子问题无覆盖，或核心实体大面积缺失 |
| I3 | 完全不相关 | 检索结果跟用户问题基本无关 |

## 判定映射

- "够了" (sufficient): S3, S2, S1
- "不够" (insufficient): I1, I2, I3

S1（勉强充足）归为 sufficient — 宁可放行、不少搜。

## 标注流程

1. 阅读 query
2. 逐条检查检索结果是否与 query 的每个子问题相关
3. 判断核心问题能否被回答
4. 选择最匹配的 Rubric 等级
5. 映射为 sufficient / insufficient

## 标注格式

每个判断点追加 label 字段:

```json
{
  "query_id": "q001",
  "round": 1,
  "annotator_a": {
    "label": "I1",
    "sufficient": false,
    "notes": "缺少代码文件覆盖"
  }
}
```

## FAQ

Q: 用户问题有多个子问题（如"PRD改了啥 + 影响哪些代码文件"），只有一个子问题有覆盖，算够吗？
A: 不够。核心问题是复合的，缺少任一子问题的覆盖就无法完整回答。

Q: 检索到 5 条结果但都是同一文档的不同 chunk，算够吗？
A: 取决于是否覆盖了问题的所有方面。同一文档的多个 chunk 可能覆盖不同方面，也可能只是重复。按"能否回答核心问题"判断。

Q: 第 1 轮够了但第 2 轮不够（或反之）可能吗？
A: 每轮独立判断。不看前一轮结果，不看后一轮结果。只看当前轮。
```

- [ ] **Step 2: Commit**

```bash
git add spike/data/annotation_rubric.md
git commit -m "docs(spike): add annotation rubric"
```

---

### Task 9: 评估指标模块

**Files:**
- Create: `spike/eval/__init__.py`
- Create: `spike/eval/metrics.py`

- [ ] **Step 1: 创建 spike/eval/__init__.py**

```python
"""Spike 评估工具。"""
```

- [ ] **Step 2: 创建 spike/eval/metrics.py**

```python
"""评估指标计算：精确率、召回率、F1、特异度、Cohen's Kappa。"""

from typing import TypedDict


class ClassificationMetrics(TypedDict):
    precision: float
    recall: float
    f1: float
    specificity: float
    accuracy: float
    tp: int
    fp: int
    tn: int
    fn: int
    total: int


def compute_metrics(y_true: list[bool], y_pred: list[bool]) -> ClassificationMetrics:
    """计算分类指标。

    Args:
        y_true: 真实标签 (True=sufficient, False=insufficient)
        y_pred: LLM 预测标签 (True=sufficient, False=insufficient)

    Returns:
        ClassificationMetrics
    """
    if len(y_true) != len(y_pred):
        raise ValueError(f"长度不匹配: y_true={len(y_true)}, y_pred={len(y_pred)}")

    tp = sum(1 for t, p in zip(y_true, y_pred) if t and p)
    fp = sum(1 for t, p in zip(y_true, y_pred) if not t and p)
    tn = sum(1 for t, p in zip(y_true, y_pred) if not t and not p)
    fn = sum(1 for t, p in zip(y_true, y_pred) if t and not p)
    total = len(y_true)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
    specificity = tn / (tn + fp) if (tn + fp) > 0 else 0.0
    accuracy = (tp + tn) / total if total > 0 else 0.0

    return ClassificationMetrics(
        precision=round(precision, 4),
        recall=round(recall, 4),
        f1=round(f1, 4),
        specificity=round(specificity, 4),
        accuracy=round(accuracy, 4),
        tp=tp, fp=fp, tn=tn, fn=fn, total=total,
    )


def compute_fold_metrics(fold_results: list[ClassificationMetrics]) -> dict:
    """聚合多折指标，输出平均±标准差。

    Args:
        fold_results: 每折的指标

    Returns:
        {"precision": {"mean": 0.82, "std": 0.02}, ...}
    """
    import statistics

    keys = ["precision", "recall", "f1", "specificity", "accuracy"]
    aggregated = {}
    for key in keys:
        values = [r[key] for r in fold_results]
        aggregated[key] = {
            "mean": round(statistics.mean(values), 4),
            "std": round(statistics.stdev(values), 4) if len(values) > 1 else 0.0,
        }
    aggregated["total_judgment_points"] = sum(r["total"] for r in fold_results)
    return aggregated


def cohens_kappa(y_true: list[bool], y_pred: list[bool]) -> float:
    """计算 Cohen's Kappa。

    Kappa = (Po - Pe) / (1 - Pe)
    """
    if len(y_true) != len(y_pred):
        raise ValueError("长度不匹配")
    n = len(y_true)
    if n == 0:
        return 0.0

    # 观察一致率
    po = sum(1 for t, p in zip(y_true, y_pred) if t == p) / n

    # 随机一致率
    p_true = sum(y_true) / n
    p_pred = sum(y_pred) / n
    pe = p_true * p_pred + (1 - p_true) * (1 - p_pred)

    if pe == 1.0:
        return 1.0

    return round((po - pe) / (1 - pe), 4)
```

- [ ] **Step 3: Commit**

```bash
git add spike/eval/__init__.py spike/eval/metrics.py
git commit -m "feat(spike): add evaluation metrics (precision, recall, kappa)"
```

---

### Task 10: 5折交叉验证分割器

**Files:**
- Create: `spike/eval/fold_splitter.py`

- [ ] **Step 1: 创建 spike/eval/fold_splitter.py**

```python
"""5折交叉验证分割器。

按 query 级别分割（不是判断点级别），确保同一 query 的所有轮次在同一 fold 中。
"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from sklearn.model_selection import StratifiedKFold


class FoldSplitter:
    """将 golden 数据集按 query 做分层 5 折划分。"""

    def __init__(self, n_folds: int = 5, random_seed: int = 42) -> None:
        self.n_folds = n_folds
        self.random_seed = random_seed

    def split(self, golden_dataset_path: str | Path) -> list[dict[str, Any]]:
        """读取数据集并按 query 分层划分。

        Args:
            golden_dataset_path: golden_eval_dataset.json 路径

        Returns:
            [
              {
                "fold": 0,
                "train": [judgment_points...],
                "test": [judgment_points...],
              },
              ...
            ]
        """
        with open(golden_dataset_path, encoding="utf-8") as f:
            points = json.load(f)

        # 按 query_id 分组
        query_groups = defaultdict(list)
        for point in points:
            query_groups[point["query_id"]].append(point)

        query_ids = sorted(query_groups.keys())

        # 每 query 取第一条判断点的 label 作为分层标签
        y = [
            query_groups[qid][0]["golden_label"]
            for qid in query_ids
        ]

        skf = StratifiedKFold(
            n_splits=self.n_folds, shuffle=True, random_state=self.random_seed
        )

        folds = []
        for fold_idx, (train_qids_idx, test_qids_idx) in enumerate(skf.split(query_ids, y)):
            train_qids = {query_ids[i] for i in train_qids_idx}
            test_qids = {query_ids[i] for i in test_qids_idx}

            train_points = [p for p in points if p["query_id"] in train_qids]
            test_points = [p for p in points if p["query_id"] in test_qids]

            folds.append(
                {
                    "fold": fold_idx,
                    "train": train_points,
                    "test": test_points,
                    "train_query_count": len(train_qids),
                    "test_query_count": len(test_qids),
                }
            )

        return folds


def save_folds(folds: list[dict], output_path: str | Path) -> None:
    """保存折划分结果到 JSON 文件（用于可复现评估）。"""
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(folds, f, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Commit**

```bash
git add spike/eval/fold_splitter.py
git commit -m "feat(spike): add 5-fold CV splitter (query-level stratification)"
```

---

### Task 11: Prompt 模板

**Files:**
- Create: `spike/prompts/prompt_a_simple.md`
- Create: `spike/prompts/prompt_b_structured.md`
- Create: `spike/prompts/prompt_c_scoring.md`

- [ ] **Step 1: 创建 Prompt A — 简洁判断**

```markdown
# Prompt A: 简洁判断

你是一个检索质量评估器。判断以下检索结果是否足以回答用户问题。

用户问题: {query}

检索结果:
{results_json}

只需回答一个字: 够了 / 不够
```

- [ ] **Step 2: 创建 Prompt B — 结构化逐项检查**

```markdown
# Prompt B: 结构化逐项检查

你是一个检索质量评估器。判断当前检索结果是否足以回答用户的问题。

## 示例 1（够了）

用户问题: "用户登录模块 PRD 的最新版本"

检索结果:
1. [doc] 用户登录模块PRD v2.3（2024-06-15更新）——含完整需求描述
2. [doc] 用户登录模块变更记录 —— 含v2.1到v2.3的diff
3. [code] src/auth/login.py —— 对应用户登录的核心实现

逐项检查:
- 关键实体覆盖: 用户问了"登录模块PRD"，结果包含PRD文档和变更记录，实体覆盖完整
- 数量充足性: 文档≥3条，代码≥1条，充足
- 语义覆盖: 子问题"最新版本"已被v2.3文档覆盖

结论: sufficient

## 示例 2（不够）

用户问题: "退款流程的代码实现和涉及的数据库表"

检索结果:
1. [doc] 退款流程PRD文档
2. [doc] 支付模块架构设计

逐项检查:
- 关键实体覆盖: 缺少代码文件和数据库表，只有文档
- 数量充足性: 文档2条但缺代码和SQL表
- 语义覆盖: 子问题"代码实现"和"数据库表"无覆盖

结论: insufficient

## 示例 3（边缘 case）

用户问题: "近一周注册用户的转化率"

检索结果:
1. [sql] users 表结构（含created_at, status字段）
2. [sql] user_events 表结构（含event_type字段）
3. [doc] 用户转化率计算口径说明

逐项检查:
- 关键实体覆盖: users表、user_events表命中，但缺少具体SQL查询结果
- 数量充足性: schema信息充足，可据此写出查询
- 语义覆盖: 能回答核心问题（有了schema和计算口径），但缺少直接数据

结论: sufficient（勉强——schema+计算口径足够让SQL Agent生成查询）

---

现在请评估:

用户问题: {query}

检索结果:
{results_json}

请逐项检查:
1. 关键实体覆盖: 用户问到的需求ID、文件名、表名是否都检索到了？
2. 数量充足性: 返回的结果数量是否足够？
3. 语义覆盖: 用户问题的每个子问题是否都有对应的检索结果？

输出 JSON:
{{
  "entity_coverage": {{"covered": [...], "missing": [...]}},
  "count_sufficient": true/false,
  "semantic_coverage": "full" | "partial" | "insufficient",
  "verdict": "sufficient" | "insufficient",
  "confidence": 0.0-1.0,
  "missing_info": "如果 insufficient，说明缺少什么信息"
}}
```

- [ ] **Step 3: 创建 Prompt C — 多维度评分**

```markdown
# Prompt C: 多维度评分 + 阈值判定

你是一个检索质量评估器。对以下检索结果进行多维度评分。

用户问题: {query}

检索结果:
{results_json}

请对以下四个维度分别给出 0-1 的评分:

1. 实体覆盖度（权重 0.35）: 关键实体（需求ID、文件名、表名）的命中率
   - 1.0 = 所有关键实体全部命中
   - 0.5 = 部分关键实体命中
   - 0.0 = 关键实体均未命中

2. 数量充足度（权重 0.20）: 结果数量和多样性
   - 1.0 = 数量充足且结果多样（不重复）
   - 0.5 = 数量勉强够但多样性不足
   - 0.0 = 结果太少或严重重复

3. 语义覆盖度（权重 0.30）: 每个子问题是否至少有一条相关结果
   - 1.0 = 所有子问题均有覆盖
   - 0.5 = 核心子问题有覆盖但次要子问题缺失
   - 0.0 = 核心子问题无覆盖

4. 结果新鲜度（权重 0.15）: 时间相关查询的结果时效性
   - 1.0 = 结果均在时间范围内（或查询无时间要求）
   - 0.5 = 部分结果时效性不确定
   - 0.0 = 结果明显过时

计算加权总分: score = 0.35×实体 + 0.20×数量 + 0.30×语义 + 0.15×新鲜度

输出 JSON:
{{
  "entity_coverage_score": 0.0-1.0,
  "count_sufficiency_score": 0.0-1.0,
  "semantic_coverage_score": 0.0-1.0,
  "freshness_score": 0.0-1.0,
  "weighted_score": 0.0-1.0,
  "verdict": "sufficient" | "insufficient",
  "confidence": 0.0-1.0,
  "reasoning": "各维度评分依据的简要说明"
}}

判定规则: weighted_score >= 0.7 → sufficient
```

- [ ] **Step 4: Commit**

```bash
git add spike/prompts/prompt_a_simple.md spike/prompts/prompt_b_structured.md spike/prompts/prompt_c_scoring.md
git commit -m "feat(spike): add three prompt templates (simple, structured, scoring)"
```

---

### Task 12: LLM 完备度判断客户端

**Files:**
- Create: `spike/eval/llm_judge.py`

- [ ] **Step 1: 创建 spike/eval/llm_judge.py**

```python
"""调用 LLM API 做完备度判断。"""

import json
import re
from pathlib import Path
from typing import Any

import anthropic
import yaml


class LLMJudge:
    """LLM 完备度判断器 —— 加载 Prompt 模板，调用 Haiku API，返回判断结果。"""

    def __init__(self, config_path: str | Path = "config.yaml") -> None:
        with open(config_path, encoding="utf-8") as f:
            self.config = yaml.safe_load(f)

        api_config = self.config["api"]
        self.client = anthropic.Anthropic()
        self.model = api_config["model"]
        self.max_tokens = api_config["max_tokens"]
        self.temperature = api_config["temperature"]

    def load_prompt(self, version: str) -> str:
        """加载 Prompt 模板。

        Args:
            version: "A" | "B" | "C" | "final"

        Returns:
            Prompt 模板文本
        """
        filename_map = {
            "A": "prompt_a_simple.md",
            "B": "prompt_b_structured.md",
            "C": "prompt_c_scoring.md",
            "final": "prompt_final.md",
        }
        filename = filename_map.get(version)
        if filename is None:
            raise ValueError(f"未知 Prompt 版本: {version}，可选: A/B/C/final")

        prompt_path = Path(self.config["paths"]["prompts_dir"]) / filename
        if not prompt_path.exists():
            raise FileNotFoundError(f"Prompt 文件不存在: {prompt_path}")

        return prompt_path.read_text(encoding="utf-8")

    def judge(self, query_text: str, results: list[dict], prompt_version: str) -> dict:
        """调用 LLM 做完备度判断。

        Args:
            query_text: 用户问题
            results: 本轮检索结果列表
            prompt_version: Prompt 版本 "A" | "B" | "C" | "final"

        Returns:
            {
                "verdict": "sufficient" | "insufficient",
                "confidence": float,
                "raw_response": str,
                "parsed_json": dict | None,
            }
        """
        template = self.load_prompt(prompt_version)
        results_str = json.dumps(results, ensure_ascii=False, indent=2)
        prompt = template.replace("{query}", query_text).replace("{results_json}", results_str)

        message = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            messages=[{"role": "user", "content": prompt}],
        )

        raw_response = message.content[0].text

        if prompt_version == "A":
            return self._parse_simple(raw_response)
        else:
            return self._parse_structured(raw_response)

    def _parse_simple(self, response: str) -> dict:
        """解析 Prompt A 的简单回答。"""
        is_sufficient = "够" in response and "不够" not in response
        return {
            "verdict": "sufficient" if is_sufficient else "insufficient",
            "confidence": 0.5,
            "raw_response": response,
            "parsed_json": None,
        }

    def _parse_structured(self, response: str) -> dict:
        """解析 Prompt B/C 的 JSON 输出。"""
        parsed = self._extract_json(response)
        verdict = parsed.get("verdict", "insufficient") if parsed else "insufficient"
        confidence = parsed.get("confidence", 0.5) if parsed else 0.5
        return {
            "verdict": verdict,
            "confidence": confidence,
            "raw_response": response,
            "parsed_json": parsed,
        }

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """从 LLM 响应中提取 JSON 对象。"""
        match = re.search(r"\{[^{}]*\{[^{}]*\}[^{}]*\}|\{[^{}]*\}", text, re.DOTALL)
        if not match:
            # 尝试匹配 ```json ... ``` 包裹的块
            match = re.search(r"```json\s*([\s\S]*?)\s*```", text)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
            return None
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None

    def judge_batch(
        self, points: list[dict], prompt_version: str
    ) -> list[dict]:
        """批量判断。

        Args:
            points: 判断点列表
            prompt_version: Prompt 版本

        Returns:
            每个判断点附加 "llm_verdict", "llm_confidence", "llm_raw_response"
        """
        results = []
        for point in points:
            result = self.judge(
                point["query_text"], point["results"], prompt_version
            )
            point_copy = dict(point)
            point_copy["llm_verdict"] = result["verdict"]
            point_copy["llm_confidence"] = result["confidence"]
            point_copy["llm_raw_response"] = result["raw_response"]
            point_copy["llm_parsed"] = result.get("parsed_json")
            results.append(point_copy)
        return results
```

- [ ] **Step 2: Commit**

```bash
git add spike/eval/llm_judge.py
git commit -m "feat(spike): add LLM judge client (Haiku API)"
```

---

### Task 13: 主评估脚本

**Files:**
- Create: `spike/eval/convergence_eval.py`

- [ ] **Step 1: 创建 spike/eval/convergence_eval.py**

```python
"""主评估脚本 —— 跑 5 折 CV 评估 LLM 完备度判断精确率。

用法:
    # 消融实验: 对所有 Prompt 版本跑5折CV
    python -m spike.eval.convergence_eval --mode ablation

    # 最终评估: 只跑 final prompt
    python -m spike.eval.convergence_eval --mode final --prompt-version final

    # 验证数据完整性
    python -m spike.eval.convergence_eval --validate-data
"""

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from spike.eval.fold_splitter import FoldSplitter, save_folds
from spike.eval.llm_judge import LLMJudge
from spike.eval.metrics import ClassificationMetrics, compute_fold_metrics, compute_metrics


def load_config(config_path: str = "config.yaml") -> dict:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_data(golden_path: str) -> bool:
    """验证 golden 数据集的完整性。"""
    with open(golden_path, encoding="utf-8") as f:
        points = json.load(f)

    required_fields = [
        "query_id", "query_text", "query_type", "source_type",
        "round", "results", "golden_label",
    ]
    errors = []

    for i, point in enumerate(points):
        for field in required_fields:
            if field not in point:
                errors.append(f"判断点 #{i}: 缺少字段 '{field}'")

        label = point.get("golden_label")
        if label not in ("sufficient", "insufficient"):
            errors.append(f"判断点 #{i}: golden_label 值无效 '{label}'")

        if not isinstance(point.get("results"), list):
            errors.append(f"判断点 #{i}: results 必须是列表")

    if errors:
        print("❌ 数据验证失败:")
        for err in errors[:20]:
            print(f"  - {err}")
        if len(errors) > 20:
            print(f"  ... 及 {len(errors) - 20} 个其他错误")
        return False

    query_ids = set(p["query_id"] for p in points)
    print(f"✅ 数据验证通过: {len(points)} 个判断点, {len(query_ids)} 个 query")
    return True


def run_fold_evaluation(
    fold: dict, prompt_version: str, judge: LLMJudge
) -> ClassificationMetrics:
    """在单个 fold 的 test 集上跑评估。

    Args:
        fold: 5折划分中的一折
        prompt_version: Prompt 版本
        judge: LLM 判断器

    Returns:
        ClassificationMetrics
    """
    test_points = fold["test"]
    judged = judge.judge_batch(test_points, prompt_version)

    y_true = [p["golden_label"] == "sufficient" for p in judged]
    y_pred = [p["llm_verdict"] == "sufficient" for p in judged]

    metrics = compute_metrics(y_true, y_pred)

    # 附加 FP 详情
    fp_cases = []
    for p in judged:
        t = p["golden_label"] == "sufficient"
        p_hat = p["llm_verdict"] == "sufficient"
        if not t and p_hat:
            fp_cases.append(
                {
                    "query_id": p["query_id"],
                    "query_text": p["query_text"],
                    "round": p["round"],
                    "llm_raw_response": p.get("llm_raw_response", ""),
                }
            )

    return {
        **metrics,
        "fp_cases": fp_cases,
    }


def run_ablation(config: dict, prompt_versions: list[str]) -> dict:
    """消融实验：对所有 Prompt 版本跑 5 折 CV。"""
    golden_path = config["paths"]["golden_dataset"]
    n_folds = config["evaluation"]["folds"]
    seed = config["evaluation"]["random_seed"]

    splitter = FoldSplitter(n_folds=n_folds, random_seed=seed)
    folds = splitter.split(golden_path)
    save_folds(folds, "data/folds.json")

    judge = LLMJudge()

    all_results = {}
    for version in prompt_versions:
        print(f"\n{'='*60}")
        print(f"评估 Prompt {version}...")
        print(f"{'='*60}")

        fold_metrics = []
        for fold in folds:
            print(f"  Fold {fold['fold'] + 1}/{n_folds}...")
            metrics = run_fold_evaluation(fold, version, judge)
            fold_metrics.append(metrics)

        aggregated = compute_fold_metrics(fold_metrics)
        all_results[version] = {
            "fold_metrics": fold_metrics,
            "aggregated": aggregated,
        }

    return all_results


def main() -> None:
    parser = argparse.ArgumentParser(description="收敛判断 Spike 评估")
    parser.add_argument(
        "--mode",
        choices=["ablation", "final"],
        default="ablation",
        help="评估模式",
    )
    parser.add_argument(
        "--prompt-version",
        choices=["A", "B", "C", "final"],
        default="B",
        help="Prompt 版本（final 模式使用）",
    )
    parser.add_argument(
        "--validate-data",
        action="store_true",
        help="验证 golden 数据集完整性",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="配置文件路径",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.validate_data:
        valid = validate_data(config["paths"]["golden_dataset"])
        sys.exit(0 if valid else 1)

    if args.mode == "ablation":
        results = run_ablation(config, ["A", "B", "C"])
        output_path = config["paths"]["prompts_dir"] + "/ablation_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"\n消融结果已保存: {output_path}")

    elif args.mode == "final":
        splitter = FoldSplitter(
            n_folds=config["evaluation"]["folds"],
            random_seed=config["evaluation"]["random_seed"],
        )
        folds = splitter.split(config["paths"]["golden_dataset"])
        judge = LLMJudge()

        fold_metrics = []
        for fold in folds:
            print(f"Fold {fold['fold'] + 1}...")
            metrics = run_fold_evaluation(fold, args.prompt_version, judge)
            fold_metrics.append(metrics)

        aggregated = compute_fold_metrics(fold_metrics)
        print(f"\n最终评估结果:")
        print(f"  精确率: {aggregated['precision']['mean']:.4f} ± {aggregated['precision']['std']:.4f}")
        print(f"  召回率: {aggregated['recall']['mean']:.4f} ± {aggregated['recall']['std']:.4f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Commit**

```bash
git add spike/eval/convergence_eval.py
git commit -m "feat(spike): add main evaluation script (ablation + final modes)"
```

---

### Task 14: FP 案例分析器

**Files:**
- Create: `spike/eval/fp_analyzer.py`

- [ ] **Step 1: 创建 spike/eval/fp_analyzer.py**

```python
"""FP（假阳性）案例分析 —— 对"LLM说够了但实际不够"的案例分类和可修复性判断。"""

import json
from collections import Counter
from pathlib import Path
from typing import Any


FP_CATEGORIES = {
    "A": "实体遗漏型 — LLM 忽略了某个关键实体缺失",
    "B": "数量幻觉型 — LLM 被结果数量迷惑，不检查质量",
    "C": "语义理解错误 — LLM 误解了 query 或结果内容",
    "D": "跨源关联失败 — LLM 没发现跨源信息之间的缺口",
    "E": "其他 — 标注错误、极端 case 等",
}

REPAIRABLE = {"A", "B"}
STRUCTURAL = {"C", "D", "E"}


def analyze_fp_cases(
    judged_points: list[dict],
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """分析 FP 案例。

    Args:
        judged_points: 经 LLM 判断过的判断点列表（含 llm_verdict 和 golden_label）
        output_path: 可选的分析报告输出路径

    Returns:
        {
            "total_fp": int,
            "fp_rate": float,
            "category_distribution": {"A": 5, "B": 3, ...},
            "repairable_count": int,
            "structural_count": int,
            "repairable_ratio": float,
            "fp_cases": [...],
        }
    """
    fp_cases = []
    for p in judged_points:
        is_fp = (
            p["golden_label"] != "sufficient" and p.get("llm_verdict") == "sufficient"
        )
        if is_fp:
            fp_cases.append(p)

    total = len(judged_points)
    fp_count = len(fp_cases)
    fp_rate = fp_count / total if total > 0 else 0.0

    # 按 LLM 原始输出中的关键词做启发式分类
    categories = Counter()
    for case in fp_cases:
        raw = case.get("llm_raw_response", "").lower()
        if any(kw in raw for kw in ["实体", "entity", "missing entity"]):
            cat = "A"
        elif any(kw in raw for kw in ["数量", "count", "足够", "enough"]):
            cat = "B"
        elif any(kw in raw for kw in ["语义", "理解", "misunderstand"]):
            cat = "C"
        elif any(kw in raw for kw in ["跨源", "cross", "关联"]):
            cat = "D"
        else:
            cat = "E"
        categories[cat] += 1
        case["fp_category"] = cat

    repairable = sum(categories[c] for c in REPAIRABLE)
    structural = sum(categories[c] for c in STRUCTURAL)
    repairable_ratio = repairable / fp_count if fp_count > 0 else 0.0

    result = {
        "total_fp": fp_count,
        "fp_rate": round(fp_rate, 4),
        "category_distribution": dict(categories),
        "category_explanations": {k: FP_CATEGORIES[k] for k in categories},
        "repairable_count": repairable,
        "structural_count": structural,
        "repairable_ratio": round(repairable_ratio, 4),
        "fp_cases": [
            {
                "query_id": c["query_id"],
                "query_text": c.get("query_text", ""),
                "round": c.get("round", "?"),
                "category": c.get("fp_category", "E"),
                "llm_response_excerpt": c.get("llm_raw_response", "")[:300],
            }
            for c in fp_cases
        ],
    }

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result


def print_fp_summary(analysis: dict) -> None:
    """打印 FP 分析摘要。"""
    print(f"\n{'='*50}")
    print("FP 案例分析摘要")
    print(f"{'='*50}")
    print(f"总 FP 数: {analysis['total_fp']}")
    print(f"FP 率: {analysis['fp_rate']:.2%}")
    print(f"\n分类分布:")
    for cat, count in analysis["category_distribution"].items():
        desc = FP_CATEGORIES.get(cat, "未知")
        print(f"  {cat} ({desc}): {count}")
    print(f"\n可修复 (A+B): {analysis['repairable_count']}")
    print(f"结构性 (C+D+E): {analysis['structural_count']}")
    print(f"可修复比例: {analysis['repairable_ratio']:.2%}")
    if analysis["repairable_ratio"] >= 0.6:
        print("✅ 大部分 FP 可通过 Prompt 改进修复")
    else:
        print("⚠️  超过40%的FP是结构性的 → Plan B 论据")
```

- [ ] **Step 2: Commit**

```bash
git add spike/eval/fp_analyzer.py
git commit -m "feat(spike): add FP case analyzer with repairability classification"
```

---

### Task 15: 分层分析 & 报告生成器

**Files:**
- Create: `spike/eval/report_generator.py`

- [ ] **Step 1: 创建 spike/eval/report_generator.py**

```python
"""分层分析 & 评估报告生成。"""

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from spike.eval.metrics import compute_metrics


def stratified_analysis(judged_points: list[dict]) -> dict[str, Any]:
    """按多个维度做分层分析。

    Returns:
        {
            "by_query_type": {"single_doc": {metrics}, ...},
            "by_round": {"1": {metrics}, ...},
            "by_query_length": {"short": {metrics}, ...},
            "by_result_count": {"few": {metrics}, ...},
        }
    """
    return {
        "by_query_type": _analyze_by(judged_points, _get_query_type),
        "by_round": _analyze_by(judged_points, lambda p: str(p.get("round", "?"))),
        "by_query_length": _analyze_by(judged_points, _get_query_length),
        "by_result_count": _analyze_by(judged_points, _get_result_count),
    }


def _analyze_by(
    points: list[dict], key_fn: callable
) -> dict[str, dict]:
    """按 key_fn 分组计算指标。"""
    groups = defaultdict(list)
    for p in points:
        key = key_fn(p)
        groups[key].append(p)

    result = {}
    for key, group in sorted(groups.items()):
        y_true = [p["golden_label"] == "sufficient" for p in group]
        y_pred = [p.get("llm_verdict") == "sufficient" for p in group]
        metrics = compute_metrics(y_true, y_pred)
        result[key] = {**metrics, "sample_count": len(group)}
    return result


def _get_query_type(point: dict) -> str:
    return point.get("query_type", "unknown")


def _get_query_length(point: dict) -> str:
    text = point.get("query_text", "")
    length = len(text)
    if length < 15:
        return "short"
    elif length <= 50:
        return "medium"
    else:
        return "long"


def _get_result_count(point: dict) -> str:
    count = len(point.get("results", []))
    if count <= 2:
        return "1-2"
    elif count <= 5:
        return "3-5"
    elif count <= 10:
        return "6-10"
    else:
        return ">10"


def generate_report(
    fold_metrics: list[dict],
    stratified: dict,
    fp_analysis: dict,
    prompt_version: str,
    output_path: str | Path,
) -> None:
    """生成 Markdown 评估报告。"""
    from spike.eval.metrics import compute_fold_metrics

    aggregated = compute_fold_metrics(fold_metrics)

    lines = [
        f"# Phase 0 收敛判断 Spike — 评估报告",
        "",
        f"> 生成时间: 自动生成 | Prompt 版本: {prompt_version}",
        "",
        "---",
        "",
        "## 1. 总体指标",
        "",
        "| 指标 | 均值 | 标准差 |",
        "|------|------|--------|",
        f"| 精确率 | {aggregated['precision']['mean']:.4f} | ±{aggregated['precision']['std']:.4f} |",
        f"| 召回率 | {aggregated['recall']['mean']:.4f} | ±{aggregated['recall']['std']:.4f} |",
        f"| F1 | {aggregated['f1']['mean']:.4f} | ±{aggregated['f1']['std']:.4f} |",
        f"| 特异度 | {aggregated['specificity']['mean']:.4f} | ±{aggregated['specificity']['std']:.4f} |",
        f"| 判断点总数 | {aggregated['total_judgment_points']} | — |",
        "",
        "### Go/No-Go 判定",
        "",
    ]

    precision = aggregated["precision"]["mean"]
    if precision >= 0.85:
        lines.append(f"✅ **Go** — 精确率 {precision:.2%} ≥ 85%，强烈信心进入 Phase 1")
    elif precision >= 0.80:
        lines.append(f"✅ **Go (有条件)** — 精确率 {precision:.2%} ∈ [80%, 85%)，Agent 循环中增加确定性收敛权重")
    elif precision >= 0.70:
        lines.append(f"⚠️ **Risk** — 精确率 {precision:.2%} ∈ [70%, 80%)，需评审 FP 案例可修复性")
    else:
        lines.append(f"❌ **No-Go** — 精确率 {precision:.2%} < 70%，启用 Plan B")

    lines.extend([
        "",
        "---",
        "",
        "## 2. 各折详情",
        "",
        "| Fold | 精确率 | 召回率 | F1 | 判断点数 |",
        "|------|--------|--------|----|---------|",
    ])

    for i, fm in enumerate(fold_metrics):
        lines.append(
            f"| {i+1} | {fm['precision']:.4f} | {fm['recall']:.4f} | {fm['f1']:.4f} | {fm['total']} |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 3. 分层分析",
        "",
    ])

    for dim_name, dim_data in stratified.items():
        lines.append(f"### {dim_name}")
        lines.append("")
        lines.append("| 分组 | 精确率 | 召回率 | F1 | 样本数 |")
        lines.append("|------|--------|--------|----|--------|")
        for key, metrics in dim_data.items():
            lines.append(
                f"| {key} | {metrics['precision']:.4f} | {metrics['recall']:.4f} | {metrics['f1']:.4f} | {metrics['sample_count']} |"
            )
        lines.append("")

    lines.extend([
        "---",
        "",
        "## 4. FP 案例分析",
        "",
        f"- 总 FP 数: {fp_analysis['total_fp']}",
        f"- FP 率: {fp_analysis['fp_rate']:.2%}",
        f"- 可修复比例: {fp_analysis['repairable_ratio']:.2%}",
        "",
        "### FP 分类分布",
        "",
        "| 类别 | 描述 | 数量 |",
        "|------|------|------|",
    ])

    for cat, count in fp_analysis.get("category_distribution", {}).items():
        desc = {
            "A": "实体遗漏型", "B": "数量幻觉型",
            "C": "语义理解错误", "D": "跨源关联失败", "E": "其他",
        }.get(cat, "未知")
        lines.append(f"| {cat} | {desc} | {count} |")

    lines.extend([
        "",
        "### 是否可通过 Prompt 修复？",
        "",
        f"可修复率 {fp_analysis['repairable_ratio']:.2%} → "
        f"{'建议改进Prompt后重新评估' if fp_analysis['repairable_ratio'] >= 0.6 else '存在结构性缺陷，需考虑Plan B'}",
    ])

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
```

- [ ] **Step 2: Commit**

```bash
git add spike/eval/report_generator.py
git commit -m "feat(spike): add stratified analysis and report generator"
```

---

### Task 16: IAA 标注一致率计算

**Files:**
- Create: `spike/annotation/__init__.py`
- Create: `spike/annotation/iaa.py`

- [ ] **Step 1: 创建 spike/annotation/__init__.py**

```python
"""标注工具 & IAA 计算。"""
```

- [ ] **Step 2: 创建 spike/annotation/iaa.py**

```python
"""标注一致率 (IAA) 计算 —— Cohen's Kappa。"""

import json
from pathlib import Path

from spike.eval.metrics import cohens_kappa


def compute_iaa(annotations_path: str | Path) -> dict:
    """计算双人标注的 Cohen's Kappa。

    Args:
        annotations_path: 标注文件路径 (JSON)
            格式: [
              {
                "query_id": "q001",
                "round": 1,
                "annotator_a": {"label": "S2", "sufficient": true},
                "annotator_b": {"label": "S1", "sufficient": true},
              },
              ...
            ]

    Returns:
        {
            "kappa": float,
            "total_points": int,
            "agreements": int,
            "disagreements": int,
            "agreement_rate": float,
            "disagreement_cases": [...],
        }
    """
    with open(annotations_path, encoding="utf-8") as f:
        annotations = json.load(f)

    y_a = []
    y_b = []
    agreements = 0
    disagreements = 0
    disagreement_cases = []

    for ann in annotations:
        suff_a = ann["annotator_a"]["sufficient"]
        suff_b = ann["annotator_b"]["sufficient"]
        y_a.append(suff_a)
        y_b.append(suff_b)

        if suff_a == suff_b:
            agreements += 1
        else:
            disagreements += 1
            disagreement_cases.append(
                {
                    "query_id": ann["query_id"],
                    "round": ann["round"],
                    "annotator_a": ann["annotator_a"]["label"],
                    "annotator_b": ann["annotator_b"]["label"],
                }
            )

    kappa = cohens_kappa(y_a, y_b)
    total = len(annotations)
    agreement_rate = agreements / total if total > 0 else 0.0

    return {
        "kappa": kappa,
        "total_points": total,
        "agreements": agreements,
        "disagreements": disagreements,
        "agreement_rate": round(agreement_rate, 4),
        "disagreement_cases": disagreement_cases,
    }


def print_iaa_report(result: dict) -> None:
    """打印 IAA 报告。"""
    print(f"\n{'='*50}")
    print("标注一致率 (IAA) 报告")
    print(f"{'='*50}")
    print(f"Cohen's Kappa: {result['kappa']:.4f}")
    print(f"一致率: {result['agreement_rate']:.2%}")
    print(f"一致: {result['agreements']} | 分歧: {result['disagreements']} | 总计: {result['total_points']}")

    if result["kappa"] >= 0.8:
        print("✅ IAA 达标 (κ ≥ 0.8)")
    elif result["kappa"] >= 0.6:
        print("⚠️  IAA 可接受但需改进 Rubric (0.6 ≤ κ < 0.8)")
    else:
        print("❌ IAA 不达标 (κ < 0.6)，需重新设计 Rubric")

    if result["disagreement_cases"]:
        print(f"\n分歧案例 (前 5 条):")
        for case in result["disagreement_cases"][:5]:
            print(f"  - {case['query_id']} R{case['round']}: A={case['annotator_a']}, B={case['annotator_b']}")
```

- [ ] **Step 3: Commit**

```bash
git add spike/annotation/
git commit -m "feat(spike): add IAA calculator (Cohen's Kappa)"
```

---

### Task 17: Plan B 设计文档

**Files:**
- Create: `spike/reports/plan_b_design.md`

- [ ] **Step 1: 创建 Plan B 设计文档模板**

```markdown
# Plan B: 纯确定性收敛 + 轮次上限

> 如果 LLM 完备度判断精确率不达标时启用。

## 一、核心变更

放弃 LLM 完备度判断，所有 Agent 改用纯确定性收敛条件。

## 二、收敛规则

| Agent | 收敛条件 | 最大轮数 | 超时 |
|-------|---------|---------|------|
| Doc Agent | 结果 ≥ 5 条 AND req_ids 精确匹配 | ≤ 3 | 2s |
| Code Agent | 结果 ≥ 3 条 AND (调用链深度 ≤ 2 层 OR 第 3 轮无新增文件) | ≤ 3 | 2s |
| SQL Agent | SQL 执行成功 AND 行数 ∈ [1, 10000] | ≤ 5 | 3s |
| Synthesis | 引用覆盖率 ≥ 80% claim AND 无跨源矛盾 | ≤ 2 | 2s |

## 三、移除的组件

- LLM 完备度判断调用（节省 ~$0.001/次 × 每 query 平均 3 轮 × 3 Agent ≈ $0.01/query）
- Agent 的"自主判断"能力 —— 不再由 LLM 决定是否继续搜索

## 四、对用户体验的影响

- **正面**: 响应延迟更可预测（LLM 判断调用免除），成本更低
- **负面**: 
  - 边缘 case 可能需要用户手动追加查询（Agent 不知道自己"还不够"）
  - 可能在明显不够的情况下仍然强制收敛（确定性条件满足但不充分）
  - 对模糊 query 的鲁棒性下降

## 五、迁移影响

| Agent | 代码变更 | 测试变更 |
|-------|---------|---------|
| Doc | 删除 `llm_judge` 调用，仅保留 `result_count ≥ 5 AND req_id_match` | 去掉 LLM mock，纯确定性测试 |
| Code | 同上，保留 `result_count ≥ 3 AND (depth ≤ 2 OR no_new_files)` | 同上 |
| SQL | 删除语义验证中的 LLM 调用 | 简化测试 |
| Synthesis | 删除 LLM 完备度自检 | 简化自检为纯规则 |

## 六、性能与成本对比

| 指标 | Plan A (LLM判断) | Plan B (纯确定性) | 差异 |
|------|------------------|-------------------|------|
| 平均响应时间 | ~2.5s | ~2.0s | -20% |
| LLM API 成本/query | ~$0.01 | $0 | -100% |
| 精确率 | 目标 ≥ 80% | 100%（确定性规则）| — |
| 召回率 | 较高（LLM 识别不够） | 较低（无法识别边缘不够） | 召回率下降 |
| 用户体验 | 更智能的收敛 | 更可预测但有时过早收敛 | — |

## 七、决策建议

待最终评估报告出来后填写具体建议。
```

- [ ] **Step 2: Commit**

```bash
git add spike/reports/plan_b_design.md
git commit -m "docs(spike): add Plan B design (deterministic convergence fallback)"
```

---

### Task 18: 集成测试 — 端到端验证管线可跑

**Files:**
- Create: `spike/tests/__init__.py`
- Create: `spike/tests/test_e2e.py`

- [ ] **Step 1: 创建 spike/tests/__init__.py**

```python
"""Spike 测试。"""
```

- [ ] **Step 2: 创建 spike/tests/test_e2e.py**

```python
"""端到端集成测试 —— 用最小 mock 数据验证管线可跑。"""

import json
import tempfile
from pathlib import Path

import pytest

from spike.eval.convergence_eval import validate_data
from spike.eval.fold_splitter import FoldSplitter
from spike.eval.metrics import cohens_kappa, compute_metrics


# 最小 mock 数据
MOCK_JUDGMENT_POINTS = [
    {
        "query_id": "q001",
        "query_text": "测试问题1",
        "query_type": "single_doc",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_001:chunk_0", "content": "测试内容"}],
        "golden_label": "sufficient",
        "llm_verdict": "sufficient",
        "llm_confidence": 0.9,
        "llm_raw_response": "够了",
    },
    {
        "query_id": "q001",
        "query_text": "测试问题1",
        "query_type": "single_doc",
        "source_type": "doc",
        "round": 2,
        "results": [{"id": "doc_001:chunk_0", "content": "测试内容"}, {"id": "doc_001:chunk_1", "content": "更多"}],
        "golden_label": "sufficient",
        "llm_verdict": "sufficient",
        "llm_confidence": 0.95,
        "llm_raw_response": "够了",
    },
    {
        "query_id": "q002",
        "query_text": "测试问题2",
        "query_type": "cross_source",
        "source_type": "doc",
        "round": 1,
        "results": [{"id": "doc_002:chunk_0", "content": "部分内容"}],
        "golden_label": "insufficient",
        "llm_verdict": "insufficient",
        "llm_confidence": 0.8,
        "llm_raw_response": "不够",
    },
    {
        "query_id": "q002",
        "query_text": "测试问题2",
        "query_type": "cross_source",
        "source_type": "doc",
        "round": 2,
        "results": [
            {"id": "doc_002:chunk_0", "content": "部分内容"},
            {"id": "doc_002:chunk_1", "content": "补充内容"},
        ],
        "golden_label": "insufficient",
        "llm_verdict": "sufficient",  # FP!
        "llm_confidence": 0.6,
        "llm_raw_response": "关键实体覆盖完整，数量充足",
    },
]


class TestMetrics:
    """评估指标单元测试。"""

    def test_perfect_precision(self):
        y_true = [True, True, False, False]
        y_pred = [True, True, False, False]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 1.0
        assert metrics["recall"] == 1.0
        assert metrics["tp"] == 2
        assert metrics["fp"] == 0

    def test_one_false_positive(self):
        y_true = [True, False, False]
        y_pred = [True, True, False]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 0.5  # 1 TP, 1 FP
        assert metrics["fp"] == 1

    def test_zero_division(self):
        y_true = [False, False]
        y_pred = [True, True]
        metrics = compute_metrics(y_true, y_pred)
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0


class TestKappa:
    """Cohen's Kappa 测试。"""

    def test_perfect_agreement(self):
        y_a = [True, True, False, False]
        y_b = [True, True, False, False]
        kappa = cohens_kappa(y_a, y_b)
        assert kappa == 1.0

    def test_chance_agreement(self):
        # 50% positive, random agreement
        y_a = [True, False, True, False]
        y_b = [True, False, False, True]
        kappa = cohens_kappa(y_a, y_b)
        assert -1.0 <= kappa <= 1.0

    def test_complete_disagreement(self):
        y_a = [True, True]
        y_b = [False, False]
        kappa = cohens_kappa(y_a, y_b)
        assert kappa <= 0.0


class TestFoldSplitter:
    """5折 CV 分割器测试。"""

    def test_query_level_split(self):
        """验证同一 query 的所有判断点在同一 fold 中。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(MOCK_JUDGMENT_POINTS, f)
            tmp_path = f.name

        try:
            splitter = FoldSplitter(n_folds=2, random_seed=42)
            folds = splitter.split(tmp_path)

            assert len(folds) == 2

            for fold in folds:
                # 每个 test set 中的 query_id 不应出现在 train set 中
                test_qids = {p["query_id"] for p in fold["test"]}
                train_qids = {p["query_id"] for p in fold["train"]}
                assert test_qids.isdisjoint(train_qids), "test 和 train 的 query 有重叠"
        finally:
            Path(tmp_path).unlink()


class TestValidateData:
    """数据验证测试。"""

    def test_valid_data(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(MOCK_JUDGMENT_POINTS, f)
            tmp_path = f.name

        try:
            assert validate_data(tmp_path) is True
        finally:
            Path(tmp_path).unlink()

    def test_missing_field(self):
        bad_data = [{"query_id": "q001"}]  # 缺少多个字段
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(bad_data, f)
            tmp_path = f.name

        try:
            assert validate_data(tmp_path) is False
        finally:
            Path(tmp_path).unlink()
```

- [ ] **Step 3: 运行测试**

```bash
cd spike && uv run pytest tests/ -v
```
Expected: 7 tests PASS

- [ ] **Step 4: Commit**

```bash
git add spike/tests/
git commit -m "test(spike): add E2E integration tests for eval pipeline"
```

---

### Task 19: 最终 README & 使用指南

**Files:**
- Modify: `spike/README.md`

- [ ] **Step 1: 更新 spike/README.md 为完整使用指南**

```markdown
# SPMA Phase 0: Convergence Judgment Spike

验证 LLM 能否可靠判断检索结果"够了"。

## 环境准备

```bash
cd spike
uv sync
```

配置 `config.yaml` 中的 Anthropic API key（通过环境变量 `ANTHROPIC_API_KEY`）。

## 数据准备

### 1. 语料收集

将三类数据源导出为 JSON 放入 `data/corpus/`:
- `docs_sample.json` — ~200页 Confluence 文档 chunks
- `code_sample.json` — ~300个代码文件
- `sql_schema_sample.json` — 完整 table DDL

### 2. Query 收集

将 query 放入 `data/queries/`:
- `raw_queries_shadowing.txt` — 50条
- `raw_queries_historical.txt` — 30条
- `raw_queries_synthetic.txt` — 20条

审核后整理为 `data/queries/approved_100_queries.json`。

### 3. 构建索引

```bash
cd spike
python -c "
from retrieval.corpus_loader import CorpusLoader
from retrieval.indexer import HybridIndexer

loader = CorpusLoader('data/corpus')
all_docs = []
for docs in loader.load_all().values():
    all_docs.extend(docs)

indexer = HybridIndexer()
indexer.index(all_docs)
indexer.save('data/index')
print(f'索引构建完成: {len(all_docs)} 个文档')
"
```

### 4. 生成判断点 & 标注

```bash
# 运行多轮模拟器生成判断点
python -c "
import json
from simulation.multi_round_simulator import MultiRoundSimulator
from retrieval.corpus_loader import CorpusLoader
from retrieval.indexer import HybridIndexer
from retrieval.searcher import HybridSearcher

# 加载索引
indexer = HybridIndexer()
indexer.load('data/index')
searcher = HybridSearcher(indexer)

# 加载 queries 和 golden results
with open('data/queries/approved_100_queries.json') as f:
    queries = json.load(f)
with open('data/golden_eval_dataset.json') as f:
    golden = json.load(f)

# 构建 golden 映射
golden_map = {}
for item in golden:
    qid = item['query_id']
    if qid not in golden_map:
        golden_map[qid] = []
    golden_map[qid].extend(item.get('golden_doc_ids', []))
    golden_map[qid].extend(item.get('golden_code_ids', []))
    golden_map[qid].extend(item.get('golden_sql_ids', []))

# 模拟
simulator = MultiRoundSimulator(searcher, golden_map)
outputs = simulator.simulate_batch(queries)
points = simulator.to_judgment_points(outputs)

with open('data/judgment_points_stage1.json', 'w') as f:
    json.dump(points, f, ensure_ascii=False, indent=2)

print(f'生成 {len(points)} 个判断点，待标注')
"
```

然后进行双人标注，结果写入 `data/golden_eval_dataset.json`。

### 5. IAA 校验

```bash
python -c "
from annotation.iaa import compute_iaa, print_iaa_report
result = compute_iaa('data/golden_eval_dataset.json')
print_iaa_report(result)
"
```

### 6. 消融实验

```bash
python -m spike.eval.convergence_eval --mode ablation
```

### 7. FP 分析 & 改进 Prompt

基于消融结果改进 Prompt 后，保存为 `prompts/prompt_final.md`。

### 8. 最终评估

```bash
python -m spike.eval.convergence_eval --mode final --prompt-version final
```

## 目录结构

```
spike/
├── config.yaml              # 配置文件
├── pyproject.toml           # 依赖
├── README.md                # 本文件
├── data/
│   ├── corpus/              # 检索语料 (JSON)
│   ├── queries/             # 用户查询
│   ├── golden_eval_dataset.json  # 标注后的数据集
│   └── annotation_rubric.md      # 标注规范
├── retrieval/               # 检索管线
├── simulation/              # 多轮模拟器
├── prompts/                 # Prompt 模板
├── eval/                    # 评估脚本
├── annotation/              # 标注工具
├── tests/                   # 测试
└── reports/                 # 评估报告
```

## 设计文档

[Phase 0 收敛判断 Spike 设计](../../docs/superpowers/specs/2026-06-07-phase0-convergence-spike-design.md)
```

- [ ] **Step 2: Commit**

```bash
git add spike/README.md
git commit -m "docs(spike): add complete usage guide to README"
```

---

## 自审清单

### 1. Spec 覆盖检查

| Spec 章节 | 对应 Task | 覆盖? |
|-----------|----------|-------|
| 二、整体架构 & 数据流 | Task 1, 13 | ✅ |
| 三、标注规范 | Task 8 | ✅ |
| 四、检索模拟 & 多轮模拟器 | Task 2, 3, 4, 5, 6 | ✅ |
| 五、Prompt 工程实验设计 | Task 11, 12, 13 | ✅ |
| 六、评估方法论 & 统计分析 | Task 9, 10, 14, 15 | ✅ |
| 七、数据采集计划 | Task 5, 7 | ✅ |
| 八、项目结构 | Task 1 (全部文件) | ✅ |
| 十一、风险与缓解 | Task 17 (Plan B) | ✅ |

### 2. Placeholder 检查

- 无 TBD、TODO、占位符
- 所有步骤含实际代码或具体内容
- 测试文件含完整测试代码

### 3. 类型一致性检查

- `Document` dataclass 在 Task 2 定义，Task 3/4/6 引用 ✅
- `ClassificationMetrics` TypedDict 在 Task 9 定义，Task 13/15 引用 ✅
- `FoldSplitter` 在 Task 10 定义，Task 13 引用 ✅
- `golden_label` 字段名贯穿所有评估代码 ✅
- `query_id`, `query_text`, `query_type` 字段名一致 ✅

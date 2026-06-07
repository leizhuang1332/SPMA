"""Agent 多轮循环模拟器 —— 用规则驱动的方式模拟各 Agent 的多轮检索行为。

核心思路:
  第 1 轮: 用真实检索结果（体现真实分布）
  第 2+ 轮: 逐步从 golden 结果中注入未命中项（模拟 Agent 多轮改善）

不写真实 Agent 代码，不依赖 src/spma。
"""

import random
from dataclasses import dataclass
from pathlib import Path

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

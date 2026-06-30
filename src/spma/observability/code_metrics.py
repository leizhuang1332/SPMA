"""Code Agent Prometheus 指标（design-13 §7.1）。

16 个指标，命名与项目已有的 qr_* 指标保持一致。
"""
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


COUNTER_ROUTE_TOTAL = "code_route_total"
COUNTER_ROUTE_CONFIDENCE = "code_route_confidence"
COUNTER_ROUTE_FALLBACK = "code_route_fallback_total"
COUNTER_ROUTE_ACCURACY = "code_route_accuracy_sample"
COUNTER_EXPLORER_REFINE_ERRORS = "code_explorer_refine_errors_total"
COUNTER_SEARCHER_TIMEOUT = "code_searcher_timeout_total"
COUNTER_SEARCHER_FAIL = "code_searcher_fail_total"
COUNTER_REPO_REGISTRY_FALLBACK = "code_repo_registry_fallback_total"
COUNTER_REPO_REGISTRY_ADMIN_OPS = "code_repo_registry_admin_ops_total"
HISTOGRAM_ROUTE_LLM_LATENCY = "code_route_llm_latency_seconds"
HISTOGRAM_ROUTE_TOTAL_LATENCY = "code_route_total_latency_seconds"
HISTOGRAM_EXPLORE_ROUNDS = "code_explore_rounds"
HISTOGRAM_REPO_REGISTRY_QUERY = "code_repo_registry_query_seconds"
HISTOGRAM_ROUTE_TWO_STAGE_SECONDS = "code_route_two_stage_seconds"
HISTOGRAM_ROUTE_TWO_STAGE_RESULTS = "code_route_two_stage_results"
GAUGE_REPO_REGISTRY_COUNT = "code_repo_registry_count"


@dataclass
class CodeMetrics:
    registry: CollectorRegistry
    route_total: Counter
    route_confidence: Counter
    route_fallback: Counter
    route_accuracy: Counter
    explore_rounds: Histogram
    explorer_refine_errors: Counter
    searcher_timeout: Counter
    searcher_fail: Counter
    repo_registry_query: Histogram
    repo_registry_admin_ops: Counter
    repo_registry_fallback: Counter
    repo_registry_count: Gauge
    route_llm_latency: Histogram
    route_total_latency: Histogram
    route_two_stage_seconds: Histogram
    route_two_stage_results: Histogram


def build_code_metrics() -> CodeMetrics:
    """每次调用返回独立 CollectorRegistry（便于多实例/多测试）。"""
    registry = CollectorRegistry()
    return CodeMetrics(
        registry=registry,
        route_total=Counter(
            COUNTER_ROUTE_TOTAL, "Code route hits by route_method",
            labelnames=("route_method",), registry=registry,
        ),
        route_confidence=Counter(
            COUNTER_ROUTE_CONFIDENCE, "Code route hits by confidence",
            labelnames=("confidence",), registry=registry,
        ),
        route_fallback=Counter(
            COUNTER_ROUTE_FALLBACK, "Code route fallback hits",
            labelnames=("from_method", "to_method"), registry=registry,
        ),
        route_accuracy=Counter(
            COUNTER_ROUTE_ACCURACY, "Code route accuracy sample",
            labelnames=("verdict",), registry=registry,
        ),
        explore_rounds=Histogram(
            HISTOGRAM_EXPLORE_ROUNDS, "Code explore rounds distribution",
            labelnames=("converge_level",),
            buckets=(1, 2, 3, 4, 5, 6, 7),
            registry=registry,
        ),
        explorer_refine_errors=Counter(
            COUNTER_EXPLORER_REFINE_ERRORS, "Code explorer refine/assess errors",
            labelnames=("op",), registry=registry,
        ),
        searcher_timeout=Counter(
            COUNTER_SEARCHER_TIMEOUT, "ripgrep subprocess timeouts",
            labelnames=("op",), registry=registry,
        ),
        searcher_fail=Counter(
            COUNTER_SEARCHER_FAIL, "ripgrep subprocess failures",
            labelnames=("op",), registry=registry,
        ),
        repo_registry_query=Histogram(
            HISTOGRAM_REPO_REGISTRY_QUERY, "RepoRegistry DB query latency",
            labelnames=("op",), registry=registry,
        ),
        repo_registry_admin_ops=Counter(
            COUNTER_REPO_REGISTRY_ADMIN_OPS, "RepoRegistry admin ops",
            labelnames=("op", "status"), registry=registry,
        ),
        repo_registry_fallback=Counter(
            COUNTER_REPO_REGISTRY_FALLBACK, "RepoRegistry fallback hits",
            labelnames=("reason",), registry=registry,
        ),
        repo_registry_count=Gauge(
            GAUGE_REPO_REGISTRY_COUNT, "RepoRegistry enabled=true count",
            registry=registry,
        ),
        route_llm_latency=Histogram(
            HISTOGRAM_ROUTE_LLM_LATENCY, "Code route LLM latency seconds",
            labelnames=("route_method",),
            buckets=(0.1, 0.5, 1.0, 2.0, 3.0, 5.0),
            registry=registry,
        ),
        route_total_latency=Histogram(
            HISTOGRAM_ROUTE_TOTAL_LATENCY, "Code route total latency seconds",
            labelnames=("route_method",),
            buckets=(0.1, 0.5, 1.0, 2.0, 3.0, 5.0),
            registry=registry,
        ),
        route_two_stage_seconds=Histogram(
            HISTOGRAM_ROUTE_TWO_STAGE_SECONDS, "Stage 1 keyword filter latency",
            labelnames=("op",),
            buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=registry,
        ),
        route_two_stage_results=Histogram(
            HISTOGRAM_ROUTE_TWO_STAGE_RESULTS, "Stage 1 keyword filter recall count",
            labelnames=("op",),
            buckets=(0, 1, 3, 5, 10, 20),
            registry=registry,
        ),
    )

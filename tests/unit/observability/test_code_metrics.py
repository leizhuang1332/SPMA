"""Tests for code_metrics module (design-13 §7.1)."""
from spma.observability.code_metrics import (
    build_code_metrics, COUNTER_ROUTE_TOTAL, COUNTER_ROUTE_FALLBACK,
    COUNTER_ROUTE_ACCURACY, COUNTER_EXPLORER_REFINE_ERRORS,
    COUNTER_SEARCHER_TIMEOUT, COUNTER_SEARCHER_FAIL,
    COUNTER_REPO_REGISTRY_FALLBACK, COUNTER_REPO_REGISTRY_ADMIN_OPS,
    HISTOGRAM_ROUTE_LLM_LATENCY, HISTOGRAM_ROUTE_TOTAL_LATENCY,
    HISTOGRAM_EXPLORE_ROUNDS, HISTOGRAM_REPO_REGISTRY_QUERY,
    HISTOGRAM_ROUTE_TWO_STAGE_SECONDS, HISTOGRAM_ROUTE_TWO_STAGE_RESULTS,
    GAUGE_REPO_REGISTRY_COUNT, COUNTER_ROUTE_CONFIDENCE,
)


def test_build_code_metrics_returns_all_components():
    metrics = build_code_metrics()
    # 16 个指标全部存在
    assert metrics.route_total is not None
    assert metrics.route_fallback is not None
    assert metrics.route_llm_latency is not None
    assert metrics.route_total_latency is not None
    assert metrics.route_confidence is not None
    assert metrics.route_accuracy is not None
    assert metrics.explore_rounds is not None
    assert metrics.explorer_refine_errors is not None
    assert metrics.searcher_timeout is not None
    assert metrics.searcher_fail is not None
    assert metrics.repo_registry_query is not None
    assert metrics.repo_registry_admin_ops is not None
    assert metrics.repo_registry_fallback is not None
    assert metrics.repo_registry_count is not None
    assert metrics.route_two_stage_seconds is not None
    assert metrics.route_two_stage_results is not None


def test_metric_names_constants():
    """指标名常量与 spec 附录 C 100% 对齐。"""
    assert COUNTER_ROUTE_TOTAL == "code_route_total"
    assert COUNTER_ROUTE_FALLBACK == "code_route_fallback_total"
    assert COUNTER_ROUTE_ACCURACY == "code_route_accuracy_sample"
    assert COUNTER_EXPLORER_REFINE_ERRORS == "code_explorer_refine_errors_total"
    assert COUNTER_SEARCHER_TIMEOUT == "code_searcher_timeout_total"
    assert COUNTER_SEARCHER_FAIL == "code_searcher_fail_total"
    assert COUNTER_REPO_REGISTRY_FALLBACK == "code_repo_registry_fallback_total"
    assert COUNTER_REPO_REGISTRY_ADMIN_OPS == "code_repo_registry_admin_ops_total"
    assert HISTOGRAM_ROUTE_LLM_LATENCY == "code_route_llm_latency_seconds"
    assert HISTOGRAM_ROUTE_TOTAL_LATENCY == "code_route_total_latency_seconds"
    assert HISTOGRAM_EXPLORE_ROUNDS == "code_explore_rounds"
    assert HISTOGRAM_REPO_REGISTRY_QUERY == "code_repo_registry_query_seconds"
    assert HISTOGRAM_ROUTE_TWO_STAGE_SECONDS == "code_route_two_stage_seconds"
    assert HISTOGRAM_ROUTE_TWO_STAGE_RESULTS == "code_route_two_stage_results"
    assert GAUGE_REPO_REGISTRY_COUNT == "code_repo_registry_count"
    assert COUNTER_ROUTE_CONFIDENCE == "code_route_confidence"

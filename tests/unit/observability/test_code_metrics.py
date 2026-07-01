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


# ============================================================
# Task 5：4 个反思相关 Prometheus 指标注册测试
# ============================================================


def test_reflection_metrics_registered():
    """4 个反思相关 Prometheus 指标必须已注册（Task 5）。"""
    from spma.observability import code_metrics

    # 访问指标（prometheus_client 在首次访问时注册）
    assert code_metrics.code_reflection_total is not None
    assert code_metrics.code_reflection_duration_seconds is not None
    assert code_metrics.code_reflection_search_terms_changed is not None
    assert code_metrics.code_reflection_consecutive_no_progress is not None


def test_reflection_total_outcome_labels():
    """code_reflection_total 必须支持 outcome 标签（triggered/skipped/failed/capped）。"""
    from spma.observability.code_metrics import code_reflection_total

    # 4 种 outcome 标签都能 labels().inc()，无 ValueError
    for outcome in ("triggered", "skipped", "failed", "capped"):
        code_reflection_total.labels(outcome=outcome).inc()


def test_reflection_metrics_behavior():
    """4 个指标的接口必须支持各自语义操作。"""
    from spma.observability import code_metrics

    # Counter: .inc()
    code_metrics.code_reflection_search_terms_changed.inc()
    # Histogram: .observe(seconds)
    code_metrics.code_reflection_duration_seconds.observe(1.5)
    # Gauge: .set(value)
    code_metrics.code_reflection_consecutive_no_progress.set(2)


def test_reflection_metric_types():
    """4 个反思指标类型与 plan Task 5 设计一致：Counter/Histogram/Counter/Gauge。"""
    from prometheus_client import Counter, Gauge, Histogram
    from spma.observability import code_metrics

    assert isinstance(code_metrics.code_reflection_total, Counter)
    assert isinstance(code_metrics.code_reflection_duration_seconds, Histogram)
    assert isinstance(code_metrics.code_reflection_search_terms_changed, Counter)
    assert isinstance(code_metrics.code_reflection_consecutive_no_progress, Gauge)


def test_reflection_metric_labels_and_buckets():
    """code_reflection_total 必须有 outcome label；duration histogram 必须有合理 buckets。"""
    from spma.observability import code_metrics

    # Counter label 检查
    assert code_metrics.code_reflection_total._labelnames == ("outcome",)
    # Histogram buckets 检查（与 plan Step 5.3 一致；prometheus_client
    # 自动追加 +Inf bucket，所以用 prefix 比对）
    expected_prefix = [0.5, 1.0, 2.0, 5.0, 10.0, 30.0]
    actual = code_metrics.code_reflection_duration_seconds._upper_bounds
    assert actual[: len(expected_prefix)] == expected_prefix
    assert actual[-1] == float("inf")  # prometheus 自动加 +Inf

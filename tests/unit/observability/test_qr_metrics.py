"""QR Prometheus 指标注册与采集测试。"""

from spma.observability.qr_metrics import (
    COUNTER_CACHE_ERRORS,
    COUNTER_CACHE_REQUESTS,
    GAUGE_WEIGHT_VERSION,
    QrMetrics,
    build_qr_metrics,
)


def test_build_qr_metrics_returns_distinct_registry_per_call():
    a = build_qr_metrics()
    b = build_qr_metrics()
    assert a is not b
    assert isinstance(a, QrMetrics)


def _family_name(registered_name: str) -> str:
    """prometheus_client strips ``_total`` from counter family names."""
    return registered_name[:-6] if registered_name.endswith("_total") else registered_name


def test_qr_metrics_increments():
    m = build_qr_metrics()
    m.observe_request(layer="l1", stage="rewrite")
    m.observe_request(layer="l1", stage="rewrite")
    m.observe_request(layer="miss", stage="rewrite")
    m.observe_error(layer="l2", error_type="pgvector_down")
    m.observe_l2_distance(distance=0.04, match_type="semantic_match")
    m.observe_flush_lag(seconds=12)
    m.set_weight_version(version=3)

    # 计数器值累计
    counters = {fam.name: fam for fam in m.registry.collect()}
    requests_fam = _family_name(COUNTER_CACHE_REQUESTS)
    errors_fam = _family_name(COUNTER_CACHE_ERRORS)
    val_l1 = next(
        s.value
        for fam in counters.values()
        if fam.name == requests_fam
        for s in fam.samples
        if s.labels.get("layer") == "l1"
    )
    assert val_l1 == 2
    assert any(
        s.value == 1
        for fam in counters.values()
        if fam.name == requests_fam
        for s in fam.samples
        if s.labels.get("layer") == "miss"
    )
    assert any(
        s.value == 1
        for fam in counters.values()
        if fam.name == errors_fam
        for s in fam.samples
        if s.labels.get("error_type") == "pgvector_down"
    )


def test_qr_metrics_well_known_names():
    m = build_qr_metrics()
    m.observe_request(layer="l1", stage="rewrite")
    fam_names = {fam.name for fam in m.registry.collect()}
    expected = {
        _family_name(COUNTER_CACHE_REQUESTS),
        _family_name(COUNTER_CACHE_ERRORS),
        "qr_cache_latency_seconds",
        "qr_cache_l2_distance",
        "qr_cache_hit_ratio",        # NEW
        GAUGE_WEIGHT_VERSION,
        "qr_audit_flush_lag_seconds",
    }
    assert expected <= fam_names


def test_qr_metrics_hit_ratio():
    m = build_qr_metrics()
    m.observe_hit_ratio(layer="l1", ratio=0.65)
    m.observe_hit_ratio(layer="l2", ratio=0.20)
    fam_names = {fam.name for fam in m.registry.collect()}
    assert "qr_cache_hit_ratio" in fam_names
    samples = {s.labels.get("layer"): s.value for fam in m.registry.collect()
               if fam.name == "qr_cache_hit_ratio" for s in fam.samples if s.labels}
    assert samples.get("l1") == 0.65
    assert samples.get("l2") == 0.20

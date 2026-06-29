"""Query Rewriter Prometheus 指标。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.3
"""

from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

COUNTER_CACHE_REQUESTS = "qr_cache_requests_total"
COUNTER_CACHE_ERRORS = "qr_cache_errors_total"
HISTOGRAM_CACHE_LATENCY = "qr_cache_latency_seconds"
HISTOGRAM_CACHE_L2_DISTANCE = "qr_cache_l2_distance"
COUNTER_FALLBACK = "qr_fallback_total"
GAUGE_WEIGHT_VERSION = "qr_state_weight_version"
GAUGE_FLUSH_LAG = "qr_audit_flush_lag_seconds"
GAUGE_CACHE_HIT_RATIO = "qr_cache_hit_ratio"


@dataclass
class QrMetrics:
    registry: CollectorRegistry
    cache_requests: Counter
    cache_errors: Counter
    cache_latency: Histogram
    cache_l2_distance: Histogram
    fallback_total: Counter
    weight_version: Gauge
    flush_lag: Gauge
    cache_hit_ratio: Gauge

    def observe_request(self, *, layer: str, stage: str = "rewrite") -> None:
        self.cache_requests.labels(layer=layer, stage=stage).inc()

    def observe_error(self, *, layer: str, error_type: str) -> None:
        self.cache_errors.labels(layer=layer, error_type=error_type).inc()

    def observe_latency(self, *, layer: str, op: str, seconds: float) -> None:
        self.cache_latency.labels(layer=layer, op=op).observe(seconds)

    def observe_l2_distance(self, *, distance: float, match_type: str) -> None:
        self.cache_l2_distance.labels(match_type=match_type).observe(distance)

    def observe_fallback(self, *, level: str, stage: str) -> None:
        self.fallback_total.labels(level=level, stage=stage).inc()

    def set_weight_version(self, *, version: int) -> None:
        self.weight_version.set(version)

    def observe_flush_lag(self, *, seconds: float) -> None:
        self.flush_lag.set(seconds)

    def observe_hit_ratio(self, *, layer: str, ratio: float) -> None:
        """在 1m 滚动窗口里设置命中率(0.0-1.0)."""
        self.cache_hit_ratio.labels(layer=layer).set(ratio)


def build_qr_metrics() -> QrMetrics:
    """每次调用返回独立 CollectorRegistry(便于多实例 / 多测试)。"""
    registry = CollectorRegistry()
    return QrMetrics(
        registry=registry,
        cache_requests=Counter(
            COUNTER_CACHE_REQUESTS,
            "QR cache requests by layer",
            labelnames=("layer", "stage"),
            registry=registry,
        ),
        cache_errors=Counter(
            COUNTER_CACHE_ERRORS,
            "QR cache errors",
            labelnames=("layer", "error_type"),
            registry=registry,
        ),
        cache_latency=Histogram(
            HISTOGRAM_CACHE_LATENCY,
            "QR cache latency seconds",
            labelnames=("layer", "op"),
            buckets=(0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0),
            registry=registry,
        ),
        cache_l2_distance=Histogram(
            HISTOGRAM_CACHE_L2_DISTANCE,
            "QR cache L2 cosine distance",
            labelnames=("match_type",),
            buckets=(0.01, 0.04, 0.08, 0.12, 0.16, 0.20, 0.30, 0.50),
            registry=registry,
        ),
        fallback_total=Counter(
            COUNTER_FALLBACK,
            "QR fallback triggers",
            labelnames=("level", "stage"),
            registry=registry,
        ),
        weight_version=Gauge(
            GAUGE_WEIGHT_VERSION,
            "Current weights version (PG qr_state_meta)",
            registry=registry,
        ),
        flush_lag=Gauge(
            GAUGE_FLUSH_LAG,
            "QR audit buffer flush lag seconds",
            registry=registry,
        ),
        cache_hit_ratio=Gauge(
            GAUGE_CACHE_HIT_RATIO,
            "QR cache hit ratio (rolling 1m) — labels: {layer}",
            labelnames=("layer",),
            registry=registry,
        ),
    )

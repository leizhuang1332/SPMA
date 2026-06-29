"""Query Rewriter 链路追踪(基于 OpenTelemetry)。

设计依据: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §4.2
"""

import hashlib
from collections.abc import Generator
from contextlib import contextmanager

from opentelemetry import trace
from opentelemetry.trace import Span, StatusCode

tracer = trace.get_tracer("query_rewriter")


def _get_tracer() -> trace.Tracer:
    """每次重新获取 tracer,尊重 set_tracer_provider 的最新结果(测试场景需要)。"""
    return trace.get_tracer("query_rewriter")


@contextmanager
def span_cache_lookup(
    query: str,
    weights_version: int,
    synonym_version: int,
    cache_layer: str | None = None,
) -> Generator[Span]:
    """qr.cache.lookup 根 span,必填属性:weights/synonym version。"""
    with _get_tracer().start_as_current_span("qr.cache.lookup") as span:
        span.set_attribute("query.hash", hashlib.md5(query.encode()).hexdigest()[:8])
        span.set_attribute("query.length", len(query))
        span.set_attribute("cache.weights_version", weights_version)
        span.set_attribute("cache.synonym_version", synonym_version)
        if cache_layer is not None:
            span.set_attribute("cache.layer", cache_layer)
        try:
            yield span
        except Exception as e:
            span.set_status(StatusCode.ERROR, str(e))
            span.record_exception(e)
            raise


def record_cache_layer(span: Span, cache_layer: str) -> None:
    """在 root span 上记录命中的层(l1/l2/miss)。"""
    span.set_attribute("cache.layer", cache_layer)


def record_l2_distance(span: Span, distance: float, match_type: str) -> None:
    """L2 子 span 属性:l2.distance + l2.match_type。"""
    span.set_attribute("l2.distance", float(distance))
    span.set_attribute("l2.match_type", match_type)

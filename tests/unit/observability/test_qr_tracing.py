"""qr_tracing span helpers 单元测试(用 in-memory exporter 验证属性)。"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from spma.observability.qr_tracing import span_cache_lookup


def test_cache_lookup_span_exposes_required_attributes():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    with span_cache_lookup(query="q", weights_version=2, synonym_version=3, cache_layer="l2") as span:
        span.set_attribute("extra", "x")

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "qr.cache.lookup"
    assert s.attributes["cache.weights_version"] == 2
    assert s.attributes["cache.synonym_version"] == 3
    assert s.attributes["extra"] == "x"


def test_cache_lookup_default_layer_is_none_when_not_provided():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    with span_cache_lookup(query="q", weights_version=1, synonym_version=1):
        pass
    s = exporter.get_finished_spans()[0]
    assert "cache.layer" not in s.attributes or s.attributes.get("cache.layer") is None

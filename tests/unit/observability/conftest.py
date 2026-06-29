"""observability 测试的 OpenTelemetry fixtures。

opentelemetry-api 的 global TracerProvider 一旦设置就不能再 set。
本 conftest 提供 autouse fixture,在每个测试前重置
_TRACER_PROVIDER_SET_ONCE._done,允许每个测试独立 set_tracer_provider
并使用独立的 InMemorySpanExporter。
"""

import pytest


@pytest.fixture(autouse=True)
def _reset_otel_tracer_provider():
    """每个测试前重置 OTel global TracerProvider。"""
    from opentelemetry import trace

    trace._TRACER_PROVIDER_SET_ONCE._done = False
    yield
    trace._TRACER_PROVIDER_SET_ONCE._done = False

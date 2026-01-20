# -*- coding: utf-8 -*-
"""The tracing interface class in agentscope."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from opentelemetry.trace import Tracer
else:
    Tracer = "Tracer"


def setup_tracing(endpoint: str) -> None:
    """Set up the AgentScope tracing by configuring the endpoint URL.

    Args:
        endpoint (`str`):
            The endpoint URL for the tracing exporter.
    """
    # Lazy import
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    # Prepare a span_processor
    exporter = OTLPSpanExporter(endpoint=endpoint)
    span_processor = BatchSpanProcessor(exporter)

    tracer_provider: TracerProvider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        # The provider is set outside, just add the span processor
        tracer_provider.add_span_processor(span_processor)

    else:
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)


def setup_phoenix_tracing(endpoint: str, project_name: str | None = None) -> None:
    """Set up tracing for Arize Phoenix with GenAI to OpenInference conversion.

    This function sets up OpenTelemetry tracing with a custom processor that
    converts OTel GenAI semantic convention attributes to OpenInference format,
    which is required for proper display in Phoenix UI.

    Args:
        endpoint (`str`):
            The Phoenix collector endpoint (e.g., http://localhost:6006/v1/traces).
        project_name (`str | None`, optional):
            The project name displayed in Phoenix UI. Defaults to "agentscope".
    """
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    from ._processor import GenAIToOpenInferenceProcessor

    project = project_name or "agentscope"
    resource = Resource.create({"service.name": project})
    provider = TracerProvider(resource=resource)

    exporter = OTLPSpanExporter(
        endpoint=endpoint,
        headers={"phoenix-project-name": project},
    )
    processor = GenAIToOpenInferenceProcessor(exporter)
    provider.add_span_processor(processor)

    trace.set_tracer_provider(provider)


def _get_tracer() -> Tracer:
    """Get the tracer
    Returns:
        `Tracer`: The tracer with the name "agentscope" and version.
    """
    from opentelemetry import trace
    from .._version import __version__

    return trace.get_tracer("agentscope", __version__)

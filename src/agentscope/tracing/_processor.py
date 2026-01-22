# -*- coding: utf-8 -*-
"""GenAI to OpenInference span processor for Phoenix compatibility."""
import logging
from typing import Any, Mapping, Sequence

from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

_logger = logging.getLogger(__name__)


# GenAI operation name to OpenInference span kind mapping
_OPERATION_TO_SPAN_KIND: dict[str, str] = {
    "chat": "LLM",
    "invoke_agent": "AGENT",
    "execute_tool": "TOOL",
    "embeddings": "EMBEDDING",
    "format": "CHAIN",
    "invoke_generic_function": "CHAIN",
}

# GenAI attribute keys
_GENAI_OPERATION_NAME = "gen_ai.operation.name"
_GENAI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
_GENAI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
_GENAI_REQUEST_MODEL = "gen_ai.request.model"
_GENAI_PROVIDER_NAME = "gen_ai.system"
_GENAI_AGENT_NAME = "gen_ai.agent.name"
_GENAI_TOOL_NAME = "gen_ai.tool.name"
_GENAI_CONVERSATION_ID = "gen_ai.conversation.id"

# AgentScope attribute keys
_AGENTSCOPE_FUNCTION_INPUT = "agentscope.function.input"
_AGENTSCOPE_FUNCTION_OUTPUT = "agentscope.function.output"

# OpenInference attribute keys
_OI_SPAN_KIND = "openinference.span.kind"
_OI_INPUT_VALUE = "input.value"
_OI_INPUT_MIME_TYPE = "input.mime_type"
_OI_OUTPUT_VALUE = "output.value"
_OI_OUTPUT_MIME_TYPE = "output.mime_type"
_OI_LLM_TOKEN_PROMPT = "llm.token_count.prompt"
_OI_LLM_TOKEN_COMPLETION = "llm.token_count.completion"
_OI_LLM_TOKEN_TOTAL = "llm.token_count.total"
_OI_METADATA_MODEL = "metadata.model"
_OI_METADATA_PROVIDER = "metadata.provider"
_OI_METADATA_AGENT_NAME = "metadata.agent_name"
_OI_METADATA_TOOL_NAME = "metadata.tool_name"
_OI_METADATA_CONVERSATION_ID = "metadata.conversation_id"


class _ConvertedSpan(ReadableSpan):
    """A ReadableSpan wrapper that adds OpenInference attributes."""

    __slots__ = ("_original", "_merged_attributes")

    def __init__(
        self,
        original: ReadableSpan,
        extra_attributes: dict[str, Any],
    ) -> None:
        self._original = original
        orig_attrs = dict(original.attributes) if original.attributes else {}
        self._merged_attributes = {**orig_attrs, **extra_attributes}

    @property
    def name(self) -> str:
        return self._original.name

    @property
    def context(self):
        return self._original.context

    @property
    def kind(self):
        return self._original.kind

    @property
    def parent(self):
        return self._original.parent

    @property
    def start_time(self) -> int:
        return self._original.start_time

    @property
    def end_time(self) -> int:
        return self._original.end_time

    @property
    def status(self):
        return self._original.status

    @property
    def attributes(self) -> Mapping[str, Any]:
        return self._merged_attributes

    @property
    def events(self):
        return self._original.events

    @property
    def links(self):
        return self._original.links

    @property
    def resource(self):
        return self._original.resource

    @property
    def instrumentation_scope(self):
        return self._original.instrumentation_scope

    @property
    def dropped_attributes(self) -> int:
        return self._original.dropped_attributes

    @property
    def dropped_events(self) -> int:
        return self._original.dropped_events

    @property
    def dropped_links(self) -> int:
        return self._original.dropped_links

    def get_span_context(self):
        return self._original.get_span_context()

    def to_json(self, indent: int | None = 4) -> str:
        return self._original.to_json(indent)


def _convert_genai_to_openinference(attrs: Mapping[str, Any]) -> dict[str, Any]:
    """Convert GenAI attributes to OpenInference format.

    Args:
        attrs: Original span attributes containing GenAI conventions.

    Returns:
        Dictionary of OpenInference attributes to add.
    """
    extra: dict[str, Any] = {}

    # 1. operation_name → span_kind
    if op_name := attrs.get(_GENAI_OPERATION_NAME):
        span_kind = _OPERATION_TO_SPAN_KIND.get(op_name, "CHAIN")
        extra[_OI_SPAN_KIND] = span_kind

    # 2. token counts
    input_tokens = attrs.get(_GENAI_USAGE_INPUT_TOKENS)
    output_tokens = attrs.get(_GENAI_USAGE_OUTPUT_TOKENS)
    if input_tokens is not None:
        extra[_OI_LLM_TOKEN_PROMPT] = input_tokens
    if output_tokens is not None:
        extra[_OI_LLM_TOKEN_COMPLETION] = output_tokens
    if input_tokens is not None and output_tokens is not None:
        extra[_OI_LLM_TOKEN_TOTAL] = input_tokens + output_tokens

    # 3. input/output values from agentscope.function.*
    if func_input := attrs.get(_AGENTSCOPE_FUNCTION_INPUT):
        extra[_OI_INPUT_VALUE] = func_input
        extra[_OI_INPUT_MIME_TYPE] = "application/json"
    if func_output := attrs.get(_AGENTSCOPE_FUNCTION_OUTPUT):
        extra[_OI_OUTPUT_VALUE] = func_output
        extra[_OI_OUTPUT_MIME_TYPE] = "application/json"

    # 4. metadata mappings
    if model := attrs.get(_GENAI_REQUEST_MODEL):
        extra[_OI_METADATA_MODEL] = model
    if provider := attrs.get(_GENAI_PROVIDER_NAME):
        extra[_OI_METADATA_PROVIDER] = provider
    if agent_name := attrs.get(_GENAI_AGENT_NAME):
        extra[_OI_METADATA_AGENT_NAME] = agent_name
    if tool_name := attrs.get(_GENAI_TOOL_NAME):
        extra[_OI_METADATA_TOOL_NAME] = tool_name
    if conv_id := attrs.get(_GENAI_CONVERSATION_ID):
        extra[_OI_METADATA_CONVERSATION_ID] = conv_id

    return extra


class GenAIToOpenInferenceExporter(SpanExporter):
    """SpanExporter wrapper that converts GenAI attributes to OpenInference.

    This exporter wraps another exporter and converts OTel GenAI semantic
    convention attributes to OpenInference format before exporting to Phoenix.
    """

    def __init__(self, wrapped_exporter: SpanExporter) -> None:
        self._wrapped = wrapped_exporter

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        converted = [self._convert_span(span) for span in spans]
        result = self._wrapped.export(converted)
        for span in converted:
            _logger.info("Exported span: %s (trace_id=%s)", span.name, span.context.trace_id)
        return result

    def _convert_span(self, span: ReadableSpan) -> ReadableSpan:
        if not span.attributes:
            return span

        extra_attrs = _convert_genai_to_openinference(span.attributes)
        if not extra_attrs:
            return span

        return _ConvertedSpan(span, extra_attrs)

    def shutdown(self) -> None:
        self._wrapped.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._wrapped.force_flush(timeout_millis)


class GenAIToOpenInferenceProcessor(SpanProcessor):
    """SpanProcessor that converts GenAI attributes to OpenInference on export.

    This processor wraps a SpanProcessor and uses a converting exporter to add
    OpenInference attributes before sending to Phoenix.
    """

    def __init__(
        self,
        exporter: SpanExporter,
        batch: bool = False,
        **processor_kwargs: Any,
    ) -> None:
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor

        converting_exporter = GenAIToOpenInferenceExporter(exporter)
        if batch:
            self._processor = BatchSpanProcessor(converting_exporter, **processor_kwargs)
        else:
            self._processor = SimpleSpanProcessor(converting_exporter)

    def on_start(self, span, parent_context=None) -> None:
        self._processor.on_start(span, parent_context)

    def on_end(self, span: ReadableSpan) -> None:
        self._processor.on_end(span)

    def shutdown(self) -> None:
        self._processor.shutdown()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return self._processor.force_flush(timeout_millis)

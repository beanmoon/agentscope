# AgentScope Tracing 机制分析

本文档详细分析 AgentScope 如何构造 tracing 数据并上报到 Arize Phoenix 等 OpenTelemetry 兼容的后端服务器。

## 目录
- [1. 初始化流程](#1-初始化流程)
- [2. OpenTelemetry 设置](#2-opentelemetry-设置)
- [3. Tracing 装饰器](#3-tracing-装饰器)
- [4. Span 属性字段详解](#4-span-属性字段详解)
- [5. 数据转换和序列化](#5-数据转换和序列化)
- [6. 完整数据流程图](#6-完整数据流程图)

---

## 1. 初始化流程

当用户调用 `agentscope.init(tracing_url=...)` 时，触发 tracing 的初始化：

```python
# src/agentscope/__init__.py

def init(
    project: str | None = None,
    name: str | None = None,
    run_id: str | None = None,
    logging_path: str | None = None,
    logging_level: str = "INFO",
    studio_url: str | None = None,
    tracing_url: str | None = None,  # Arize Phoenix 后端地址
) -> None:
    """初始化 agentscope"""
    
    # ... 其他初始化逻辑 ...
    
    endpoints = []
    if tracing_url:
        endpoints.append(tracing_url)
    
    if studio_url:
        endpoints.append(studio_url.strip("/") + "/v1/traces")
    
    # 移除重复端点
    endpoints = list(set(endpoints))

    if endpoints:
        from .tracing import setup_tracing
        
        for endpoint in endpoints:
            setup_tracing(endpoint=endpoint)
        _config.trace_enabled = True  # 设置全局标志
```

**关键点**：
- `tracing_url` 直接作为 OTLP HTTP 端点
- `studio_url` 会被转换为 `/v1/traces` 格式
- 设置 `_config.trace_enabled = True` 启用全局 tracing

---

## 2. OpenTelemetry 设置

`setup_tracing` 函数负责配置 OpenTelemetry 的基础设施：

```python
# src/agentscope/tracing/_setup.py

def setup_tracing(endpoint: str) -> None:
    """配置 OpenTelemetry tracing 端点"""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
        OTLPSpanExporter,
    )

    # 创建 OTLP HTTP Exporter，指向 Arize Phoenix
    exporter = OTLPSpanExporter(endpoint=endpoint)
    
    # 使用批量处理器提高性能
    span_processor = BatchSpanProcessor(exporter)

    tracer_provider: TracerProvider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        # 如果已有 provider，添加新的 processor
        tracer_provider.add_span_processor(span_processor)
    else:
        # 创建新的 provider
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)


def _get_tracer() -> Tracer:
    """获取 agentscope 专用 tracer"""
    from opentelemetry import trace
    from .._version import __version__

    return trace.get_tracer("agentscope", __version__)
```

**组件说明**：
| 组件 | 说明 |
|------|------|
| `OTLPSpanExporter` | 通过 HTTP 协议将 span 数据发送到后端 |
| `BatchSpanProcessor` | 批量处理 span，提高性能 |
| `TracerProvider` | OpenTelemetry 的核心 provider |
| `Tracer` | 用于创建 span 的实例，名称为 "agentscope" |

---

## 3. Tracing 装饰器

AgentScope 提供了多种 tracing 装饰器，用于追踪不同类型的操作：

### 3.1 `@trace_llm` - LLM 调用追踪

应用于 Chat Model 的 `__call__` 方法：

```python
# src/agentscope/model/_openai_model.py

class OpenAIChatModel(ChatModelBase):
    @trace_llm  # 应用装饰器
    async def __call__(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: ... = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        ...
```

**装饰器实现**：

```python
# src/agentscope/tracing/_trace.py

def trace_llm(func):
    """追踪 LLM 调用"""
    @wraps(func)
    async def async_wrapper(
        self: ChatModelBase,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        tracer = _get_tracer()

        # 提取请求属性
        request_attributes = _get_llm_request_attributes(self, args, kwargs)
        span_name = _get_llm_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.__call__"
        
        # 创建 span
        with tracer.start_as_current_span(
            name=span_name,  # e.g. "chat gpt-4"
            attributes={
                **request_attributes,
                **_get_common_attributes(),
                SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
            },
            end_on_exit=False,
        ) as span:
            try:
                res = await func(self, *args, **kwargs)

                if isinstance(res, AsyncGenerator):
                    return _trace_async_generator_wrapper(res, span)

                # 设置响应属性
                span.set_attributes(_get_llm_response_attributes(res))
                _set_span_success_status(span)
                return res

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None

    return async_wrapper
```

### 3.2 `@trace_reply` - Agent 回复追踪

应用于 Agent 的 `reply` 方法：

```python
# src/agentscope/agent/_react_agent.py

class ReActAgent(ReActAgentBase):
    @trace_reply  # 应用装饰器
    async def reply(self, msg: Msg | None = None) -> Msg:
        ...
```

### 3.3 `@trace_toolkit` - 工具调用追踪

应用于 Toolkit 的 `call_tool_function` 方法：

```python
# src/agentscope/tool/_toolkit.py

class Toolkit:
    @trace_toolkit  # 应用装饰器
    async def call_tool_function(
        self,
        tool_call: ToolUseBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        ...
```

### 3.4 `@trace_format` - 格式化追踪

应用于 Formatter 的 `format` 方法：

```python
# src/agentscope/formatter/_truncated_formatter_base.py

class TruncatedFormatterBase(FormatterBase):
    @trace_format  # 应用装饰器
    async def format(...) -> list[dict]:
        ...
```

### 3.5 `@trace_embedding` - Embedding 追踪

用于追踪 embedding 模型调用。

### 3.6 `@trace` - 通用追踪装饰器

用于追踪任意函数：

```python
from agentscope.tracing import trace

@trace(name="my_custom_function")
async def my_function(*args, **kwargs):
    ...
```

---

## 4. Span 属性字段详解

AgentScope 遵循 [OpenTelemetry Semantic Conventions for GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 规范填充 span 属性。

### 4.1 属性定义

```python
# src/agentscope/tracing/_attributes.py

class SpanAttributes:
    """Span 属性常量定义"""
    
    # ===== GenAI 公共属性 =====
    GEN_AI_CONVERSATION_ID = "gen_ai.conversation_id"    # 对话 ID
    GEN_AI_OPERATION_NAME = "gen_ai.operation_name"      # 操作名称
    GEN_AI_PROVIDER_NAME = "gen_ai.provider_name"        # 提供者名称
    
    # ===== GenAI 请求属性 =====
    GEN_AI_REQUEST_MODEL = "gen_ai.request.model"              # 模型名称
    GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"  # 温度参数
    GEN_AI_REQUEST_TOP_P = "gen_ai.request.top_p"              # top_p 参数
    GEN_AI_REQUEST_TOP_K = "gen_ai.request.top_k"              # top_k 参数
    GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"    # 最大 token 数
    GEN_AI_REQUEST_PRESENCE_PENALTY = "gen_ai.request.presence_penalty"
    GEN_AI_REQUEST_FREQUENCY_PENALTY = "gen_ai.request.frequency_penalty"
    GEN_AI_REQUEST_STOP_SEQUENCES = "gen_ai.request.stop_sequences"
    GEN_AI_REQUEST_SEED = "gen_ai.request.seed"
    
    # ===== GenAI 响应属性 =====
    GEN_AI_RESPONSE_ID = "gen_ai.response.id"                    # 响应 ID
    GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"  # 结束原因
    
    # ===== GenAI 使用量属性 =====
    GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"    # 输入 token 数
    GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"  # 输出 token 数
    
    # ===== GenAI 消息属性 =====
    GEN_AI_INPUT_MESSAGES = "gen_ai.input.messages"    # 输入消息
    GEN_AI_OUTPUT_MESSAGES = "gen_ai.output.messages"  # 输出消息
    
    # ===== GenAI Agent 属性 =====
    GEN_AI_AGENT_ID = "gen_ai.agent.id"                      # Agent ID
    GEN_AI_AGENT_NAME = "gen_ai.agent.name"                  # Agent 名称
    GEN_AI_AGENT_DESCRIPTION = "gen_ai.agent.description"    # Agent 描述
    GEN_AI_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"  # 系统指令
    
    # ===== GenAI Tool 属性 =====
    GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"              # 工具调用 ID
    GEN_AI_TOOL_NAME = "gen_ai.tool.name"                    # 工具名称
    GEN_AI_TOOL_DESCRIPTION = "gen_ai.tool.description"      # 工具描述
    GEN_AI_TOOL_CALL_ARGUMENTS = "gen_ai.tool.call.arguments"  # 工具参数
    GEN_AI_TOOL_CALL_RESULT = "gen_ai.tool.call.result"      # 工具结果
    GEN_AI_TOOL_DEFINITIONS = "gen_ai.tool.definitions"      # 工具定义
    
    # ===== GenAI Embedding 属性 =====
    GEN_AI_EMBEDDINGS_DIMENSION_COUNT = "gen_ai.embeddings.dimension.count"
    
    # ===== AgentScope 扩展属性 =====
    AGENTSCOPE_FORMAT_TARGET = "agentscope.format.target"      # 格式化目标
    AGENTSCOPE_FORMAT_COUNT = "agentscope.format.count"        # 格式化消息数
    AGENTSCOPE_FUNCTION_NAME = "agentscope.function.name"      # 函数名称
    AGENTSCOPE_FUNCTION_INPUT = "agentscope.function.input"    # 函数输入
    AGENTSCOPE_FUNCTION_OUTPUT = "agentscope.function.output"  # 函数输出


class OperationNameValues:
    """操作名称枚举"""
    FORMATTER = "format"                    # 格式化操作
    INVOKE_GENERIC_FUNCTION = "invoke_generic_function"  # 通用函数调用
    CHAT = "chat"                           # LLM 聊天
    INVOKE_AGENT = "invoke_agent"           # Agent 调用
    EXECUTE_TOOL = "execute_tool"           # 工具执行
    EMBEDDINGS = "embeddings"               # Embedding 操作


class ProviderNameValues:
    """提供者名称枚举"""
    DASHSCOPE = "dashscope"
    OLLAMA = "ollama"
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GCP_GEMINI = "gcp.gemini"
    MOONSHOT = "moonshot"
    AZURE_AI_OPENAI = "azure.ai.openai"
    AWS_BEDROCK = "aws.bedrock"
```

### 4.2 属性提取函数

#### LLM 请求属性提取

```python
# src/agentscope/tracing/_extractor.py

def _get_llm_request_attributes(
    instance: ChatModelBase,
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Dict[str, Any]:
    """提取 LLM 请求属性"""
    
    attributes = {
        # 必需属性
        SpanAttributes.GEN_AI_OPERATION_NAME: OperationNameValues.CHAT,
        SpanAttributes.GEN_AI_PROVIDER_NAME: _get_provider_name(instance),
        
        # 条件必需属性
        SpanAttributes.GEN_AI_REQUEST_MODEL: getattr(
            instance, "model_name", "unknown_model"
        ),
        
        # 推荐属性
        SpanAttributes.GEN_AI_REQUEST_TEMPERATURE: kwargs.get("temperature"),
        SpanAttributes.GEN_AI_REQUEST_TOP_P: kwargs.get("p") or kwargs.get("top_p"),
        SpanAttributes.GEN_AI_REQUEST_TOP_K: kwargs.get("top_k"),
        SpanAttributes.GEN_AI_REQUEST_MAX_TOKENS: kwargs.get("max_tokens"),
        SpanAttributes.GEN_AI_REQUEST_PRESENCE_PENALTY: kwargs.get("presence_penalty"),
        SpanAttributes.GEN_AI_REQUEST_FREQUENCY_PENALTY: kwargs.get("frequency_penalty"),
        SpanAttributes.GEN_AI_REQUEST_STOP_SEQUENCES: kwargs.get("stop_sequences"),
        SpanAttributes.GEN_AI_REQUEST_SEED: kwargs.get("seed"),
        
        # 自定义属性
        SpanAttributes.AGENTSCOPE_FUNCTION_INPUT: _serialize_to_str({
            "args": args,
            "kwargs": kwargs,
        }),
    }

    # 提取工具定义
    tool_definitions = _get_tool_definitions(
        tools=kwargs.get("tools"),
        tool_choice=kwargs.get("tool_choice"),
    )
    if tool_definitions:
        attributes[SpanAttributes.GEN_AI_TOOL_DEFINITIONS] = tool_definitions

    return {k: v for k, v in attributes.items() if v is not None}
```

#### LLM 响应属性提取

```python
def _get_llm_response_attributes(chat_response: Any) -> Dict[str, Any]:
    """提取 LLM 响应属性"""
    
    attributes = {
        SpanAttributes.GEN_AI_RESPONSE_ID: getattr(
            chat_response, "id", "unknown_id"
        ),
        SpanAttributes.GEN_AI_RESPONSE_FINISH_REASONS: '["stop"]',
    }
    
    # Token 使用量
    if hasattr(chat_response, "usage") and chat_response.usage:
        attributes[SpanAttributes.GEN_AI_USAGE_INPUT_TOKENS] = (
            chat_response.usage.input_tokens
        )
        attributes[SpanAttributes.GEN_AI_USAGE_OUTPUT_TOKENS] = (
            chat_response.usage.output_tokens
        )

    # 输出消息
    output_messages = _get_llm_output_messages(chat_response)
    if output_messages:
        attributes[SpanAttributes.GEN_AI_OUTPUT_MESSAGES] = _serialize_to_str(
            output_messages
        )

    attributes[SpanAttributes.AGENTSCOPE_FUNCTION_OUTPUT] = _serialize_to_str(
        chat_response
    )
    return attributes
```

#### Agent 属性提取

```python
def _get_agent_request_attributes(
    instance: "AgentBase",
    args: Tuple[Any, ...],
    kwargs: Dict[str, Any],
) -> Dict[str, str]:
    """提取 Agent 请求属性"""
    
    attributes = {
        SpanAttributes.GEN_AI_OPERATION_NAME: OperationNameValues.INVOKE_AGENT,
        SpanAttributes.GEN_AI_AGENT_ID: getattr(instance, "id", "unknown"),
        SpanAttributes.GEN_AI_AGENT_NAME: getattr(instance, "name", "unknown_agent"),
        SpanAttributes.GEN_AI_AGENT_DESCRIPTION: inspect.getdoc(instance.__class__)
            or "No description available",
    }

    # 输入消息
    msg = None
    if args and len(args) > 0:
        msg = args[0]
    elif "msg" in kwargs:
        msg = kwargs["msg"]
    if msg:
        input_messages = _get_agent_messages(msg)
        attributes[SpanAttributes.GEN_AI_INPUT_MESSAGES] = _serialize_to_str(
            input_messages
        )

    attributes[SpanAttributes.AGENTSCOPE_FUNCTION_INPUT] = _serialize_to_str({
        "args": args,
        "kwargs": kwargs,
    })
    return attributes
```

#### Tool 属性提取

```python
def _get_tool_request_attributes(
    instance: "Toolkit",
    tool_call: ToolUseBlock,
) -> Dict[str, str]:
    """提取 Tool 请求属性"""
    
    attributes = {
        SpanAttributes.GEN_AI_OPERATION_NAME: OperationNameValues.EXECUTE_TOOL,
    }

    if tool_call:
        tool_name = tool_call.get("name")
        attributes[SpanAttributes.GEN_AI_TOOL_CALL_ID] = tool_call.get("id")
        attributes[SpanAttributes.GEN_AI_TOOL_NAME] = tool_name
        attributes[SpanAttributes.GEN_AI_TOOL_CALL_ARGUMENTS] = _serialize_to_str(
            tool_call.get("input")
        )

        # 获取工具描述
        if tool_name:
            if tool := getattr(instance, "tools", {}).get(tool_name):
                if tool_func := getattr(tool, "json_schema", {}).get("function", {}):
                    attributes[SpanAttributes.GEN_AI_TOOL_DESCRIPTION] = tool_func.get(
                        "description", "unknown_description"
                    )

        attributes[SpanAttributes.AGENTSCOPE_FUNCTION_INPUT] = _serialize_to_str({
            "tool_call": tool_call,
        })
    return attributes
```

### 4.3 Provider 识别

AgentScope 根据模型类名和 base_url 自动识别提供者：

```python
_CLASS_NAME_MAP = {
    "dashscope": ProviderNameValues.DASHSCOPE,
    "openai": ProviderNameValues.OPENAI,
    "anthropic": ProviderNameValues.ANTHROPIC,
    "gemini": ProviderNameValues.GCP_GEMINI,
    "ollama": ProviderNameValues.OLLAMA,
    "deepseek": ProviderNameValues.DEEPSEEK,
    "trinity": ProviderNameValues.OPENAI,
}

# 基于 base_url 的提供者识别（用于 OpenAI 兼容 API）
_BASE_URL_PROVIDER_MAP = [
    ("api.openai.com", ProviderNameValues.OPENAI),
    ("dashscope", ProviderNameValues.DASHSCOPE),
    ("deepseek", ProviderNameValues.DEEPSEEK),
    ("moonshot", ProviderNameValues.MOONSHOT),
    ("generativelanguage.googleapis.com", ProviderNameValues.GCP_GEMINI),
    ("openai.azure.com", ProviderNameValues.AZURE_AI_OPENAI),
    ("amazonaws.com", ProviderNameValues.AWS_BEDROCK),
]
```

---

## 5. 数据转换和序列化

### 5.1 ContentBlock 转换

将 AgentScope 的 ContentBlock 转换为 OpenTelemetry GenAI 格式：

```python
# src/agentscope/tracing/_converter.py

def _convert_block_to_part(block: ContentBlock) -> Dict[str, Any] | None:
    """将 content block 转换为 OpenTelemetry GenAI part 格式"""
    
    block_type = block.get("type")
    part = None

    if block_type == "text":
        part = {
            "type": "text",
            "content": block.get("text", ""),
        }
    elif block_type == "thinking":
        part = {
            "type": "reasoning",
            "content": block.get("thinking", ""),
        }
    elif block_type == "tool_use":
        part = {
            "type": "tool_call",
            "id": block.get("id", ""),
            "name": block.get("name", ""),
            "arguments": block.get("input", {}),
        }
    elif block_type == "tool_result":
        output = block.get("output", "")
        result = _serialize_to_str(output) if isinstance(output, (list, dict)) else str(output)
        part = {
            "type": "tool_call_response",
            "id": block.get("id", ""),
            "response": result,
        }
    elif block_type in ("image", "audio", "video"):
        source = block.get("source", {})
        part = _convert_media_block(source, modality=block_type)

    return part
```

### 5.2 JSON 序列化

```python
# src/agentscope/tracing/_utils.py

def _serialize_to_str(value: Any) -> str:
    """将任意值序列化为 JSON 字符串"""
    try:
        return json.dumps(value, ensure_ascii=False)
    except TypeError:
        return json.dumps(_to_serializable(value), ensure_ascii=False)


def _to_serializable(obj: Any) -> Any:
    """将对象转换为可 JSON 序列化的类型"""
    
    # 原始类型
    if isinstance(obj, (str, int, bool, float, type(None))):
        return obj
    
    # 集合类型
    elif isinstance(obj, (list, tuple, set, frozenset)):
        return [_to_serializable(x) for x in obj]
    
    # 字典类型
    elif isinstance(obj, dict):
        return {str(key): _to_serializable(val) for (key, val) in obj.items()}
    
    # Pydantic 模型和 dataclass
    elif isinstance(obj, (Msg, BaseModel)) or is_dataclass(obj):
        return repr(obj)
    
    # 日期时间类型
    elif isinstance(obj, (datetime.date, datetime.datetime, datetime.time)):
        return obj.isoformat()
    
    elif isinstance(obj, datetime.timedelta):
        return obj.total_seconds()
    
    # 枚举类型
    elif isinstance(obj, enum.Enum):
        return _to_serializable(obj.value)
    
    # 其他类型转字符串
    else:
        return str(obj)
```

---

## 6. 完整数据流程图

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                          AgentScope Tracing 数据流                              │
└─────────────────────────────────────────────────────────────────────────────────┘

                           ┌──────────────────────┐
                           │  agentscope.init()   │
                           │  tracing_url = ...   │
                           └──────────┬───────────┘
                                      │
                                      ▼
                           ┌──────────────────────┐
                           │   setup_tracing()    │
                           │                      │
                           │ • OTLPSpanExporter   │
                           │ • BatchSpanProcessor │
                           │ • TracerProvider     │
                           └──────────┬───────────┘
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
           ┌────────────────┐ ┌────────────────┐ ┌────────────────┐
           │   @trace_llm   │ │  @trace_reply  │ │ @trace_toolkit │
           │                │ │                │ │                │
           │ ChatModelBase. │ │ AgentBase.     │ │ Toolkit.       │
           │ __call__()     │ │ reply()        │ │ call_tool()    │
           └───────┬────────┘ └───────┬────────┘ └───────┬────────┘
                   │                  │                  │
                   ▼                  ▼                  ▼
           ┌───────────────────────────────────────────────────────┐
           │              _get_*_request_attributes()              │
           │                                                       │
           │  提取 Span 属性:                                      │
           │  • gen_ai.operation_name                              │
           │  • gen_ai.provider_name                               │
           │  • gen_ai.request.model                               │
           │  • gen_ai.agent.id / gen_ai.tool.name                 │
           │  • agentscope.function.input                          │
           │  • ...                                                │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │           tracer.start_as_current_span()              │
           │                                                       │
           │  Span 信息:                                           │
           │  • name: "chat gpt-4" / "invoke_agent MyAgent"        │
           │  • attributes: request_attributes                     │
           │  • status: OK / ERROR                                 │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │              执行实际函数调用                         │
           │  await func(self, *args, **kwargs)                    │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │             _get_*_response_attributes()              │
           │                                                       │
           │  追加响应属性:                                        │
           │  • gen_ai.response.id                                 │
           │  • gen_ai.usage.input_tokens                          │
           │  • gen_ai.usage.output_tokens                         │
           │  • gen_ai.output.messages                             │
           │  • agentscope.function.output                         │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │            BatchSpanProcessor                         │
           │                                                       │
           │  批量收集 Span 数据                                   │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │            OTLPSpanExporter                           │
           │                                                       │
           │  HTTP POST to tracing_url                             │
           │  Content-Type: application/x-protobuf                 │
           └───────────────────────┬───────────────────────────────┘
                                   │
                                   ▼
           ┌───────────────────────────────────────────────────────┐
           │            Arize Phoenix / 其他 OTLP 后端             │
           │                                                       │
           │  接收、存储、可视化 Span 数据                         │
           └───────────────────────────────────────────────────────┘
```

---

## 7. 示例：一次完整的 LLM 调用 Span

当执行一次 LLM 调用时，生成的 Span 数据结构如下：

```json
{
  "name": "chat gpt-4",
  "trace_id": "abc123...",
  "span_id": "def456...",
  "parent_span_id": "parent789...",
  "start_time": "2026-01-20T10:30:00.000Z",
  "end_time": "2026-01-20T10:30:02.500Z",
  "status": {
    "code": "OK"
  },
  "attributes": {
    "gen_ai.conversation_id": "run_xyz123",
    "gen_ai.operation_name": "chat",
    "gen_ai.provider_name": "openai",
    "gen_ai.request.model": "gpt-4",
    "gen_ai.request.temperature": 0.7,
    "gen_ai.request.max_tokens": 1024,
    "gen_ai.response.id": "chatcmpl-abc...",
    "gen_ai.response.finish_reasons": "[\"stop\"]",
    "gen_ai.usage.input_tokens": 150,
    "gen_ai.usage.output_tokens": 200,
    "gen_ai.output.messages": "[{\"role\": \"assistant\", \"parts\": [{\"type\": \"text\", \"content\": \"...\"}], \"finish_reason\": \"stop\"}]",
    "agentscope.function.name": "OpenAIChatModel.__call__",
    "agentscope.function.input": "{\"args\": [...], \"kwargs\": {...}}",
    "agentscope.function.output": "ChatResponse(...)"
  }
}
```

---

## 8. 总结

AgentScope 的 tracing 机制特点：

1. **标准化**：遵循 OpenTelemetry Semantic Conventions for GenAI 规范
2. **全面性**：覆盖 LLM、Agent、Tool、Formatter、Embedding 全流程
3. **灵活性**：支持多端点配置，可同时上报到多个后端
4. **高性能**：使用 BatchSpanProcessor 批量处理
5. **可扩展**：提供通用 `@trace` 装饰器，支持自定义函数追踪
6. **兼容性**：支持主流 LLM 提供商的自动识别

通过这套机制，用户可以在 Arize Phoenix 等平台上清晰地看到：
- 每次 Agent 调用的完整链路
- LLM 请求的详细参数和响应
- Token 使用量和调用延迟
- 工具调用的输入输出
- 错误和异常追踪

# AgentScope Phoenix 集成改动说明

本文档记录了为支持 Arize Phoenix 可观测性平台所做的代码改动及原因。

## 目录
- [1. 背景](#1-背景)
- [2. 问题概述](#2-问题概述)
- [3. 解决方案](#3-解决方案)
- [4. 代码改动详情](#4-代码改动详情)
- [5. OpenInference 语义约定](#5-openinference-语义约定)

---

## 1. 背景

AgentScope 原生使用 [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) 来定义 span 属性。然而，Arize Phoenix 使用的是 [OpenInference Semantic Conventions](https://github.com/Arize-ai/openinference)，两者在属性命名和结构上存在差异。

这导致在 Phoenix UI 中部分列显示为空：
- `kind` 列 - span 类型（LLM/Agent/Tool）
- `input` 列 - 输入数据
- `output` 列 - 输出数据
- `metadata` 列 - 元数据信息
- `cumulative tokens` 列 - token 统计

---

## 2. 问题概述

### 2.1 TracerProvider 冲突

**问题**：同时启用 Phoenix tracing 和 AS Studio 时，`phoenix.otel.register()` 创建的 `SpanProcessor` 会被 Studio 的 `add_span_processor` 覆盖。

**原因**：Phoenix 的 `TracerProvider` 在调用 `add_span_processor` 时，默认会替换已有的 processor。

### 2.2 属性语义不兼容

**问题**：Phoenix 无法识别 OpenTelemetry GenAI 规范的属性。

**原因**：Phoenix 期望使用 OpenInference 语义约定：

| Phoenix 列 | 期望属性 | AgentScope 原属性 |
|-----------|---------|------------------|
| kind | `openinference.span.kind` | 无 |
| input | `input.value` | `agentscope.function.input` |
| output | `output.value` | `agentscope.function.output` |
| metadata | `metadata.*` | 无 |
| cumulative tokens | `llm.token_count.*` | `gen_ai.usage.*` |

---

## 3. 解决方案

### 3.1 TracerProvider 兼容

修改 `_setup.py` 中的 `setup_tracing` 函数，使用 `replace_default_processor=False` 参数：

```python
def setup_tracing(endpoint: str) -> None:
    provider = trace.get_tracer_provider()
    if isinstance(provider, TracerProvider):
        try:
            # Phoenix TracerProvider 支持此参数
            provider.add_span_processor(span_processor, replace_default_processor=False)
        except TypeError:
            # 标准 SDK TracerProvider 不支持该参数
            provider.add_span_processor(span_processor)
```

### 3.2 添加 OpenInference 属性

在保留原有 GenAI 属性的同时，额外添加 OpenInference 兼容属性，确保两种后端都能正常工作。

---

## 4. 代码改动详情

### 4.1 `_attributes.py` 新增属性

```python
class SpanAttributes:
    # OpenInference 核心属性
    OPENINFERENCE_SPAN_KIND = "openinference.span.kind"
    INPUT_VALUE = "input.value"
    INPUT_MIME_TYPE = "input.mime_type"
    OUTPUT_VALUE = "output.value"
    OUTPUT_MIME_TYPE = "output.mime_type"

    # OpenInference token 统计属性（用于 Phoenix cumulative tokens 列）
    LLM_TOKEN_COUNT_PROMPT = "llm.token_count.prompt"
    LLM_TOKEN_COUNT_COMPLETION = "llm.token_count.completion"
    LLM_TOKEN_COUNT_TOTAL = "llm.token_count.total"

    # OpenInference metadata 属性（用于 Phoenix metadata 列）
    METADATA_MODEL = "metadata.model"
    METADATA_PROVIDER = "metadata.provider"
    METADATA_AGENT_NAME = "metadata.agent_name"
    METADATA_TOOL_NAME = "metadata.tool_name"
    METADATA_CONVERSATION_ID = "metadata.conversation_id"


class OpenInferenceSpanKind:
    """OpenInference span kind 值"""
    LLM = "LLM"
    AGENT = "AGENT"
    TOOL = "TOOL"
    EMBEDDING = "EMBEDDING"
    CHAIN = "CHAIN"
```

### 4.2 `_extractor.py` 函数改动

#### `_get_llm_request_attributes`

新增属性：
- `openinference.span.kind` = "LLM"
- `input.value` = JSON 序列化的输入
- `input.mime_type` = "application/json"
- `metadata.model` = 模型名称
- `metadata.provider` = 提供商名称
- `metadata.conversation_id` = 对话 ID

#### `_get_llm_response_attributes`

新增属性：
- `output.value` = JSON 序列化的输出
- `output.mime_type` = "application/json"
- `llm.token_count.prompt` = 输入 token 数
- `llm.token_count.completion` = 输出 token 数
- `llm.token_count.total` = 总 token 数

#### `_get_agent_request_attributes`

新增属性：
- `openinference.span.kind` = "AGENT"
- `input.value` = JSON 序列化的输入
- `metadata.agent_name` = Agent 名称
- `metadata.conversation_id` = 对话 ID

#### `_get_agent_response_attributes`

新增属性：
- `output.value` = JSON 序列化的输出
- `output.mime_type` = "application/json"

#### `_get_tool_request_attributes`

新增属性：
- `openinference.span.kind` = "TOOL"
- `input.value` = JSON 序列化的输入
- `metadata.tool_name` = 工具名称
- `metadata.conversation_id` = 对话 ID

#### `_get_tool_response_attributes`

新增属性：
- `output.value` = JSON 序列化的输出
- `output.mime_type` = "application/json"

---

## 5. OpenInference 语义约定

### 5.1 Span Kind

Phoenix 使用 `openinference.span.kind` 属性来区分 span 类型：

| 值 | 说明 |
|----|------|
| `LLM` | LLM 模型调用 |
| `AGENT` | Agent 调用 |
| `TOOL` | 工具执行 |
| `EMBEDDING` | Embedding 操作 |
| `CHAIN` | 链式调用 |

### 5.2 Input/Output

Phoenix 期望输入输出使用以下属性：

```
input.value     - 输入内容（字符串）
input.mime_type - 输入 MIME 类型（通常为 "application/json"）
output.value    - 输出内容（字符串）
output.mime_type - 输出 MIME 类型（通常为 "application/json"）
```

### 5.3 Token 统计

Phoenix 的 "cumulative tokens" 列使用以下属性：

```
llm.token_count.prompt     - 输入 token 数
llm.token_count.completion - 输出 token 数
llm.token_count.total      - 总 token 数
```

这些属性会在 span 层级聚合显示。

### 5.4 Metadata

Phoenix 的 "metadata" 列显示所有以 `metadata.` 为前缀的属性：

```
metadata.model          - 模型名称
metadata.provider       - 提供商名称
metadata.agent_name     - Agent 名称
metadata.tool_name      - 工具名称
metadata.conversation_id - 对话 ID
```

---

## 6. 属性映射对照表

| Phoenix UI 列 | OpenInference 属性 | 值来源 |
|--------------|-------------------|-------|
| kind | `openinference.span.kind` | "LLM" / "AGENT" / "TOOL" |
| input | `input.value` | JSON 序列化的输入参数 |
| output | `output.value` | JSON 序列化的输出结果 |
| metadata | `metadata.*` | model, provider, agent_name 等 |
| cumulative tokens | `llm.token_count.prompt/completion/total` | ChatResponse.usage |

---

## 7. 配置示例

```python
import agentscope

agentscope.init(
    project="my_project",
    # Phoenix tracing 端点
    tracing_url="http://localhost:6006/v1/traces",
    # AS Studio 端点（可选，两者可同时启用）
    studio_url="http://localhost:3000",
)
```

两个后端可以同时工作，不会互相影响。

---

## 8. 验证方法

启动 Phoenix 后，运行 AgentScope 程序，在 Phoenix UI 中检查：

1. **kind 列**：应显示 "LLM"、"AGENT" 或 "TOOL"
2. **input 列**：点击可查看 JSON 格式的输入
3. **output 列**：点击可查看 JSON 格式的输出
4. **metadata 列**：应显示 model、provider 等信息
5. **cumulative tokens 列**：LLM span 应显示 token 统计

---

## 9. 相关文件

| 文件 | 说明 |
|-----|------|
| `tracing/_attributes.py` | 属性常量定义 |
| `tracing/_extractor.py` | 属性提取函数 |
| `tracing/_setup.py` | TracerProvider 配置 |
| `__init__.py` | agentscope.init() 入口 |

---

## 10. 参考链接

- [OpenInference Specification](https://github.com/Arize-ai/openinference/tree/main/spec)
- [Phoenix Documentation](https://docs.arize.com/phoenix)
- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

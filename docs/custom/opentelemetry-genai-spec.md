# OpenTelemetry Generative AI 语义规范详解

本文档详细介绍 OpenTelemetry 为生成式 AI（Generative AI / GenAI）应用提供的语义规范（Semantic Conventions），包括 Spans、Metrics、Events 等各类信号的格式定义。

> 💡 **提示**：当前 OpenTelemetry GenAI 语义规范处于 **Development（开发中）** 状态，尚未完全稳定，可能会有更新。

---

## 一、规范概览

OpenTelemetry 的 GenAI 语义规范旨在统一生成式 AI 场景下的可观测性数据格式，涵盖以下信号类型：

| 信号类型 | 用途 | 典型场景 |
|---------|------|----------|
| **Spans（跨度）** | 追踪操作的执行过程 | 模型推理、Agent 调用、工具执行 |
| **Metrics（度量）** | 量化性能、成本、使用情况 | Token 用量、延迟、耗时统计 |
| **Events（事件）** | 捕获详细的交互内容 | 输入/输出消息、系统指令 |

规范还针对特定提供商（如 OpenAI、Azure AI、AWS Bedrock、Anthropic 等）提供了扩展定义。

---

## 二、通用属性（Attributes）

这些属性可用于 Spans、Events、Metrics，是 GenAI 可观测性的基础字段。

### 2.1 核心属性

| 属性 Key | 类型 | 描述 | 示例值 | 要求级别 |
|----------|------|------|--------|----------|
| `gen_ai.operation.name` | string | 正在执行的操作名称 | `chat`, `text_completion`, `invoke_agent` | Required |
| `gen_ai.provider.name` | string | AI 模型/服务提供商 | `openai`, `aws.bedrock`, `anthropic` | Required |
| `gen_ai.request.model` | string | 请求中指定的模型名称 | `gpt-4`, `claude-3-opus` | Required |
| `gen_ai.response.model` | string | 实际响应的模型名称（可能与请求不同） | `gpt-4-0125-preview` | Recommended |
| `gen_ai.conversation.id` | string | 会话/对话标识符 | `conv_123456` | Conditionally Required |
| `error.type` | string | 操作失败时的错误类型 | `timeout`, `rate_limit_exceeded` | Conditionally Required |

### 2.2 Agent 相关属性

| 属性 Key | 类型 | 描述 | 示例值 |
|----------|------|------|--------|
| `gen_ai.agent.id` | string | Agent 的唯一标识符 | `agent_001` |
| `gen_ai.agent.name` | string | Agent 的名称 | `QuestionAnswerBot` |
| `gen_ai.agent.description` | string | Agent 的描述信息 | `A bot that answers questions` |
| `gen_ai.data_source.id` | string | Agent 使用的数据源标识 | `knowledge_base_1` |

### 2.3 请求参数属性

| 属性 Key | 类型 | 描述 | 示例值 |
|----------|------|------|--------|
| `gen_ai.request.max_tokens` | int | 最大生成 token 数 | `1024` |
| `gen_ai.request.temperature` | float | 采样温度 | `0.7` |
| `gen_ai.request.top_p` | float | 核采样参数 | `0.9` |
| `gen_ai.request.frequency_penalty` | float | 频率惩罚系数 | `0.5` |
| `gen_ai.request.presence_penalty` | float | 存在惩罚系数 | `0.5` |
| `gen_ai.request.choice.count` | int | 请求的输出选项数量 | `1` |

### 2.4 响应属性

| 属性 Key | 类型 | 描述 | 示例值 |
|----------|------|------|--------|
| `gen_ai.response.id` | string | 响应的唯一标识 | `chatcmpl-123abc` |
| `gen_ai.response.finish_reason` | string | 生成结束的原因 | `stop`, `length`, `tool_calls` |
| `gen_ai.usage.input_tokens` | int | 输入 token 数量 | `150` |
| `gen_ai.usage.output_tokens` | int | 输出 token 数量 | `320` |
| `gen_ai.output.type` | string | 输出内容类型 | `text`, `json`, `image` |

### 2.5 内容属性（敏感数据）

以下属性包含实际的输入输出内容，**默认不采集**，需要 opt-in 开启：

| 属性 Key | 类型 | 描述 |
|----------|------|------|
| `gen_ai.input.messages` | JSON array | 输入消息列表（按顺序） |
| `gen_ai.output.messages` | JSON array | 输出消息列表 |
| `gen_ai.system_instructions` | string | 系统指令/提示词 |

消息格式示例：

```json
[
  {
    "role": "system",
    "content": "You are a helpful assistant."
  },
  {
    "role": "user",
    "content": "What is the weather today?"
  },
  {
    "role": "assistant",
    "content": "I don't have access to real-time weather data..."
  }
]
```

---

## 三、Span 类型与规范

Span 代表一次独立的操作单元，OpenTelemetry 为 GenAI 定义了多种 Span 类型。

### 3.1 推理调用 Span（Inference / Client Spans）

用于客户端发起与 GenAI 模型的交互，如生成回答、内容补全等。

| 元素 | 规范要求 |
|------|----------|
| **Span 名称** | `{gen_ai.operation.name} {gen_ai.request.model}`<br>示例：`chat gpt-4` |
| **Span Kind** | `CLIENT`（远程调用）或 `INTERNAL`（同进程内） |
| **必需属性** | `gen_ai.operation.name`, `gen_ai.provider.name` |
| **推荐属性** | `gen_ai.request.model`, `gen_ai.response.model`, `error.type` |

**操作名称预定义值：**

| 值 | 描述 |
|----|------|
| `chat` | 对话/聊天完成 |
| `text_completion` | 文本补全 |
| `generate_content` | 内容生成 |
| `embeddings` | 嵌入向量生成 |

### 3.2 Agent Spans

用于 Agent/框架级别的操作追踪。

#### 3.2.1 创建 Agent Span（create_agent）

| 元素 | 规范要求 |
|------|----------|
| **Span 名称** | `create_agent {gen_ai.agent.name}`<br>示例：`create_agent QuestionBot` |
| **Span Kind** | `CLIENT` |
| **操作名称** | `gen_ai.operation.name = "create_agent"` |
| **必需属性** | `gen_ai.agent.name`, `gen_ai.operation.name` |

#### 3.2.2 调用 Agent Span（invoke_agent）

| 元素 | 规范要求 |
|------|----------|
| **Span 名称** | `invoke_agent {gen_ai.agent.name}` 或仅 `invoke_agent`（名称不可用时） |
| **Span Kind** | `CLIENT`（远程 Agent）或 `INTERNAL`（本地 Agent） |
| **操作名称** | `gen_ai.operation.name = "invoke_agent"` |
| **必需属性** | `gen_ai.operation.name` |
| **推荐属性** | `gen_ai.agent.name`, `gen_ai.conversation.id` |

### 3.3 工具执行 Span（Execute Tool）

用于追踪模型调用外部工具的执行过程。

| 元素 | 规范要求 |
|------|----------|
| **Span 名称** | `execute_tool {gen_ai.tool.name}`<br>示例：`execute_tool calculator` |
| **Span Kind** | `INTERNAL` |
| **操作名称** | `gen_ai.operation.name = "execute_tool"` |
| **必需属性** | `gen_ai.tool.name`, `gen_ai.operation.name` |

**工具相关属性：**

| 属性 Key | 类型 | 描述 |
|----------|------|------|
| `gen_ai.tool.name` | string | 工具名称 |
| `gen_ai.tool.call.id` | string | 工具调用 ID |
| `gen_ai.tool.call.arguments` | string (JSON) | 工具调用参数 |
| `gen_ai.tool.call.result` | string | 工具执行结果 |

### 3.4 Span 层级关系示例

```
invoke_agent QuestionBot (CLIENT)
├── format dashscope (INTERNAL)
├── chat qwen-plus (CLIENT)
│   └── HTTP POST api.dashscope.com (CLIENT)
├── execute_tool web_search (INTERNAL)
│   └── HTTP GET search-api.com (CLIENT)
└── chat qwen-plus (CLIENT)
```

---

## 四、事件规范（Events）

Events 用于捕获 Span 执行过程中的详细交互内容。

### 4.1 推理操作详情事件

**事件名称**：`gen_ai.client.inference.operation.details`

| 属性 Key | 描述 | 要求级别 |
|----------|------|----------|
| `gen_ai.input.messages` | 输入消息列表 | Opt-In |
| `gen_ai.output.messages` | 输出消息列表 | Opt-In |
| `gen_ai.system_instructions` | 系统指令 | Opt-In |
| `gen_ai.request.choice.count` | 请求选项数 | Recommended |
| `gen_ai.request.model` | 请求模型 | Required |

### 4.2 数据格式要求

- **结构化优先**：如果支持复杂属性类型（对象、数组），优先使用结构化格式
- **JSON 序列化**：如果不支持结构化，将 JSON 对象序列化为字符串
- **截断处理**：内容过大时允许截断，并记录引用信息

---

## 五、度量规范（Metrics）

Metrics 用于量化 GenAI 操作的性能、成本和使用情况。

### 5.1 客户端度量

| 指标名称 | 类型 | 单位 | 描述 |
|----------|------|------|------|
| `gen_ai.client.token.usage` | Histogram | `{token}` | 输入/输出 token 使用量 |
| `gen_ai.client.operation.duration` | Histogram | `s` | 操作总耗时 |

**Token 使用度量属性：**

| 属性 Key | 描述 |
|----------|------|
| `gen_ai.operation.name` | 操作名称 |
| `gen_ai.provider.name` | 提供商名称 |
| `gen_ai.request.model` | 请求模型 |
| `gen_ai.token.type` | Token 类型：`input` 或 `output` |

### 5.2 服务端度量

| 指标名称 | 类型 | 单位 | 描述 |
|----------|------|------|------|
| `gen_ai.server.request.duration` | Histogram | `s` | 服务端请求处理时长 |
| `gen_ai.server.time_per_output_token` | Histogram | `s` | 每个输出 token 的生成时间 |
| `gen_ai.server.time_to_first_token` | Histogram | `s` | 首个 token 生成时间（TTFT） |

### 5.3 Histogram Bucket 建议

对于时间类度量，建议使用以下 bucket 边界（秒）：

```
[0.01, 0.02, 0.04, 0.08, 0.16, 0.32, 0.64, 1.28, 2.56, 5.12, 10.24, 20.48, 40.96, 81.92]
```

对于 token 计数，建议使用：

```
[1, 4, 16, 64, 256, 1024, 4096, 16384, 65536, 262144, 1048576]
```

---

## 六、提供商特定规范

OpenTelemetry 为主流 AI 提供商定义了特定的属性和规范扩展。

### 6.1 预定义提供商名称

| 值 | 提供商 |
|----|--------|
| `openai` | OpenAI |
| `azure.ai.openai` | Azure OpenAI Service |
| `azure.ai.inference` | Azure AI Inference |
| `aws.bedrock` | AWS Bedrock |
| `gcp.vertex_ai` | Google Vertex AI |
| `gcp.gen_ai` | Google Generative AI |
| `anthropic` | Anthropic |
| `cohere` | Cohere |

### 6.2 OpenAI 特定属性

| 属性 Key | 描述 |
|----------|------|
| `gen_ai.openai.request.seed` | 用于确定性生成的 seed |
| `gen_ai.openai.request.response_format` | 响应格式（text/json_object） |
| `gen_ai.openai.response.service_tier` | 服务层级 |

---

## 七、隐私与安全考虑

### 7.1 敏感数据处理

以下属性包含敏感信息，**默认不采集**：

- `gen_ai.input.messages`：用户输入内容
- `gen_ai.output.messages`：模型输出内容
- `gen_ai.system_instructions`：系统提示词
- `gen_ai.tool.call.arguments`：工具调用参数
- `gen_ai.tool.call.result`：工具执行结果

### 7.2 Opt-In 机制

- 提供配置选项让用户选择是否采集敏感内容
- 支持内容截断（truncation）以控制数据大小
- 支持外部存储，在 span 中仅记录引用

### 7.3 错误信息要求

`error.type` 属性值应为**低基数**（low-cardinality），避免包含动态信息：

✅ 正确：`rate_limit_exceeded`, `invalid_api_key`, `model_not_found`

❌ 错误：`Rate limit exceeded for user_123`, `Invalid key: sk-xxx...`

---

## 八、实现建议

### 8.1 Span 命名规范

```
# 推理调用
{operation.name} {model}
例：chat gpt-4

# Agent 操作
{operation.name} {agent.name}
例：invoke_agent QuestionBot

# 工具执行
execute_tool {tool.name}
例：execute_tool web_search
```

### 8.2 属性序列化

```python
# 结构化属性（推荐）
span.set_attribute("gen_ai.input.messages", [
    {"role": "user", "content": "Hello"}
])

# JSON 字符串（兼容方案）
import json
span.set_attribute("gen_ai.input.messages", json.dumps([
    {"role": "user", "content": "Hello"}
]))
```

### 8.3 错误记录

```python
try:
    response = model.generate(prompt)
    span.set_status(StatusCode.OK)
except RateLimitError as e:
    span.set_status(StatusCode.ERROR, str(e))
    span.set_attribute("error.type", "rate_limit_exceeded")
    span.record_exception(e)
```

---

## 九、版本与稳定性

| 版本 | 状态 | 说明 |
|------|------|------|
| v1.38.0+ | Development | 当前最新开发版本 |
| v1.36.0 | Development | 基础语义规范版本 |

**Opt-In 配置：**

通过环境变量 `OTEL_SEMCONV_STABILITY_OPT_IN` 可以选择使用实验性版本：

```bash
export OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai
```

---

## 十、参考链接

- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- [Semantic Conventions for GenAI Client Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [Semantic Conventions for GenAI Agent Spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [Semantic Conventions for GenAI Events](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-events/)
- [Semantic Conventions for GenAI Metrics](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/)
- [GenAI Attributes Registry](https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/)

---

## 附录：完整调用链路示例

以下是一个完整的 Agent 调用链路追踪示例：

```
Trace: tr_abc123
│
└─ Span: invoke_agent QuestionBot (CLIENT)
   │  gen_ai.operation.name = "invoke_agent"
   │  gen_ai.agent.name = "QuestionBot"
   │  gen_ai.conversation.id = "conv_456"
   │
   ├─ Span: format dashscope (INTERNAL)
   │  │  gen_ai.operation.name = "format"
   │  │  agentscope.format.target = "dashscope"
   │  │  agentscope.format.count = 3
   │
   ├─ Span: chat qwen-plus (CLIENT)
   │  │  gen_ai.operation.name = "chat"
   │  │  gen_ai.provider.name = "dashscope"
   │  │  gen_ai.request.model = "qwen-plus"
   │  │  gen_ai.response.model = "qwen-plus-0125"
   │  │  gen_ai.usage.input_tokens = 150
   │  │  gen_ai.usage.output_tokens = 85
   │  │
   │  └─ Event: gen_ai.client.inference.operation.details
   │        gen_ai.input.messages = [...]
   │        gen_ai.output.messages = [...]
   │
   ├─ Span: execute_tool web_search (INTERNAL)
   │  │  gen_ai.operation.name = "execute_tool"
   │  │  gen_ai.tool.name = "web_search"
   │  │  gen_ai.tool.call.id = "call_789"
   │  │  gen_ai.tool.call.arguments = '{"query": "weather today"}'
   │  │  gen_ai.tool.call.result = '{"temperature": "25°C"}'
   │
   └─ Span: chat qwen-plus (CLIENT)
      │  gen_ai.operation.name = "chat"
      │  gen_ai.provider.name = "dashscope"
      │  gen_ai.usage.input_tokens = 280
      │  gen_ai.usage.output_tokens = 120
```

**对应的 Metrics 数据：**

```
gen_ai.client.token.usage{
  gen_ai.operation.name="chat",
  gen_ai.provider.name="dashscope",
  gen_ai.request.model="qwen-plus",
  gen_ai.token.type="input"
} = 430

gen_ai.client.token.usage{
  gen_ai.operation.name="chat",
  gen_ai.token.type="output"
} = 205

gen_ai.client.operation.duration{
  gen_ai.operation.name="invoke_agent",
  gen_ai.agent.name="QuestionBot"
} = 3.45s
```

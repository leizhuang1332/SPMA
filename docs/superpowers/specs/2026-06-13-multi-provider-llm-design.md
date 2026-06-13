# 多提供商 LLM 抽象层设计

> 状态：待审查 | 创建：2026-06-13 | 作者：SPMA Team

## 1. 目标

将 SPMA 的 LLM 调用从硬编码 Anthropic 改造为**多提供商、可配置、可运行时热切换**的抽象层。首批支持的提供商：Anthropic、DeepSeek（含 V4 Pro/Flash）、OpenAI、本地 vLLM。

## 2. 背景

### 2.1 现状问题

- [src/spma/llm/clients.py](../../../src/spma/llm/clients.py) 硬编码 `ChatAnthropic` / `AsyncAnthropic`，仅支持 Claude 系列
- 模型名称分散在环境变量和代码默认值中，缺乏统一管理
- L1 降级动作硬编码 `PRIMARY_MODEL = "claude-sonnet"` 和 `FALLBACK_MODEL = "qwen3-8b-local"`
- 新增任何提供商都需要修改核心调用代码

### 2.2 目标能力

1. 支持 Anthropic、DeepSeek、OpenAI、本地 vLLM 及任何 OpenAI 兼容 API
2. 按"角色槽位"路由模型（classification / generation / completeness / fallback）
3. 运行时热切换：通过 API 或 Feature Flag 下发热切换，无需重启
4. 与现有降级系统（L1-L5）、熔断器、审计日志无缝联动

## 3. 整体架构

```
┌─────────────────────────────────────────────────────┐
│  Agent/Graph 调用层                                  │
│  chat(messages, role="generation")                   │
│  get_llm_client(role="classification")               │
└────────────────────────┬────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────┐
│  LLMRouter（单例，全局唯一）                          │
│  - roles: {classification → Provider+Model}          │
│  - set_role() 运行时热切换                            │
│  - 线程安全 + 异步安全                                │
└────┬──────────┬──────────┬──────────┬───────────────┘
     │          │          │          │
┌────▼───┐ ┌───▼────┐ ┌───▼────┐ ┌───▼──────┐
│Anthro- │ │OpenAI- │ │Local   │ │(future)  │
│pic     │ │Compat  │ │vLLM    │ │...       │
│Provider│ │Provider│ │Provider│ │Provider  │
└────────┘ └────────┘ └────────┘ └──────────┘
     │          │          │
     │     ┌────┴────┐     │
     │     │DeepSeek │     │
     │     │OpenAI   │     │
     │     │Any OAI- │     │
     │     │compat   │     │
     │     └─────────┘     │
```

- **Provider**：负责"怎么调用"——封装不同提供商的 API 差异
- **Role**：负责"用什么调用"——命名槽位，映射到 provider + model
- **Router**：中间的映射层，维护 role → (provider, model) 的绑定关系

## 4. 核心组件

### 4.1 Provider 抽象接口

```python
class LLMProvider(ABC):
    """所有 LLM 提供商的统一抽象。"""

    name: str  # "anthropic", "openai_compat", "local_vllm"

    @abstractmethod
    async def chat(self, messages: list[dict], model: str, **kwargs) -> str:
        """异步对话，返回文本响应。"""

    @abstractmethod
    async def ping(self) -> bool:
        """健康检查，用于降级/恢复判断。"""

    @abstractmethod
    def supports_thinking(self) -> bool:
        """是否支持思考链（DeepSeek V4 thinking / Claude extended thinking）。"""

    def get_langchain_client(self, model: str) -> BaseChatModel:
        """返回 LangChain 兼容 ChatModel，供 LangGraph 直接使用。"""
```

### 4.2 内置 Provider 实现

| Provider | 底层 SDK | `chat()` 使用 | `get_langchain_client()` 返回 | 覆盖模型 |
|----------|----------|--------------|------------------------------|----------|
| `AnthropicProvider` | `langchain_anthropic` | `AsyncAnthropic` | `ChatAnthropic` | Claude 系列 |
| `OpenAICompatProvider` | `openai` | `AsyncOpenAI` | `ChatOpenAI` | DeepSeek、OpenAI、任何 OpenAI 兼容 API |
| `LocalVLLMProvider` | `openai` | `AsyncOpenAI`（指向本地 vLLM） | `ChatOpenAI` | Qwen3-8B 等本地模型 |

**关键设计决策**：

- `OpenAICompatProvider` 一份代码覆盖所有 OpenAI 兼容 API——通过构造时传入的 `base_url` + `api_key` 区分不同提供商
- `LocalVLLMProvider` 本质也是 OpenAI 兼容，但默认 `base_url` 指向 `http://vllm.internal:8000/v1`
- `AnthropicProvider` 保留现有实现，只是包装到接口后面
- `thinking: enabled` 的跨提供商映射：
  - **DeepSeek**：发送 `"thinking": {"type": "enabled"}`（DeepSeek V4 thinking mode）
  - **Anthropic**：发送 `thinking={"type": "enabled", "budget_tokens": 2048}`（Claude extended thinking）
  - **OpenAI**：忽略此参数（OpenAI 不支持 thinking mode）
  - **LocalVLLM**：忽略此参数

### 4.3 LLMRouter（路由层）

```python
class LLMRouter:
    """线程安全的 LLM 路由单例。"""

    def __init__(self, config: LLMConfig):
        self._providers: dict[str, LLMProvider] = {}   # name → provider
        self._roles: dict[str, RoleConfig] = {}         # role → (provider, model, kwargs)
        self._lock = threading.RLock()

    async def chat(self, messages, *, role: str | None = None,
                   model: str | None = None, **kwargs) -> str:
        """核心方法：按 role 路由到正确的 provider+model。

        路由优先级：
        1. 如传入 model → 用当前 role 的 provider + 传入的 model
        2. 否则 → 用 role 配置的 provider + model
        3. role 未配置 → 退回 default role
        4. provider 不健康 → 降级到 fallback role
        """

    def set_role(self, role: str, provider: str, model: str, **kwargs) -> None:
        """运行时热切换：原子替换某个 role 的 provider/model 绑定。"""

    def get_role_config(self, role: str) -> RoleConfig:
        """查询当前 role 配置，供审计/调试使用。"""

    def get_langchain_client(self, role: str | None = None) -> BaseChatModel:
        """返回指定 role 对应的 LangChain 客户端，供 LangGraph 使用。"""
```

**线程安全保证**：

- `threading.RLock()` 保护 `_roles` 读写
- `set_role()` 是原子操作：获取锁 → 替换配置 → 释放锁
- `chat()` 读锁期间获取配置快照，随后的 API 调用在锁外进行（不阻塞其他请求）
- 已在执行中的 `chat()` 调用不受 `set_role()` 影响，继续使用旧配置完成

## 5. 配置设计

### 5.1 YAML 配置结构

改造 `config/spma.yaml` 中 `llm` 段：

```yaml
llm:
  providers:
    anthropic:
      type: anthropic
      api_key: "${ANTHROPIC_API_KEY}"
      base_url: "https://api.anthropic.com"
      default_model: "claude-sonnet-4-6"

    deepseek:
      type: openai_compat
      api_key: "${DEEPSEEK_API_KEY}"
      base_url: "https://api.deepseek.com"
      default_model: "deepseek-v4-pro"

    openai:
      type: openai_compat
      api_key: "${OPENAI_API_KEY}"
      base_url: "https://api.openai.com/v1"
      default_model: "gpt-4o"

    local_vllm:
      type: openai_compat
      api_key: "not-needed"
      base_url: "http://vllm.internal:8000/v1"
      default_model: "qwen3-8b-local"

  roles:
    classification:
      provider: deepseek
      model: deepseek-v4-flash
      max_tokens: 2048
      temperature: 0.1

    generation:
      provider: deepseek
      model: deepseek-v4-pro
      max_tokens: 4096
      temperature: 0.3
      thinking: enabled

    completeness:
      provider: deepseek
      model: deepseek-v4-flash
      max_tokens: 1024
      temperature: 0.1

    default:
      provider: deepseek
      model: deepseek-v4-pro

    fallback:
      provider: local_vllm
      model: qwen3-8b-local

  retry:
    max_retries: 3
    multiplier_seconds: 0.5
    max_wait_seconds: 2.0
```

### 5.2 环境变量覆盖

| 环境变量 | 用途 |
|----------|------|
| `SPMA_LLM_ROLE_<NAME>_PROVIDER` | 覆盖指定 role 的 provider |
| `SPMA_LLM_ROLE_<NAME>_MODEL` | 覆盖指定 role 的 model |
| `SPMA_LLM_PROVIDER_<NAME>_API_KEY` | 覆盖指定 provider 的 api_key |
| `SPMA_LLM_PROVIDER_<NAME>_BASE_URL` | 覆盖指定 provider 的 base_url |

**优先级**：环境变量 > YAML 配置 > 代码默认值

环境变量中的 `<NAME>` 使用 role 或 provider 名称的大写形式，以下划线分隔。例如：
- `SPMA_LLM_ROLE_GENERATION_PROVIDER=deepseek`
- `SPMA_LLM_ROLE_CLASSIFICATION_MODEL=deepseek-v4-flash`
- `SPMA_LLM_PROVIDER_DEEPSEEK_API_KEY=sk-xxx`

## 6. 运行时热切换

### 6.1 切换入口

**方式 1：Admin API 端点**

```
POST /api/v1/admin/llm/role/{role_name}
{
    "provider": "anthropic",
    "model": "claude-sonnet-4-6"
}

GET /api/v1/admin/llm/roles
→ 返回所有 role 的当前配置

GET /api/v1/admin/llm/providers
→ 返回所有已注册 provider 及其健康状态
```

**方式 2：Feature Flag 下发**

```yaml
# feature_flags.yaml
llm_role_overrides:
  generation:
    provider: anthropic
    model: claude-sonnet-4-6
```

Feature Flag 刷新时（周期性或事件驱动），自动调用 `router.set_role()` 同步。

### 6.2 切换流程

```
外部触发（API / Feature Flag / 降级系统）
         │
         ▼
  LLMRouter.set_role(role, provider, model)
         │
         ├── 1. 校验 provider 是否已注册
         ├── 2. 获取 RLock，原子替换 role 配置
         ├── 3. 记录审计日志（操作人、时间、from→to）
         ├── 4. 通知降级管理器（如涉及 fallback 切换）
         └── 5. 立即生效：后续 chat() 调用自动走新 provider+model
```

- **零延迟生效**：无重启、无重连，仅修改内存中的配置映射
- **进行中请求不受影响**：已开始的 `chat()` 继续用旧配置完成
- **线程安全**：`threading.RLock` 保证读写正确性

### 6.3 与降级系统的联动

改造 L1 降级动作：

```
L1 触发：当前 generation role 的 provider 连续 N 次 ping 失败
L1 执行：router.set_role("generation", provider="local_vllm", model="qwen3-8b-local")
L1 恢复：router.set_role("generation", provider=<原provider>, model=<原model>)
```

降级系统不再硬编码 `PRIMARY_MODEL` / `FALLBACK_MODEL`，改为动态获取当前 role 配置。

## 7. 错误处理与重试

### 7.1 错误分类

| 错误类型 | 策略 |
|----------|------|
| 429 Rate Limit | 指数退避重试（最多 3 次），读取 `Retry-After` 头 |
| 5xx 服务端错误 | 重试 1 次 → 失败则降级到 fallback role |
| 4xx 客户端错误（除 429） | 不重试，直接抛出，记录审计日志 |
| 连接超时 | 重试 1 次 → 失败则降级到 fallback role |
| 网络错误 | 重试 1 次 → 失败则降级到 fallback role |

### 7.2 重试实现

复用现有 `tenacity` 依赖，在 `LLMRouter.chat()` 中统一处理：

```python
@retry(
    retry=retry_if_exception_type(LLMRateLimitError) | retry_if_exception_type(LLMServiceError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=2.0),
)
```

### 7.3 降级链

```
1. 正常：按 role 配置的 provider+model 调用
2. 可重试错误 → tenacity 重试
3. 重试耗尽 → 自动尝试 fallback role
4. fallback 也失败 → 抛出 LLMUnavailableError
5. 上层（Agent/Graph）捕获 → 返回降级响应或缓存结果
```

## 8. 代码改造范围

### 8.1 新增文件

```
src/spma/llm/
├── router.py                    # LLMRouter 单例 + LLMConfig 数据类
└── providers/
    ├── __init__.py              # 注册表，by_name()
    ├── base.py                  # LLMProvider 抽象基类 + RoleConfig
    ├── anthropic.py             # AnthropicProvider
    ├── openai_compat.py         # OpenAICompatProvider（覆盖 DeepSeek/OpenAI/其他）
    └── local_vllm.py            # LocalVLLMProvider
```

### 8.2 改造文件

| 文件 | 改造内容 |
|------|----------|
| `src/spma/llm/__init__.py` | 从 router 重新导出 `chat`、`get_langchain_client` |
| `src/spma/llm/clients.py` | 标记废弃，改为调用 router |
| `src/spma/config/settings.py` | 增加 `LLMConfig` 从 YAML + 环境变量加载 |
| `src/spma/api/routes/query.py` | `get_default_llm()` → `router.get_langchain_client(role)` |
| `src/spma/api/app.py` | 注册 `/admin/llm/*` 管理端点 |
| `src/spma/agents/sql/generator.py` | 直接 `llm.clients.chat` → `router.chat(role="generation")` |
| `src/spma/agents/sql/verifier.py` | 直接 `llm.clients.chat` → `router.chat(role="completeness")` |
| `src/spma/infrastructure/degradation/actions/l1_llm.py` | 移除硬编码模型名，改为从 router 读取 |
| `config/spma.yaml` | `llm` 段改为新结构 |
| `pyproject.toml` | 无需修改（已有 `openai`、`anthropic`、`langchain-anthropic` 依赖） |

### 8.3 向后兼容

- `src/spma/llm/clients.py` 的 `chat()` 函数签名保持不变，内部委托给 router
- `get_default_llm()` 返回 router 的 default role 对应的 LangChain 客户端
- 环境变量 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` 继续作为 fallback 读取路径

## 9. 测试策略

| 层级 | 测试内容 | 工具 |
|------|---------|------|
| 单元测试 | `LLMConfig` 从 YAML + 环境变量加载 | pytest |
| 单元测试 | `LLMRouter.set_role()` 线程安全、路由正确性 | pytest + threading |
| 单元测试 | 各 Provider 的 `chat()` mock 验证 | pytest + unittest.mock |
| 集成测试 | `OpenAICompatProvider` 对 DeepSeek API 的实际调用 | pytest-asyncio + httpx |
| 集成测试 | 热切换后新请求使用新配置，进行中请求不受影响 | pytest-asyncio |
| 集成测试 | 降级链：主模型故障 → fallback 接管 | pytest + mock |
| E2E | 通过 API 端点切换 role → 验证后续查询使用新模型 | 现有 E2E 框架 |

## 10. 实施阶段

| 阶段 | 内容 | 预计改动量 |
|------|------|-----------|
| Phase 1 | Provider 抽象 + AnthropicProvider + OpenAICompatProvider + 配置加载 | ~400 行 |
| Phase 2 | LLMRouter + role 机制 + 热切换 | ~300 行 |
| Phase 3 | 改造现有代码 + Admin API 端点 + 与降级系统联动 | ~200 行改动 |
| Phase 4 | 测试补充（单元 + 集成 + E2E） | ~400 行 |

总计预计新增/改动约 1300 行代码。

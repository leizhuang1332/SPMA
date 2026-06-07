# API 契约：基础设施契约

> 所属项目：[SPMA 全局概览](designs/SPMA-design-00-global-overview.md)
> 相关设计：[基础设施与运维设计](designs/SPMA-design-06-infrastructure.md)
> 契约边界：**状态存储、缓存、Feature Flags、降级配置、Token 预算、配置管理**
> 版本：1.0

---

## 一、Agent 状态存储契约

### 1.1 三层存储架构

```
┌──────────────────────────────────────────────────────────────┐
│                    Agent 状态存储三层架构                       │
│                                                              │
│  Layer 1: 进程内存（Python dict）                              │
│  ├─ Phase 1+: 单 Agent 循环内的临时状态                        │
│  ├─ 无外部依赖                                                 │
│  └─ Latency: ~0ms                                             │
│                                                              │
│  Layer 2: Redis 热状态（Write-through）                        │
│  ├─ Phase 2+: Agent 间共享状态                                │
│  ├─ TTL: 5min（查询结束后自动过期）                            │
│  └─ Latency: ~1ms                                             │
│                                                              │
│  Layer 3: PostgreSQL 冷 trace（Write-back）                    │
│  ├─ Phase 3+: Agent 执行 trace 完整记录                        │
│  ├─ 查询结束后异步写入，不阻塞 Agent 循环                      │
│  └─ Latency: 异步（不影响查询延迟）                             │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 Redis 热状态 Key 设计

```
Key Pattern: agent:{user_id}:{session_id}:{query_id}:{agent_type}:state

示例:
  agent:user-001:sess-abc:uuid-123:supervisor:state
  agent:user-001:sess-abc:uuid-123:doc:state
  agent:user-001:sess-abc:uuid-123:code:state
  agent:user-001:sess-abc:uuid-123:sql:state
  agent:user-001:sess-abc:uuid-123:synthesis:state

TTL: 300（5 分钟）
写入方式: Write-through（每次状态变更同步写入）
序列化格式: JSON
```

### 1.3 Agent State JSON Schema

```json
{
  "$schema": "spma/agent-state/1.0",
  "type": "object",
  "required": ["round", "confidence", "results", "token_used"],
  "properties": {
    "round": {"type": "integer", "minimum": 0},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    "results": {
      "type": "array",
      "items": {"type": "object"}
    },
    "token_used": {"type": "integer", "minimum": 0},
    "assessment_history": {
      "type": "array",
      "items": {"type": "string"}
    },
    "llm_calls": {"type": "integer", "minimum": 0},
    "latency_ms": {"type": "integer", "minimum": 0},
    "convergence_reason": {"type": "string"},
    "has_exact_match": {"type": "boolean"}
  }
}
```

### 1.4 状态降级路径

```python
from enum import StrEnum

class StateStorageMode(StrEnum):
    REDIS_HOT = "redis_hot"          # 正常：Redis read/write
    PROCESS_MEMORY = "process_memory" # 降级：Redis 不可用 → 进程内存
    POSTGRES_COLD = "postgres_cold"   # Phase 3+: 异步写入 PG

class StateStorageConfig(TypedDict):
    redis_host: str
    redis_port: int                   # 默认 6379
    redis_db: int                     # 默认 0
    redis_ttl_seconds: int            # 默认 300
    redis_max_retries: int            # 默认 3
    redis_retry_backoff_ms: int       # 默认 100
    fallback_to_memory: bool          # 默认 True
```

---

## 二、缓存契约

### 2.1 缓存 Key 设计

| 缓存类型 | Key 模式 | TTL | 说明 |
|---------|---------|-----|------|
| 热点问答 | `cache:qa:{query_hash}` | 1h | 相同问题的缓存回答 |
| 查询结果 | `cache:result:{query_id}` | 5min | 查询结果缓存 |
| LLM 翻译缓存 | `cache:llm_trans:{zh_term}` | 24h | 中文→英文代码标识符翻译 |
| 同义词映射 | `cache:synonym:v{version}` | 刷新时更新 | 同义词映射表全量缓存 |
| 查询扩展 | `cache:qe:{query_hash}` | 24h | 查询扩展词缓存 |
| 文件路径路由 | `cache:top_dirs` | 1h | 顶层目录结构缓存 |
| 仓库注册表 | `cache:repo_registry` | 10min | 仓库元数据 |

### 2.2 缓存写入策略

```python
class CacheWriteStrategy(StrEnum):
    WRITE_THROUGH = "write_through"    # 同步写入缓存+存储
    WRITE_BACK = "write_back"          # 异步写入存储（Agent trace）
    WRITE_AROUND = "write_around"      # 直接写存储，读时填充缓存

# 各场景策略:
# - Agent 状态: Write-through（Redis → 异步 PG）
# - 热点问答: Write-around（首答写入，后续命中缓存）
# - LLM 翻译: Write-through（首次翻译后写入缓存）
# - 同义词映射: 启动时全量加载到进程内存 + Redis 缓存
```

### 2.3 缓存降级

```python
class CacheDegradationConfig(TypedDict):
    """缓存降级配置"""
    max_retries: int                  # 默认 2
    retry_backoff_ms: int             # 默认 50
    fallback_to_stale: bool           # 允许返回过期缓存
    stale_ttl_extension_seconds: int  # 过期缓存额外有效期（默认 300）
    circuit_breaker:                  # v2 引入
        max_failures: int             # 默认 5
        open_duration_seconds: int    # 默认 30
```

---

## 三、Feature Flags 契约

### 3.1 Flag 定义

```yaml
# config/feature_flags.yaml
agents:
  # Agentic 模式开关
  sql_agentic: false          # false=单轮 pipeline, true=多轮语义验证
  doc_agentic: false          # false=单次 BM25+向量, true=完备度判断+多轮
  code_agentic: false         # false=单次 ripgrep+AST, true=完备度判断+多轮
  supervisor_agentic: false   # false=单次分类+规则, true=多轮编排循环
  synth_agentic: false        # false=一次 LLM 生成, true=自检循环

  # 查询改写功能开关
  query_normalization: true   # 标准化（始终开启）
  query_expansion: true       # 查询扩展
  query_decomposition: false  # 分解（Phase 2）
  query_hyde: false           # HyDE（Phase 2）
  query_step_back: false      # 退一步改写（Phase 3+）
  query_context_aware: false  # 上下文感知改写（Phase 3+）

  # 检索增强
  hybrid_search_weighted: false    # 加权混合检索（Phase 2）
  code_fallback: true              # Code Agent 渐进式回退
  sql_user_confirmation: true      # SQL 高风险查询用户确认
  cross_reranker: false            # Cross-encoder Reranker（Phase 3）

  # 降级
  degradation_auto_recovery: true  # 自动恢复（检查间隔 30s）

# 回滚触发条件
rollback_triggers:
  false_confidence_rate_threshold: 0.15   # 虚假信心率 > 15%
  p99_latency_degradation_threshold: 0.30 # P99 延迟恶化 > 30%
  token_cost_degradation_threshold: 0.50  # Token 成本恶化 > 50%
```

### 3.2 Flag 运行时接口

```python
from pydantic import BaseModel

class FeatureFlags(BaseModel):
    """所有 feature flags 的运行时模型"""
    sql_agentic: bool = False
    doc_agentic: bool = False
    code_agentic: bool = False
    supervisor_agentic: bool = False
    synth_agentic: bool = False

    query_normalization: bool = True
    query_expansion: bool = True
    query_decomposition: bool = False
    query_hyde: bool = False
    query_step_back: bool = False
    query_context_aware: bool = False

    hybrid_search_weighted: bool = False
    code_fallback: bool = True
    sql_user_confirmation: bool = True
    cross_reranker: bool = False

    degradation_auto_recovery: bool = True

class FeatureFlagUpdate(BaseModel):
    """Feature Flag 更新请求"""
    flag_name: str
    value: bool
    reason: str                                   # 变更原因（必填，用于审计）
    updated_by: str                               # 操作人

# 管理 API
# GET  /api/v1/admin/feature-flags            — 列出所有 flags
# PUT  /api/v1/admin/feature-flags/{name}     — 更新单个 flag
# GET  /api/v1/admin/feature-flags/history    — flag 变更历史
```

### 3.3 Flag 评估接口

```python
class FeatureFlagService:
    """Feature Flag 服务接口"""

    def is_enabled(self, flag_name: str, context: dict = None) -> bool:
        """
        检查指定 flag 是否启用。

        Args:
            flag_name: flag 名称（如 "doc_agentic"）
            context: 可选的上下文（如 user_id，用于灰度发布）

        Returns:
            bool: flag 是否启用
        """
        ...

    def get_all_flags(self) -> FeatureFlags:
        """获取所有 flag 的当前状态"""
        ...

    def update_flag(self, update: FeatureFlagUpdate) -> bool:
        """
        更新 flag 状态（秒级生效，无需重启）。

        变更记录到审计日志: feature_flags_change_log 表
        """
        ...
```

---

## 四、Token 预算管理契约

### 4.1 预算分配表

```python
class TokenBudgetConfig(TypedDict):
    """Token 预算配置（按查询类型）"""
    budgets: dict[str, int]                       # LLM 调用次数上限

DEFAULT_TOKEN_BUDGETS: TokenBudgetConfig = {
    "budgets": {
        "single_source_simple": 8,                 # 单源简单查询
        "single_source_complex": 12,               # 单源复杂查询
        "cross_source": 20,                        # 跨源查询
        "three_source_full": 25,                   # 三源全查
    }
}
```

### 4.2 预算消耗接口

```python
class TokenBudgetTracker:
    """Token 预算追踪器（跨 Agent 共享）"""

    def consume(self, amount: int, agent_type: str) -> bool:
        """
        从预算中消耗 LLM 调用次数。

        Args:
            amount: 消耗的调用次数
            agent_type: 消耗的 Agent 类型

        Returns:
            bool: 是否还有剩余预算

        Raises:
            TokenBudgetExhausted: 预算耗尽
        """
        ...

    def remaining(self) -> int:
        """返回剩余 LLM 调用次数"""
        ...

    def snapshot(self) -> dict:
        """返回各 Agent 的预算消耗快照"""
        ...

# 使用模式（每个 LLM 调用前检查）:
# if not budget.consume(1, "doc"):
#     raise TokenBudgetExhausted("Doc Agent token budget exhausted")
```

### 4.3 模型成本矩阵

```python
class ModelCostConfig(TypedDict):
    """模型成本配置（用于成本追踪）"""
    models: dict[str, ModelCost]

class ModelCost(TypedDict):
    provider: str                                 # "anthropic" | "local"
    cost_per_1k_input_tokens: float
    cost_per_1k_output_tokens: float
    avg_latency_ms: int

DEFAULT_MODEL_COSTS: ModelCostConfig = {
    "models": {
        "claude-haiku-4-5": {
            "provider": "anthropic",
            "cost_per_1k_input_tokens": 0.001,
            "cost_per_1k_output_tokens": 0.005,
            "avg_latency_ms": 300
        },
        "claude-sonnet-4-6": {
            "provider": "anthropic",
            "cost_per_1k_input_tokens": 0.003,
            "cost_per_1k_output_tokens": 0.015,
            "avg_latency_ms": 800
        },
        "qwen3-8b-local": {
            "provider": "local",
            "cost_per_1k_input_tokens": 0.0,
            "cost_per_1k_output_tokens": 0.0,
            "avg_latency_ms": 500
        }
    }
}
```

---

## 五、降级配置契约

### 5.1 六级降级定义

```python
from typing import Literal

DegradationLevel = Literal["L0", "L1", "L2", "L3", "L4", "L5"]

class DegradationConfig(TypedDict):
    """降级配置"""
    levels: dict[DegradationLevel, DegradationLevelConfig]

class DegradationLevelConfig(TypedDict):
    trigger_conditions: list[str]                 # 触发条件
    actions: list[str]                            # 降级动作
    auto_recovery_check_interval_seconds: int     # 自动恢复检查间隔
    auto_recovery_conditions: list[str]           # 恢复条件

DEGRADATION_CONFIG: DegradationConfig = {
    "levels": {
        "L0": {
            "trigger_conditions": [],
            "actions": ["全功能 5 Agent 多轮循环"],
            "auto_recovery_check_interval_seconds": 0,
            "auto_recovery_conditions": []
        },
        "L1": {
            "trigger_conditions": [
                "主 LLM 超时率 > 10%",
                "主 LLM 5xx 错误率 > 5%"
            ],
            "actions": [
                "切换到备用模型（本地 Qwen3-8B）",
                "完备度判断降级为确定性条件"
            ],
            "auto_recovery_check_interval_seconds": 30,
            "auto_recovery_conditions": [
                "主 LLM 健康检查连续 3 次通过",
                "至少间隔 60s"
            ]
        },
        "L2": {
            "trigger_conditions": [
                "Agent P99 延迟恶化 > 50%",
                "Token 成本恶化 > 100%"
            ],
            "actions": [
                "Agent 通过 feature flag 回退到单轮 pipeline 模式"
            ],
            "auto_recovery_check_interval_seconds": 60,
            "auto_recovery_conditions": [
                "Agent 指标恢复正常"
            ]
        },
        "L3": {
            "trigger_conditions": [
                "向量数据库不可用",
                "向量检索 P99 > 500ms"
            ],
            "actions": [
                "切换纯 BM25 关键词检索"
            ],
            "auto_recovery_check_interval_seconds": 30,
            "auto_recovery_conditions": [
                "向量数据库恢复可用"
            ]
        },
        "L4": {
            "trigger_conditions": [
                "后端检索大面积故障"
            ],
            "actions": [
                "返回 Redis 缓存的热点问答"
            ],
            "auto_recovery_check_interval_seconds": 30,
            "auto_recovery_conditions": [
                "后端检索恢复"
            ]
        },
        "L5": {
            "trigger_conditions": [
                "所有动态服务不可用"
            ],
            "actions": [
                "返回预定义 FAQ + 提示联系管理员"
            ],
            "auto_recovery_check_interval_seconds": 60,
            "auto_recovery_conditions": [
                "系统完全恢复"
            ]
        }
    }
}
```

### 5.2 降级管理 API

```
GET  /api/v1/admin/degradation/status      — 当前降级状态
POST /api/v1/admin/degradation/trigger     — 手动触发降级（运维）
POST /api/v1/admin/degradation/recover     — 手动恢复（运维）
GET  /api/v1/admin/degradation/history     — 降级历史
```

**当前降级状态响应：**

```json
{
  "current_level": "L0",
  "degraded_components": [],
  "active_degradations": [],
  "last_degradation_at": null,
  "last_recovery_at": null,
  "auto_recovery_enabled": true
}
```

---

## 六、熔断器配置（v2 启用）

```python
class CircuitBreakerConfig(TypedDict):
    """熔断器配置（v2 启用）"""
    enabled: bool                                 # v1: false
    failure_threshold: int                        # 连续失败次数阈值（默认 5）
    open_duration_seconds: int                    # 熔断持续时间（默认 30）
    half_open_probe_count: int                   # 半开探测请求数（默认 3）
    half_open_success_threshold: int             # 半开恢复所需成功数（默认 2）
    monitored_services: list[str]                # 监控的服务列表

# LLM 并发控制（v1 启用）
class LLMConcurrencyConfig(TypedDict):
    max_retries: int                              # 最大重试次数（默认 3）
    retry_multiplier_seconds: float               # 退避乘数（默认 0.5）
    max_wait_seconds: float                       # 最大等待（默认 2.0）
    retry_on_status_codes: list[int]              # 触发重试的 HTTP 状态码（[429]）
    fallback_model: str                           # 降级模型（"qwen3-8b-local"）
```

---

## 七、延迟 SLO 配置

```python
class LatencySLOConfig(TypedDict):
    """延迟 SLO 配置"""
    slo: dict[str, dict[str, int]]

LATENCY_SLO: LatencySLOConfig = {
    "slo": {
        "single_source": {
            "p50_ms": 3000,
            "p95_ms": 6000,
            "p99_ms": 8000
        },
        "cross_source": {
            "p50_ms": 6000,
            "p95_ms": 12000,
            "p99_ms": 15000
        },
        "hard_timeout_ms": 10000,                  # 整体硬上限
        "agent_timeouts": {
            "supervisor": 5000,
            "doc": 2000,
            "code": 2000,
            "sql": 3000,
            "synthesis": 2000
        }
    }
}
```

---

## 八、可观测性配置契约

### 8.1 指标暴露

```python
class MetricsConfig(TypedDict):
    """可观测性指标配置"""
    export_format: str                            # "opentelemetry" | "prometheus"
    export_endpoint: str                          # OTLP collector endpoint
    llm_tracing_provider: str                     # "langfuse"

class AgentMetrics(TypedDict):
    """Agent 专用指标"""
    agent_rounds_p99: float
    agent_false_confidence_rate: float
    agent_early_stop_rate: float
    agent_degradation_rate: float
    agent_loop_efficiency: float                  # 第N轮新增 / 第N-1轮
    supervisor_reschedule_rate: float
    supervisor_timeout_rate: float
```

### 8.2 告警阈值

```yaml
# config/alerts.yaml
alerts:
  agent_rounds_p99:
    threshold: "> max_rounds"
    severity: warning
    action: "调整收敛参数"

  agent_false_confidence_rate:
    threshold: "> 0.15"
    severity: critical
    action: "触发回滚 → pipeline 模式"

  agent_early_stop_rate:
    threshold: "> 0.30"
    severity: warning
    action: "检查收敛条件是否过严"

  agent_degradation_rate:
    threshold: "> 0.10"
    severity: critical
    action: "检查基础设施（Redis/LLM）"

  supervisor_timeout_rate:
    threshold: "> 0.05"
    severity: warning
    action: "检查 5s 超时设置或 Worker 延迟"

  llm_error_rate:
    threshold: "> 0.10"
    severity: critical
    action: "触发 L1 降级"

  p99_latency_single_source:
    threshold: "> 8000ms"
    severity: warning
    action: "检查 Worker 检索延迟"

  p99_latency_cross_source:
    threshold: "> 15000ms"
    severity: warning
    action: "检查跨源编排效率"
```

---

## 九、配置管理契约

### 9.1 配置来源优先级

```
1. 环境变量（最高优先级）     — 数据库连接、API Key、Secrets
2. ConfigMap（K8s）           — 非敏感配置
3. YAML 配置文件              — 功能配置、SLO、告警
4. 数据库 feature_flags 表    — 运行时动态开关
5. 代码默认值                 — 兜底
```

### 9.2 主配置文件结构

```yaml
# config/spma.yaml
spma:
  version: "1.2.0"

  # Agent 收敛参数
  agents:
    supervisor:
      max_rounds: 5
      timeout_ms: 5000
      reschedule_max_attempts: 2
    doc:
      max_rounds: 3
      timeout_ms: 2000
      convergence_min_results: 5
    code:
      max_rounds: 3
      timeout_ms: 2000
      convergence_min_results: 3
      max_call_depth: 2
    sql:
      max_rounds: 5
      timeout_ms: 3000
      convergence_row_min: 1
      convergence_row_max: 10000
    synthesis:
      max_rounds: 2
      timeout_ms: 2000
      min_citation_coverage: 0.8

  # LLM 配置
  llm:
    classification_model: "claude-haiku-4-5"
    generation_model: "claude-sonnet-4-6"
    completeness_model: "claude-haiku-4-5"
    fallback_model: "qwen3-8b-local"
    local_model_endpoint: "http://vllm.internal:8000/v1"

  # 连接配置
  connections:
    postgres:
      readonly_replica: "${POSTGRES_READONLY_URL}"
      vector_db: "${PGVECTOR_URL}"
    redis:
      url: "${REDIS_URL}"
      db: 0
    llm_api:
      anthropic_api_key: "${ANTHROPIC_API_KEY}"
      anthropic_base_url: "https://api.anthropic.com"
```

### 9.3 配置热加载

```python
class ConfigReloadRequest(BaseModel):
    """配置热加载请求"""
    config_path: str                              # 如 "agents.doc.max_rounds"
    new_value: str                                # JSON 序列化后的新值
    reason: str
    reload_strategy: Literal["immediate", "graceful"]
```

配置变更通过 feature flag 机制实现秒级生效。`immediate` 模式立即对所有新请求生效；`graceful` 模式等待现有请求完成后切换。

---

## 十、安全配置契约

### 10.1 LLM 脱敏层配置

```python
class DataMaskingConfig(TypedDict):
    """LLM 调用前的数据脱敏配置"""
    presidio_enabled: bool                        # 默认 True
    custom_patterns: list[CustomMaskPattern]      # 自定义脱敏规则
    bypass_for_local_model: bool                  # 本地模型可跳过脱敏

class CustomMaskPattern(TypedDict):
    pattern: str                                  # 正则表达式或 Presidio entity type
    replacement: str                              # 替换为的字符串
    description: str

DEFAULT_MASKING_PATTERNS = [
    {"pattern": "PHONE_NUMBER", "replacement": "<PHONE>", "description": "手机号"},
    {"pattern": "EMAIL_ADDRESS", "replacement": "<EMAIL>", "description": "邮箱"},
    {"pattern": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "replacement": "<IP>", "description": "IP 地址"},
    {"pattern": r"¥\s*\d[\d,]*\.?\d*", "replacement": "<AMOUNT>", "description": "人民币金额"},
    {"pattern": r"\$\s*\d[\d,]*\.?\d*", "replacement": "<AMOUNT>", "description": "美元金额"},
]
```

### 10.2 RBAC 配置（v2 启用）

```yaml
# config/rbac.yaml (v2)
rbac:
  enabled: false
  roles:
    pm:
      can_query: ["doc", "sql"]
      restricted_tables: ["salaries", "payroll"]
      restricted_columns: ["pii.*"]
    developer:
      can_query: ["doc", "code", "sql"]
      restricted_tables: ["salaries"]
    dba:
      can_query: ["sql"]
      can_manage_ingestion: true
    admin:
      can_query: ["doc", "code", "sql"]
      can_manage_ingestion: true
      can_manage_feature_flags: true
```

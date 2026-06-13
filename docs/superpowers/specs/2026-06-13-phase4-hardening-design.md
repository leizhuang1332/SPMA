# Design: Phase 4 生产加固 — 降级体系 + 熔断器

> **日期:** 2026-06-13 | **状态:** 设计完成
> **PRD:** [PRD-05-phase4-production-hardening.md](../../prds/PRD-05-phase4-production-hardening.md)
> **范围:** Task 4.1 六级降级体系 + Task 4.2 熔断器
> **上游设计:** [SPMA-design-06-infrastructure.md](../../designs/SPMA-design-06-infrastructure.md), [API-06-infrastructure-contracts.md](../../API-06-infrastructure-contracts.md)

---

## 一、设计决策总结

| 决策点 | 选择 | 理由 |
|--------|------|------|
| 降级触发机制 | 内建健康检查循环 + 预留 Prometheus webhook | 零外部依赖，快速可用，后续升级不破坏接口 |
| 熔断器集成方式 | `@circuit_breaker` 装饰器 | 侵入性最小，加在现有调用点无需改函数内部 |
| 降级内部架构 | 策略模式（每级独立 `DegradationAction`） | 隔离性好，新增/修改级别只改一个类 |
| 降级 vs 熔断关系 | 分层协作（熔断器单点保护，降级系统全局策略） | 职责清晰，熔断器快挡，降级系统综合判断 |
| 代码组织 | degradation 拆为子包，其他保持单文件 | 与现有 `ingestion/` 组织风格一致 |

---

## 二、整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                     API Layer (FastAPI)                      │
│  GET /health    GET /admin/degradation/status               │
│  POST /admin/degradation/trigger   /recover   /history      │
│  GET /admin/circuit-breakers   POST /.../reset              │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   DegradationManager                         │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │  Trigger     │  │  State       │  │  Recovery        │   │
│  │  健康检查循环 │  │  Machine     │  │  自动恢复检测     │   │
│  │  每30s轮询   │  │  L0↔L5       │  │  逐级恢复         │   │
│  │  + webhook   │  │              │  │                  │   │
│  └──────┬───────┘  └──────┬───────┘  └────────┬─────────┘   │
│         │                 │                    │             │
│         │          ┌──────▼───────┐            │             │
│         │          │   Actions    │◄───────────┘             │
│         │          │  (策略模式)   │                           │
│         │          │ L1 L2 L3 L4 L5                          │
│         │          └──────┬───────┘                           │
└─────────┼─────────────────┼──────────────────────────────────┘
          │                 │
    ┌─────▼─────┐    ┌──────▼──────┐
    │ 健康检查   │    │ 执行降级动作 │
    │ 目标:     │    │ · 切LLM模型  │
    │ · LLM API │    │ · 改feature  │
    │ · Redis   │    │   flag      │
    │ · PGVector│    │ · 切检索模式 │
    │ · ES      │    │ · 启用缓存   │
    └───────────┘    └─────────────┘
                           │
┌──────────────────────────▼──────────────────────────────────┐
│                   CircuitBreaker (装饰器)                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ llm_sonnet   │  │ llm_haiku    │  │ pgvector         │   │
│  │ CLOSED/OPEN/ │  │ CLOSED/OPEN/ │  │ CLOSED/OPEN/     │   │
│  │ HALF_OPEN    │  │ HALF_OPEN    │  │ HALF_OPEN        │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│                                                              │
│  熔断事件 → 审计日志 → DegradationManager 观察（辅助信号）     │
└──────────────────────────────────────────────────────────────┘
```

### 关键交互流程

1. **正常路径**：请求 → API → Agent 循环 → `@circuit_breaker` 装饰的 LLM/检索调用 → 返回结果
2. **熔断触发**：某依赖连续失败 5 次 → 熔断器 OPEN → 快速失败抛 `CircuitBreakerOpenError` → Agent 层捕获走已有降级路径
3. **降级触发**：`DegradationTrigger` 每 30s 轮询 → 发现 LLM 超时率 >10% → 状态机 L0→L1 → `L1LLMDegradation.execute()` 切换模型
4. **信息共享**：熔断器状态变更 → 审计日志记录 → `DegradationManager` 在健康检查周期中独立判断
5. **恢复**：`DegradationRecovery` 定时检查恢复条件 → 满足 → 逐级 `recover()` → 回到 L0

---

## 三、降级子包设计 (`degradation/`)

### 3.1 文件结构

```
src/spma/infrastructure/degradation/
├── __init__.py           # 导出 DegradationManager, DegradationLevel
├── manager.py            # 编排层：状态机 + 事件总线 (~150行)
├── actions/
│   ├── __init__.py
│   ├── base.py           # DegradationAction 抽象基类 (~40行)
│   ├── l1_llm.py         # L1: LLM 切换 Sonnet→Qwen3-8B (~60行)
│   ├── l2_agent.py       # L2: Agent→pipeline 模式 (~50行)
│   ├── l3_retrieval.py   # L3: 向量检索→纯BM25 (~50行)
│   ├── l4_cache.py       # L4: Redis 热点问答缓存兜底 (~60行)
│   └── l5_static.py      # L5: 静态FAQ兜底 (~40行)
├── trigger.py            # 健康检查循环 + Prometheus webhook 预留 (~100行)
├── recovery.py           # 自动恢复检测 + 逐级恢复逻辑 (~80行)
└── events.py             # 事件定义 (~30行)
```

### 3.2 核心抽象

**DegradationAction 基类：**

```python
class DegradationAction(ABC):
    """单个降级级别的策略基类。"""

    level: DegradationLevel

    @abstractmethod
    async def health_check(self) -> bool:
        """检查该级别依赖是否健康。返回 True=正常。"""
        ...

    @abstractmethod
    async def execute(self, reason: str) -> None:
        """执行降级动作。幂等——重复调用安全。"""
        ...

    @abstractmethod
    async def recover(self) -> bool:
        """尝试恢复。返回 True=恢复成功。"""
        ...

    @abstractmethod
    def recovery_conditions_met(self) -> bool:
        """检查自动恢复条件是否满足（同步，无副作用）。"""
        ...

    @property
    @abstractmethod
    def recovery_check_interval_seconds(self) -> int:
        """恢复检查间隔（秒）。"""
        ...
```

**DegradationManager 编排层：**

```python
class DegradationManager:
    """降级状态机：管理 L0↔L5 切换。"""

    @property
    def current_level(self) -> DegradationLevel: ...
    async def start(self) -> None: ...         # 启动后台检查循环
    async def stop(self) -> None: ...          # 优雅停止
    async def manual_degrade(self, level: DegradationLevel, reason: str) -> None: ...
    async def manual_recover(self) -> None: ...
    async def get_status(self) -> dict: ...    # 管理 API 数据源
    async def get_history(self, limit: int = 50) -> list[DegradationEvent]: ...
```

### 3.3 状态机规则

```
L0 ──→ L1 ──→ L2 ──→ L3 ──→ L4 ──→ L5    (逐级降级，可跳级)
L5 ──→ L4 ──→ L3 ──→ L2 ──→ L1 ──→ L0    (逐级恢复，不可跳级)

高级别降级时叠加低级别动作（L3 = L1动作 + L2动作 + L3动作）
手动降级可跨级（如直接 L0→L3）
恢复必须逐级（每级成功后等一个检查周期再尝试下一级）
```

### 3.4 各级 Action 实现要点

| 级别 | `execute()` | `recover()` | `health_check()` |
|------|-------------|-------------|------------------|
| **L1** | 修改 LLM 路由 Sonnet→Qwen3-8B；完备度判断降级为确定性条件 | 切回 Sonnet | Sonnet API ping 连续 3 次 + 间隔≥60s |
| **L2** | 调用 `FeatureFlagService.update_flag(agent_name, False)` 逐个回退 | 恢复 feature flag 为 True | Agent P99 + Token 成本回落正常范围 |
| **L3** | 修改检索路由，跳过向量检索只走 BM25 | 恢复向量检索路径 | PGVector ping |
| **L4** | 启用 Redis 热点问答缓存作为主读取路径 | 恢复后端检索 | ES/PGVector ping |
| **L5** | 返回预定义 FAQ JSON + 联系管理员提示 | 所有动态服务恢复 | 综合健康检查 |

### 3.5 降级触发器双入口

```python
class DegradationTrigger:
    """降级触发器——双入口：内建轮询 + Prometheus webhook。"""

    # 入口1：内建健康检查循环（asyncio task）
    async def run_loop(self) -> None:
        """每30s轮询所有 action.health_check()，条件满足时回调。"""

    # 入口2：Prometheus AlertManager webhook（预留）
    async def handle_webhook(self, alert: dict) -> None:
        """接收 Prometheus alert，解析后触发降级回调。
        接口格式兼容 AlertManager webhook v4。"""
```

---

## 四、熔断器设计 (`circuit_breaker.py`)

### 4.1 状态机

```
CLOSED (正常)  ── failure_count≥5 ──→  OPEN (熔断, 持续30s)
OPEN ── 30s到期 ──→  HALF_OPEN (探测, 最多3次)
HALF_OPEN ── ≥2次成功 ──→  CLOSED
HALF_OPEN ── <2次成功 ──→  OPEN
```

### 4.2 核心接口

```python
class CircuitBreaker:
    """单个熔断器实例。协程安全（asyncio.Lock）。"""

    def __init__(self, name: str, config: CircuitBreakerConfig | None = None,
                 on_state_change: Callable | None = None): ...

    @property
    def state(self) -> CircuitState: ...
    @property
    def stats(self) -> CircuitBreakerStats: ...

    async def call(self, coro_factory, fallback=None):
        """核心：CLOSED/HALF_OPEN 执行 coro_factory，OPEN 执行 fallback。
        无 fallback 且 OPEN → 抛 CircuitBreakerOpenError。"""
        ...

    async def reset(self) -> None: ...
```

### 4.3 装饰器 API

```python
@circuit_breaker("llm_sonnet")
async def call_sonnet(prompt: str) -> str: ...

@circuit_breaker("pgvector")
async def vector_search(query: str) -> list[dict]: ...

@circuit_breaker("redis")
async def cache_get(key: str) -> str | None: ...
```

全局注册表 `_registry: dict[str, CircuitBreaker]` 按名字管理所有实例，同名幂等返回同一实例。

### 4.4 配置参数

```python
@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5           # 连续失败阈值
    open_duration_seconds: float = 30.0  # 熔断持续时间
    half_open_probe_count: int = 3       # 半开探测请求数
    half_open_success_threshold: int = 2 # 半开恢复所需成功数
```

### 4.5 集成点

| 熔断器实例 | 保护目标 | 装饰位置 |
|-----------|---------|---------|
| `llm_sonnet` | Sonnet API | `llm/clients.py` |
| `llm_haiku` | Haiku API | `llm/clients.py` |
| `pgvector` | 向量检索 | `retrieval/vector_store.py` |
| `redis` | 缓存/状态存储 | `infrastructure/cache.py`, `infrastructure/state_store.py` |
| `elasticsearch` | ES BM25 | `retrieval/es_client.py` |

---

## 五、Feature Flags 实现 (`feature_flags.py`)

### 5.1 核心设计

```python
class FeatureFlagService:
    """秒级生效：读本地内存，写内存→异步Redis。"""

    def __init__(self, defaults: dict[str, bool] | None = None, redis_client=None):
        self._flags: dict[str, bool] = dict(defaults or {})

    def is_enabled(self, flag_name: str, context: dict | None = None) -> bool:
        """O(1) 读取，无 I/O。"""
        return self._flags.get(flag_name, False)

    async def update_flag(self, flag_name: str, value: bool,
                          reason: str, updated_by: str) -> bool:
        """写内存立即生效 → 异步写 Redis → 记录变更日志。"""
```

启动时从 `config/feature_flags.yaml` 加载默认值，注入到 `DegradationManager`。

### 5.2 L2 降级如何使用

`L2AgentDegradation.execute()` 遍历 5 个 Agent 的 flag，对指标异常的 Agent 逐个调用 `update_flag(name, False, ...)`。`recover()` 全部恢复为 True。

---

## 六、审计日志 + 可观测性

### 6.1 审计日志 (`audit.py`)

```python
class AuditLogger:
    """结构化 JSON → stdout（不丢日志）+ 批量异步写 PostgreSQL。"""

    async def log(self, event: AuditEvent) -> None:
        """1. logger.info(JSON) → 2. 入队批量写 PG。非阻塞。"""
```

审计事件类型：`degradation.triggered`, `degradation.recovered`, `degradation.manual`,
`circuit_breaker.open`, `circuit_breaker.close`, `circuit_breaker.half_open`, `feature_flag.changed`。

### 6.2 Metrics (`metrics.py`)

不依赖 Prometheus SDK，暴露 gauge/getter：

```python
@dataclass
class DegradationMetrics:
    current_level: DegradationLevelLabel = "L0"
    degradation_count_total: int = 0
    last_degradation_at: float | None = None
    last_recovery_at: float | None = None
    time_in_current_level_seconds: float = 0.0

    def as_prometheus_gauges(self) -> dict[str, float]:
        """供后续 Prometheus exporter 直接使用。"""
```

### 6.3 管理 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/v1/admin/degradation/status` | 当前降级状态 |
| POST | `/api/v1/admin/degradation/trigger` | 手动触发降级 `{level, reason}` |
| POST | `/api/v1/admin/degradation/recover` | 手动恢复 |
| GET | `/api/v1/admin/degradation/history` | 降级历史 `?limit=50` |
| GET | `/api/v1/admin/circuit-breakers` | 所有熔断器状态 |
| POST | `/api/v1/admin/circuit-breakers/{name}/reset` | 手动重置熔断器 |

管理 API 通过 `api/dependencies.py` 依赖注入 `DegradationManager` 和 circuit breaker registry。

所有管理端点需要在 `api/middleware/auth.py` 中做管理员权限校验（匹配 SSO 的 admin role 或 admin API key）。

---

## 七、测试策略

### 7.1 单元测试

| 测试对象 | 测试内容 | 工具 |
|---------|---------|------|
| CircuitBreaker 状态机 | CLOSED→OPEN→HALF_OPEN→CLOSED 全路径 | pytest |
| CircuitBreaker 装饰器 | 成功计数、失败计数、熔断拒绝 | pytest + Mock |
| 各 DegradationAction | execute/recover/health_check 行为验证 | pytest + Mock |
| DegradationManager | 状态转换、手动降级、历史记录 | pytest |
| DegradationTrigger | 轮询逻辑、webhook 解析 | pytest |
| DegradationRecovery | 恢复条件判断、逐级恢复 | pytest |
| FeatureFlagService | 读写、变更日志、YAML 加载 | pytest |
| AuditLogger | 事件记录、批量写入、序列化 | pytest |

### 7.2 集成测试

| 场景 | 内容 |
|------|------|
| 降级触发 → 执行 → 审计记录 | 完整链路，Mock 健康检查返回值 |
| 熔断器 OPEN → Agent 捕获 `CircuitBreakerOpenError` → 走降级 | LLM 调用路径 |
| Feature flag 秒级生效 | `update_flag()` → `is_enabled()` 立即返回新值 |
| 逐级恢复路径 | L5→L4→L3→L2→L1→L0 |

### 7.3 混沌测试（在 Task 4.4 中）

| 实验 | 注入方式 | 预期行为 |
|------|---------|---------|
| 杀 Redis Pod | `kubectl delete pod` | L2 降级 < 30s |
| 模拟 LLM 超时 | 注入 5s 延迟 | L1 降级 + 熔断器 OPEN |
| 模拟 PGVector 不可用 | iptables DROP | L3 降级纯 BM25 |

---

## 八、与现有代码的关系

### 8.1 需改造的文件

| 文件 | 改造内容 |
|------|---------|
| `src/spma/infrastructure/degradation.py` | 删除，迁移到 `degradation/` 子包 |
| `src/spma/infrastructure/circuit_breaker.py` | 完整实现（~200行） |
| `src/spma/infrastructure/feature_flags.py` | 完整实现（~100行） |
| `src/spma/infrastructure/audit.py` | 完整实现（~120行） |
| `src/spma/infrastructure/cache.py` | 完整实现热点问答缓存（被 L4 使用） |
| `src/spma/infrastructure/metrics.py` | 暴露 gauge/getter |
| `src/spma/api/app.py` | 新增管理 API 端点 |
| `src/spma/api/dependencies.py` | 依赖注入 DegradationManager 等 |
| `src/spma/llm/clients.py` | LLM 调用添加 `@circuit_breaker` 装饰器；L1 模型切换支持 |
| `src/spma/retrieval/vector_store.py` | 添加 `@circuit_breaker("pgvector")` |
| `src/spma/retrieval/es_client.py` | 添加 `@circuit_breaker("elasticsearch")` |

### 8.2 不变更的文件

- `src/spma/infrastructure/state_store.py` — 已完整实现，RedisStateStore 自带降级逻辑
- `src/spma/infrastructure/security.py` — Phase 4.5 才实现 SSO
- 所有 `src/spma/agents/` — 降级是基础设施层，Agent 无感知

### 8.3 新增文件

```
src/spma/infrastructure/degradation/__init__.py
src/spma/infrastructure/degradation/manager.py
src/spma/infrastructure/degradation/actions/__init__.py
src/spma/infrastructure/degradation/actions/base.py
src/spma/infrastructure/degradation/actions/l1_llm.py
src/spma/infrastructure/degradation/actions/l2_agent.py
src/spma/infrastructure/degradation/actions/l3_retrieval.py
src/spma/infrastructure/degradation/actions/l4_cache.py
src/spma/infrastructure/degradation/actions/l5_static.py
src/spma/infrastructure/degradation/trigger.py
src/spma/infrastructure/degradation/recovery.py
src/spma/infrastructure/degradation/events.py
config/feature_flags.yaml
```

---

## 九、验收标准

### 降级体系
- [ ] L1-L5 降级在触发条件满足后 < 30s 自动执行
- [ ] 自动恢复条件满足后 < 60s 自动恢复
- [ ] 降级/恢复事件完整记录到审计日志（stdout + PG）
- [ ] 降级管理 API 正常工作（status/trigger/recover/history）
- [ ] L4 缓存兜底至少有 50 条热点问答
- [ ] 手动降级支持跨级（如 L0→L3 直接跳）
- [ ] 高级别降级时保留低级别降级动作（叠加执行）

### 熔断器
- [ ] 连续 5 次失败 → OPEN → 30s → HALF_OPEN → ≥2/3 成功 → CLOSED
- [ ] HALF_OPEN 下 < 2/3 成功 → 重新 OPEN
- [ ] OPEN 时抛 `CircuitBreakerOpenError`，调用方捕获后可走降级
- [ ] `@circuit_breaker` 装饰器在 LLM/PGVector/Redis/ES 调用路径生效
- [ ] 熔断器统计 + 管理 API 正常

### 集成
- [ ] `CircuitBreaker` 状态变更事件被 `AuditLogger` 记录
- [ ] `DegradationManager` 的健康检查独立于熔断器运行
- [ ] L2 降级通过 `FeatureFlagService` 实现秒级 Agent 回退
- [ ] 系统在无降级时（L0）行为与改造前完全一致

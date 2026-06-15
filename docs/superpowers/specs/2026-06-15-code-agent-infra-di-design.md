# Code Agent 基础设施依赖注入——打通 db_pool → FilePathCache → RipgrepExecutor 完整链路

## Context

`src/spma/api/routes/query.py:163-166` 在构建 Code Agent 图时，三大依赖全部硬编码为 `None`：

```python
g = build_code_agent_graph(
    file_path_cache=None,
    ripgrep_executor=None,
    ast_parser=None,
    llm=llm,
)
```

导致 `router.py:70` 抛出 `AttributeError: 'NoneType' object has no attribute 'list_repos'`。虽然前两个 try/except 静默吞掉了 None 错误，但第三个兜底路由的 `list_repos()` 暴露了问题。更深层的原因是：

- `db_pool` 从未在项目中创建（没有 `asyncpg.create_pool()` 调用）
- `bootstrap.py:init_infrastructure()` 定义了但从未被调用
- `FilePathCache` 和 `RipgrepExecutor` 在生产代码中从未被实例化

本设计打通完整基础设施链路，将硬编码 None 替换为真正的依赖注入。

## Design

### 架构流程

```
app.py startup
  ├─ 读取 spma.yaml → connections.postgres.readonly_replica (DSN)
  ├─ asyncpg.create_pool(dsn) → db_pool
  ├─ FilePathCache(db_pool) → file_path_cache
  │    └─ .list_repos() → ["backend", "frontend"]
  ├─ repo_paths = {name: f"{REPO_BASE}/{name}" for name in repos}
  ├─ RipgrepExecutor(repo_paths) → ripgrep_executor
  └─ ASTParser() → ast_parser
       ↓ 注入到 dependencies.py 全局单例
       ↓
query.py: code worker
  └─ get_file_path_cache() / get_ripgrep_executor() / get_ast_parser()
       ↓
  build_code_agent_graph(file_path_cache, ripgrep_executor, ast_parser, llm)
```

### 关键决策

1. **复用 `dependencies.py` 全局单例模式**——与已有的 `_degradation_manager`/`_feature_flag_service` 一致
2. **`repo_paths` 从 `file_path_cache.list_repos()` 推导**——约定路径 `{REPO_BASE}/{repo_name}`，`REPO_BASE` 默认 `/repos`，可通过环境变量 `SPMA_REPO_BASE` 或配置文件的 `connections.postgres.repo_base` 覆盖
3. **`db_pool` 复用配置文件已有的 DSN**——`connections.postgres.readonly_replica`，与 PGVectorStore 共用同一个 PostgreSQL 实例
4. **启动失败不阻塞**——任一组件初始化失败时优雅降级，不影响应用启动，只在 code worker 被调用时返回降级错误

### 配置变更

`config/spma.yaml` 和 `config/spma.local.yaml` 的 `connections.postgres` 下新增可选字段：

```yaml
connections:
  postgres:
    readonly_replica: "postgresql://spma:spma123@localhost:5433/spma"
    vector_db: "postgresql://spma:spma123@localhost:5433/spma"
    repo_base: "/repos"   # 新增：代码仓库根目录（可选，默认 /repos）
```

支持环境变量 `SPMA_REPO_BASE` 覆盖。

### 依赖注入层 (`dependencies.py`)

新增 4 组 `get_`/`set_`：

| 函数 | 返回类型 | 未初始化行为 |
|---|---|---|
| `get_db_pool()` | `asyncpg.Pool` | raise RuntimeError |
| `get_file_path_cache()` | `FilePathCache` | raise RuntimeError |
| `get_ripgrep_executor()` | `RipgrepExecutor` | raise RuntimeError |
| `get_ast_parser()` | `ASTParser` | raise RuntimeError |

### 初始化函数 (`bootstrap.py`)

新增 `init_code_agent_deps(db_pool, repo_base="/repos")`：
1. 创建 `FilePathCache(db_pool)`
2. 调用 `list_repos()` 获取已注册仓库列表（从 file_path_cache 表）
3. 推导 `repo_paths = {name: f"{repo_base}/{name}" for name in repos}`
4. 创建 `RipgrepExecutor(repo_paths)` 和 `ASTParser()`
5. 注入到 `dependencies.py` 全局单例

### 启动事件 (`app.py`)

扩展现有 `startup_llm_router`，或新增独立 startup handler：
1. 从 yaml 读取 `connections.postgres.readonly_replica` 和 `repo_base`
2. `asyncpg.create_pool(dsn)` 创建 db_pool
3. 调用 `init_code_agent_deps(db_pool, repo_base)`
4. 错误时 logger.warning + 优雅降级（code worker 返回 `worker_not_ready`）

### query.py 变更

`at == "code"` 分支从硬编码 None 改为从 DI 获取：

```python
elif at == "code":
    from spma.api.dependencies import get_file_path_cache, get_ripgrep_executor, get_ast_parser
    try:
        file_path_cache = get_file_path_cache()
        ripgrep_executor = get_ripgrep_executor()
        ast_parser = get_ast_parser()
    except RuntimeError:
        logger.warning("Code Agent 依赖未初始化，跳过 code worker")
        return {"worker_type": "code", "result_count": 0, ...}
```

## Files Changed

| File | Change |
|---|---|
| `config/spma.yaml` | 新增 `connections.postgres.repo_base` |
| `config/spma.local.yaml` | 同上 |
| `src/spma/api/dependencies.py` | 新增 4 组 get/set 函数 + 导入 |
| `src/spma/bootstrap.py` | 新增 `init_code_agent_deps()` |
| `src/spma/api/app.py` | startup 中创建 db_pool 并调用 `init_code_agent_deps()` |
| `src/spma/api/routes/query.py` | code worker 分支从 DI 获取依赖 |

## Edge Cases

- **file_path_cache 表为空**（未 build_cache 过）：`list_repos()` 返回空列表，`repo_paths = {}`，RipgrepExecutor 正常初始化但搜索不到结果，路由返回空候选
- **db_pool 创建失败**：startup 中 try/except，code worker 返回 `worker_not_ready` 错误，不影响 doc/sql worker
- **ASTParser 初始化失败**：概率极低（零外部依赖），但同样 try/except 兜底
- **repo 目录不存在**：RipgrepExecutor 的 `_rg_search` 执行 ripgrep 时会收到非零退出码，降级到下一层搜索策略

## Verification

1. **单元测试**：`dependencies.py` get/set 逻辑——设置后 get 返回正确值，未设置抛出 RuntimeError
2. **集成测试**：复用 `tests/integration/test_code_agent_loop.py` 的 Mock 体系，用真实 `ASTParser` + Mock `FilePathCache`/`RipgrepExecutor` 验证 agent 回路
3. **端到端**：启动 `uv run spma-api`，发送 `sources_hint: ["code"]` 的查询，确认不再报 `AttributeError: 'NoneType' object has no attribute 'list_repos'`
4. **降级验证**：不配置 DSN 时启动，code worker 应返回 `worker_not_ready` 而非崩溃

# Code Agent 路由 & 多轮探索 设计 Spec

> **日期**: 2026-07-01 | **状态**: 设计待实施
> **来源设计**: [SPMA-design-13-industry-research-code-location](../../designs/SPMA-design-13-industry-research-code-location.md)（实施导向浓缩版）
> **范围**: Code Agent 路由准确性（repo_registry 落地 + Stage 0/1/2 三段式路由）+ 多轮探索（CodeExplorer 抽离 + graph.py 薄包装）
> **目标读者**: 实现工程师（开发生）+ Code Reviewer
> **基线代码**: 紧贴 [`router.py`](../../src/spma/agents/code/router.py) / [`completeness.py`](../../src/spma/agents/code/completeness.py) / [`searcher.py`](../../src/spma/agents/code/searcher.py) / [`graph.py`](../../src/spma/agents/code/graph.py) 当前实现

---

## §0 TL;DR

**问题**：当前 Code Agent 路由**几乎全走 fallback**（`code_refs` / `module` 始终空、`repo_registry` 表未落地、所有仓库名中文/英文不通），多轮探索仅有 3 级 L1/L2/L3 收敛且 `RipgrepExecutor` 缺少 `glob_files` / `read_files` 方法。

**方案（5 个设计点）**：

1. **`route_repos` 透传用户查询**——新增 `query` / `repo_registry` / `llm` / `two_stage_threshold` 参数，保留旧路径作兜底
2. **落地 `repo_registry` 表**——DDL + 3 GIN 索引 + pg_trgm 扩展，**取代原 YAML 方案**，DB 单一真相源
3. **Stage 0/1/2 三段式路由**——仓库数 ≤ 5 走单阶段 LLM；> 5 走"pg_trgm 关键词预筛 + LLM 精排"两阶段；`two_stage_threshold=5` 默认 always-on 防 scale 风险
4. **Claude Code 模型驱动实时探索**——7 种收敛模式（5 确定性 + 2 LLM 路径），由 `CodeExplorer` 独立类承载多轮循环
5. **`CodeExplorer` 抽离 + graph.py 薄包装**——3 节点（route / explore / finalize）状态机，多轮循环移出 LangGraph

**实施**：6 个 PR 按序交付（repo_registry 表 → RepoRegistry 类 → route_repos 改造 → Stage 0/1/2 验证 → 多轮探索前置补齐 → CodeExplorer 实施），2-3 周内可全量上线。

---

## §1 Context & Goals

### 1.1 当前 3 个核心问题

| ID  | 问题 | 影响 |
| --- | --- | --- |
| **C1** | `extract_entities` 返回的 `code_refs` / `module` 始终为空（`WorkerEntities` dataclass 实现存在但未在 prompt / pipeline 装配阶段填充） | 所有查询都走 `broad_search` 兜底路由，路由准确率 ≈ 0 |
| **C2** | `route_repos` 用中文模块名直接做 `file_path_cache` 路径匹配；用户说"用户登录"但代码文件名为 `auth.py` / `login.py`（英文） | 中英文不通导致匹配失败 |
| **C3** | `repo_registry` 表 design-03 §3.6 spec 写于 2026-06-13 但**至今未落地**；仓库无中文描述 / 关键词元数据 | LLM 路由无结构化数据可消费 |

### 1.2 设计目标

- **G1**：路由准确率 ≥ **80%**（离线 replay 测试集 ≥ 30 条样本）
- **G2**：仓库数从 6 起就启用两阶段路由（防 scale 风险）
- **G3**：多轮探索收敛模式从 3 级升级到 7 级（5 确定性 + 2 LLM 路径）
- **G4**：graph.py 节点数从 4 减到 3，循环逻辑移出 LangGraph
- **G5**：可观测指标 + fail-fast + 降级路径完整，端到端 P99 ≤ 5s

### 1.3 设计哲学

- **零索引 + 实时搜索**：放弃 RAG / 向量索引，拥抱 ripgrep + LLM 实时决策
- **多轮循环**：单次搜索覆盖不全，靠 Glob→Grep→Read→Refine→Assess 迭代收敛
- **DB 单一真相源**：`repo_registry` 表替代 YAML；`file_path_cache` 继续承担"文件清单"职责
- **两阶段路由**（关键词预筛 + LLM 精排）：避免几百仓库元数据塞 LLM prompt 引发 "lost in the middle" + 成本暴涨

完整设计哲学与业界调研见 design-13 §1 / §3.3 注释。

---

## §2 5 个设计点概览

| #   | 设计点 | 核心内容 | 落地文件 |
| --- | --- | --- | --- |
| 1   | `route_repos` 透传用户查询 | 新增 `query` / `repo_registry` / `llm` / `two_stage_threshold` 参数 | [`router.py`](../../src/spma/agents/code/router.py) |
| 2   | 落地 `repo_registry` 表 | DDL + 3 GIN 索引 + pg_trgm 扩展 + `RepoRegistry` 类 + seed 脚本 | `deployments/docker/migrations/005_repo_registry.sql` + `src/spma/db/repo_registry.py` + `scripts/seed_repo_registry.py` |
| 3   | Stage 0/1/2 三段式路由 | Stage 0 决策 → Stage 1 pg_trgm 预筛 → Stage 2 LLM 精排；阈值松弛 + 降级 | `router.py` + `src/spma/db/repo_registry.py` |
| 4   | Claude Code 实时探索 | 7 种收敛模式（5 确定性 + 2 LLM 路径）；轮次→fallback_layer 映射 | [`completeness.py`](../../src/spma/agents/code/completeness.py) 升级 + `searcher.py` 扩展 |
| 5   | `CodeExplorer` 抽离 + graph.py 薄包装 | 6 阶段方法（refine/glob/grep/read/expand/assess）；3 节点状态机 | `src/spma/agents/code/explorer.py`（新增）+ [`graph.py`](../../src/spma/agents/code/graph.py) 改造 |

---

## §3 数据模型：`repo_registry` 表

### 3.1 DDL（`deployments/docker/migrations/005_repo_registry.sql`）

> **实施时直接执行**。含 `pg_trgm` 扩展 + 3 个 GIN 索引（repo_name / display_name / description），与 `001-004` migration 同目录，alembic 链路兼容。

```sql
-- 005_repo_registry.sql — 仓库元数据注册表（design-13 §3.2 + design-03 §3.6 落地）
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE repo_registry (
    id              SERIAL PRIMARY KEY,
    repo_name       VARCHAR(255) NOT NULL UNIQUE,
    display_name    VARCHAR(255) NOT NULL,              -- 中文名（如 "用户认证服务"）
    description     TEXT NOT NULL,                      -- 中文描述（1-2 句话覆盖核心职责）
    tags            TEXT[] NOT NULL DEFAULT '{}',       -- 关键词数组（中英文，5-10 个）
    repo_url        TEXT,                                -- 用于 clone（来源：config/ingestion.yaml 的 code.repo_urls）
    local_path      TEXT,                                -- 本地路径（如 "/repos/repo_auth"）
    languages       JSONB NOT NULL DEFAULT '[]',         -- 语言列表（如 ["Python", "SQL"]）
    last_indexed_at TIMESTAMPTZ,                         -- 上次索引时间（RepoMetadataMiner 写入）
    enabled         BOOLEAN NOT NULL DEFAULT true,       -- 是否参与路由（软删除标记）
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 加速 list_active_repos 查询
CREATE INDEX idx_repo_registry_enabled ON repo_registry (enabled) WHERE enabled = true;

-- pg_trgm GIN 索引（Stage 1 关键词预筛用）
CREATE INDEX idx_repo_registry_name_trgm        ON repo_registry USING GIN (repo_name        gin_trgm_ops);
CREATE INDEX idx_repo_registry_display_name_trgm ON repo_registry USING GIN (display_name    gin_trgm_ops);
CREATE INDEX idx_repo_registry_description_trgm ON repo_registry USING GIN (description     gin_trgm_ops);

-- 启动期烟雾测试用
COMMENT ON TABLE repo_registry IS '仓库元数据唯一真相源（design-13 §3.2 + design-03 §3.6）';
```

**字段命名约定**（与 design-03 §3.6 spec 对齐）：`display_name`（中文名）/ `description`（中文描述）/ `tags TEXT[]`（关键词数组）。

### 3.2 `RepoRegistry` 类（`src/spma/db/repo_registry.py`）

> **3 个 async 方法** + **启动期 fail-fast** 校验 + **可选降级**。

```python
import logging
import os
import json
from dataclasses import dataclass

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class RepoMeta:
    """仓库元数据 dataclass——从 repo_registry 表行转换。"""
    repo_name: str
    display_name: str
    description: str
    tags: list[str]
    repo_url: str | None = None
    local_path: str | None = None
    languages: list[str] | None = None
    enabled: bool = True


class RepoRegistry:
    def __init__(self, pool: asyncpg.Pool, optional: bool | None = None):
        self._pool = pool
        self._optional = (
            optional
            if optional is not None
            else os.environ.get("MODULE_REGISTRY_OPTIONAL", "false").lower() == "true"
        )
        # fail-fast：表存在 + 至少 1 条 enabled=true 行
        # （optional=True 时豁免，降级到 file_path_cache.list_repos()）
        # 完整实现见 design-13 §3.2.2 _validate_startup()

    async def list_active_repos(self) -> list[RepoMeta]: ...
    async def get_repo_by_name(self, name: str) -> RepoMeta | None: ...
    async def list_repos_by_keyword(
        self, keyword: str, top_k: int = 20, similarity_threshold: float = 0.3,
    ) -> list[RepoMeta]: ...
```

**完整方法签名 + 启动校验 + 降级路径见** [design-13 §3.2.2](../../designs/SPMA-design-13-industry-research-code-location.md#322-reporegistry-类db-查询版)。

### 3.3 失败模式矩阵

| 触发条件 | `optional=False`（默认） | `optional=True` |
| --- | --- | --- |
| `repo_registry` 表不存在 | `RuntimeError` 启动失败 | warn 日志，降级 `file_path_cache.list_repos()` |
| 表存在但 `enabled=true` 0 行 | `RuntimeError` 启动失败 | 同样降级（路由准确率显著下降） |
| `get_repo_by_name` 未命中 | 返回 `None`，路由主路径过滤后降级 `broad_search` | 行为一致 |
| DB 连接失败 | `asyncpg.PostgresError` 抛出 | 同样 raise（避免静默错误） |

### 3.4 seed 脚本（`scripts/seed_repo_registry.py`）

```bash
# 交互式录入（从 config/ingestion.yaml 读仓库 URL）
python scripts/seed_repo_registry.py

# 兼容模式（从已有 YAML 草稿迁移）
python scripts/seed_repo_registry.py --from-yaml ./config/module_manifest.yaml

# 干跑（仅打印 SQL，不执行）
python scripts/seed_repo_registry.py --dry-run
```

**幂等性要求**：重复运行不报错（`ON CONFLICT (repo_name) DO UPDATE`）。

### 3.5 admin API 占位（v1.1 再实现，本期仅占位接口契约）

| Method   | Path                       | 作用                                | Feature Flag              |
| -------- | -------------------------- | --------------------------------- | ------------------------- |
| `POST`   | `/admin/repos`             | 创建仓库元数据                           | `code_repo_admin_enabled` |
| `PATCH`  | `/admin/repos/{repo_name}` | 更新 `description`/`tags`/`enabled` | `code_repo_admin_enabled` |
| `DELETE` | `/admin/repos/{repo_name}` | 软删除（`enabled=false`）              | `code_repo_admin_enabled` |

---

## §4 核心接口契约

### 4.1 `route_repos()` 改造后签名

```python
async def route_repos(
    query: str,                       # 新增：用户原始查询
    entities: dict,                   # 保留：实体信息（可选，仍为辅助）
    file_path_cache,                  # 保留：现有 file_path_cache 实例（用于 fallback）
    repo_registry: RepoRegistry,      # 新增：DB 数据源（主路径）
    llm=None,                         # 新增：可选 LLM（主路径用于精排；缺省时走纯关键词排序）
    max_candidates: int = 5,
    two_stage_threshold: int = 5,     # 新增：仓库数 > 此阈值走两阶段
) -> dict:
    """根据用户查询和实体信息路由到候选仓库。

    Stage 0 决策：
        if len(active_repos) <= two_stage_threshold:
            → 单阶段 LLM 路由（route_method="db_registry_match_single"）
        else:
            → 两阶段：Stage 1 关键词预筛 → Stage 2 LLM 精排（"db_registry_match_two_stage"）

    兜底：exact_file_match / module_lookup / broad_search
    """
```

**返回字段**：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `candidate_repos` | `list[str]` | 候选仓库列表 |
| `route_method` | `str` | `db_registry_match_single` / `db_registry_match_two_stage` / `exact_file_match` / `module_lookup` / `broad_search` |
| `route_confidence` | `str` | `high` / `medium` / `low` |

> ⚠️ **下游消费方必读**：`route_method` 主路径枚举值从原 `db_registry_match` 拆分为 `db_registry_match_single`（仓库数 ≤ 5）与 `db_registry_match_two_stage`（> 5）两个值。`graph.py` 等下游 `switch(route_method)` 处需扩展两个分支。

### 4.2 `list_repos_by_keyword()` SQL 契约

```sql
-- Stage 1 pg_trgm 关键词预筛（RepoRegistry.list_repos_by_keyword()）
SELECT repo_name, display_name, description, tags,
       repo_url, local_path, languages, enabled
FROM repo_registry
WHERE enabled = true
  AND (
      (repo_name      <-> $1) <= $3   -- $3 = 1.0 - similarity_threshold
      OR (display_name <-> $1) <= $3
      OR (description  <-> $1) <= $3
      OR $1 = ANY(tags)              -- tags 精确命中（不受阈值影响）
  )
ORDER BY (
    GREATEST(
        similarity(repo_name, $1),
        similarity(display_name, $1),
        similarity(description, $1)
    )
    + CASE WHEN $1 = ANY(tags) THEN 0.3 ELSE 0 END  -- tags 命中加权
) DESC
LIMIT $2;
```

**阈值松弛机制**（`RepoRegistry.list_repos_by_keyword` 内部）：

```
默认 similarity_threshold=0.3
  ↓ 召回 < 3 条
自动放宽到 0.15 重试一次
  ↓ 仍 < 3 条
兜底全表 ORDER BY id LIMIT top_k（强制 LLM 处理，路由准确率可能下降但不失败）
```

### 4.3 `RipgrepExecutor` 扩展方法

> **状态**：当前 `RipgrepExecutor`（[`searcher.py:15-207`](../../src/spma/agents/code/searcher.py#L15-L207)）仅实现 `search()` / `search_gitlog()` / `_rg_search()`。**`glob_files()`** **和** **`read_files()`** **是新增方法**——`CodeExplorer._glob()` / `_read()` 阶段依赖。

```python
async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
    """Glob 模式匹配，发现目录结构。返回 [{"repo": str, "file_path": str}, ...]"""
    results: list[dict] = []
    for repo_name in candidate_repos:
        repo_path = self._repo_paths.get(repo_name)
        if not repo_path:
            continue
        # 敏感路径黑名单过滤：.env / secrets.* / .git/ / *.pem / *.key
        cmd = ["rg", "--files", "--glob", pattern, repo_path]
        # ... 执行命令并收集结果
        results.append({"repo": repo_name, "file_path": ...})
    return results

async def read_files(self, files: list[dict]) -> list[dict]:
    """读取指定文件内容。返回 [{"repo", "file_path", "content"}, ...]"""
    results: list[dict] = []
    for f in files:
        repo_path = self._repo_paths.get(f["repo"])
        if not repo_path:
            continue
        file_path = os.path.join(repo_path, f["file_path"])
        # 敏感路径黑名单过滤
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
            content = fp.read()
        results.append({"repo": f["repo"], "file_path": f["file_path"], "content": content})
    return results
```

**敏感路径黑名单（必须在 `glob_files` / `read_files` 入口过滤）**：
- `**/.env`、`**/secrets.*`、`**/.git/`、`**/*.pem`、`**/*.key`

### 4.4 `assess_code_completeness()` 升级后契约

> **状态**：当前 [`completeness.py:17-44`](../../src/spma/agents/code/completeness.py#L17-L44) 仅实现 3 级（L1 / L2 / L3）。**v2 升级到 7 种收敛模式** + 新增 3 个参数。

**v2 接口签名**：

```python
async def assess_code_completeness(
    ripgrep_results: list[dict],
    expanded_context: list[dict],
    entities: dict,
    call_depth: int,
    new_files_this_round: int,
    fallback_layer: int,
    *,
    previous_new_files: int = 0,    # 新增：stuck 模式判定必须
    max_files: int = 50,             # 新增：cap_reached 判定
    max_rounds: int = 6,             # 新增：cap_reached 判定
    round: int = 0,                  # 新增：stuck 模式判定（round ≥ 2 守卫）
    llm=None,                        # 已存在：llm 路径分支
) -> CodeCompletenessResult:
    """v2: 7 种收敛模式判定（5 确定性 + 2 LLM 路径）。"""
```

**返回类型**：

```python
class CodeCompletenessResult:
    verdict: str          # "converge" | "expand"（与 LangGraph 节点契约兼容）
    level: str            # 7 种之一（见下表）
    reason: str
```

**7 种 level**（与 design-13 §3.4 / 附录 D 完全对齐）：

| level | 类别 | 触发条件 |
| --- | --- | --- |
| `goal_verified` | 确定性 | `code_refs` 非空 + `total_results ≥ 3` + `fallback_layer = 0` |
| `stuck` | 确定性 | `round ≥ 2` 且 `new_files_this_round = 0` 且 `previous_new_files = 0`（首轮豁免） |
| `regression` | 确定性 | `round_over_round_ratio < 0.5` 且本轮 `total_results` 减少 |
| `diminishing_returns` | 确定性 | 连续两轮 `new_files_rate < 0.10` |
| `cap_reached` | 确定性 | `call_depth ≥ max_rounds` 或 `total_files ≥ max_files` |
| `llm_judged` | LLM 路径 | 5 种确定性全不命中 + LLM 判定 `sufficient` |
| `expand` | LLM 路径 | 5 种确定性全不命中 + LLM 判定 `insufficient`（或 LLM 调用失败兜底） |

### 4.5 `CodeExplorer` 类 API（`src/spma/agents/code/explorer.py` 新增）

> **约 250 行实现**。独立于 LangGraph——可注入 mock 状态做单测。

```python
@dataclass
class ExplorerState:
    """CodeExplorer 内部状态——独立于 LangGraph CodeAgentState。"""
    round: int = 0                          # 当前轮次（1-indexed）
    previous_new_files: int = 0              # 上轮新增文件数（stuck 判定用）
    new_files_this_round: int = 0            # 本轮新增文件数
    search_terms: dict = field(default_factory=dict)
    ripgrep_results: list[dict] = field(default_factory=list)
    expanded_context: list[dict] = field(default_factory=list)
    seen_files: set[tuple[str, str]] = field(default_factory=set)
    fallback_layer: int = 0
    call_depth: int = 0
    convergence: CodeCompletenessResult | None = None


class CodeExplorer:
    def __init__(
        self,
        ripgrep_executor: RipgrepExecutor,
        ast_parser,
        llm,
        on_round_complete: Callable[[ExplorerState], Awaitable[None]] | None = None,
        max_rounds: int = 6,
        max_files: int = 50,
    ): ...

    async def explore(self, graph_state: CodeAgentState) -> CodeAgentState:
        """一次性跑完多轮探索，返回写回的 graph_state。"""
        state = self._init_from_graph_state(graph_state)
        while not self._is_converged():
            await self._run_one_round(state)
            if self._on_round_complete:
                await self._on_round_complete(state)
        return self._write_back_to_graph_state(graph_state, state)

    # 6 阶段方法（顺序固定）
    async def _run_one_round(self, state):
        state.round += 1                              # 1-indexed
        state.call_depth = state.round                # 与 round 同步
        await self._refine_terms(state)               # P3 对策（首轮退化）
        glob_hits = await self._glob(state)
        grep_hits = await self._grep(state)
        read_hits = await self._read(state, glob_hits + grep_hits)  # P2 对策
        await self._expand_via_ast(state)
        await self._assess(state)                     # P1 对策：assess 放最后
```

**3 个关键设计对策**（解决 §3.5.1 列出的 3 个问题）：

| ID  | 问题 | 对策 |
| --- | --- | --- |
| P1  | 若 `assess` 跑在 `expand` 之前，第 1 轮 `new_files_this_round=0, previous_new_files=0` 立即触发 `stuck` 假收敛 | 把 `assess` 移到 `expand` 之后；`stuck` 模式判定加 `round ≥ 2` 守卫 |
| P2  | `RipgrepExecutor` 不显式实现 `glob_files` / `read_files`，多轮循环缺 Glob 和 Read | `CodeExplorer` 显式串联 6 阶段方法；任务 #5 前置补齐这 2 个方法 |
| P3  | `build_search_terms(entities)` 只读 entities 不读 `expanded_context`，违背"每轮精化关键词"机制 | 新增 `_refine_terms()` 阶段：基于上轮 `expanded_context` 调 LLM 重组关键词；首轮 `expanded_context` 为空时退化用 `query + entities` |

### 4.6 graph.py 改造

> **状态**：当前 4 节点（route / search / assess / expand）。**改造为 3 节点薄包装**（route / explore / finalize）。

```python
async def explore_node(state: CodeAgentState) -> dict:
    """薄包装——调用 CodeExplorer.explore() 一次完成。"""
    async def on_round(es: ExplorerState):
        # 钩子：每轮结束发可观测事件
        if progress:
            await progress.publish_step(
                "code_worker", "round_complete",
                f"round={es.round} new_files={es.new_files_this_round} "
                f"converge={es.convergence.level if es.convergence else 'pending'}"
            )
    return await code_explorer.explore(state)


def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 6,            # 默认从 3 升到 6
    timeout_ms: int = 2000,
    progress=None,
) -> StateGraph:
    code_explorer = CodeExplorer(
        ripgrep_executor=ripgrep_executor,
        ast_parser=ast_parser,
        llm=llm,
        on_round_complete=... ,   # 桥接 progress 回调
        max_rounds=max_rounds,
    )
    graph = StateGraph(CodeAgentState)
    graph.add_node("route", route_node)
    graph.add_node("explore", explore_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "explore")
    graph.add_edge("explore", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()
```

**状态机拓扑**（3 节点、3 边）：

```
[route] → [explore] → [finalize] → END
            ↓
        code_explorer.explore()  (内部 while 循环)
            ↓
        on_round_complete callback  →  progress.publish_step()
```

---

## §5 关键 SQL 与代码片段

### 5.1 Stage 0 决策伪代码

```python
async def route_repos(query, entities, file_path_cache, repo_registry, llm=None,
                     max_candidates=5, two_stage_threshold=5):
    # Stage 0: 策略决策
    active_repos = await repo_registry.list_active_repos()
    if len(active_repos) <= two_stage_threshold:
        candidates = active_repos
        route_method = "db_registry_match_single"
    else:
        # Stage 1: pg_trgm 关键词预筛
        candidates = await repo_registry.list_repos_by_keyword(query, top_k=20)
        route_method = "db_registry_match_two_stage"

    # Stage 2: LLM 精排（llm=None 时跳过，直接返回 candidates）
    if llm is None:
        selected = [r.repo_name for r in candidates][:max_candidates]
        return {
            "candidate_repos": selected,
            "route_method": route_method,
            "route_confidence": "medium" if len(selected) > 3 else "high",
        }

    repo_list = "\n".join([
        f"- {r.repo_name}（{r.display_name}）：{r.description}（关键词：{', '.join(r.tags)}）"
        for r in candidates
    ])
    prompt = f"""根据用户查询，选择最相关的代码仓库：

用户查询：{query}

仓库列表：
{repo_list}

请输出 JSON：{{"repo_names": ["仓库名1", "..."], "reason": "..."}}"""

    try:
        resp = await llm.ainvoke(prompt)
        parsed = parse_json(resp.content)
    except Exception:
        return await _fallback_module_lookup(entities, file_path_cache, max_candidates)

    # 过滤掉不在 candidates 中的仓库名（防 LLM 幻觉）
    valid_names = {r.repo_name for r in candidates}
    selected = [n for n in parsed["repo_names"] if n in valid_names][:max_candidates]
    confidence = "high" if len(selected) <= 3 else "medium"

    return {
        "candidate_repos": selected,
        "route_method": route_method,
        "route_confidence": confidence,
    }
```

### 5.2 完整 6 阶段流程（实施时按此实现 `CodeExplorer`）

```
explore(graph_state)
  └─ while not converged:
       1. _refine_terms    ← P3 对策
            ├─ 首轮（state.round == 1 且 expanded_context 为空）→ 退化用 query + entities
            └─ 后续轮 → 调 LLM 基于上轮 expanded_context 重组关键词
       2. _glob            ← P2 对策（ripgrep_executor.glob_files）
       3. _grep            ← ripgrep_executor.search（4 层降级，fallback_layer 0-3）
       4. _read            ← P2 对策（ripgrep_executor.read_files）
       5. _expand_via_ast  ← TreeSitter 引用扩展（增量追加到 seen_files）
       6. _assess          ← P1 对策（assess_code_completeness，7 种 level）
       7. emit on_round_complete
```

**轮次→fallback_layer 映射**：

| 轮次 (round) | fallback_layer | search 模式 | 适用场景 |
| --- | --- | --- | --- |
| 1 | 0 | exact (L0) | 精确词命中，最高信度 |
| 2 | 1 | stem (L1) | 精确词无果，按词干拆分 |
| 3 | 2 | fuzzy (L2) | 词干无果，模糊匹配 |
| ≥ 4 | 3 | llm_retry (L3) | 兜底，调用 LLM 重组关键词 |

### 5.3 错误处理矩阵（6 种失败模式）

| 失败模式 | Explorer 行为 | graph.py 责任 |
| --- | --- | --- |
| LLM 调用超时（`_refine_terms`） | 捕获异常 → `search_terms` 保持上轮值 → 继续 | 记录 `code_explorer_refine_errors_total` |
| `_glob` 全仓库失败 | 返回 `[]`，下一轮继续 | 记录 `code_searcher_timeout_total{op="glob"}` |
| `_grep` 单仓库失败 | 单仓库跳过，不中断整轮 | 同上 `{op="grep"}` |
| `_read` 文件 I/O 失败 | `errors="ignore"` 静默跳过该文件 | 记录 `code_searcher_fail_total{op="read"}` |
| `_assess` LLM 路径失败 | 内部 `except` 兜底为 `expand`（已实现） | 无需额外处理 |
| 达到 `max_rounds` 仍不收敛 | Explorer 返回 `convergence.level="cap_reached"` | 记录 `code_explore_rounds` |

---

## §6 实施步骤（PR 顺序 + DoD）

> **必须按表格行序执行**。`repo_registry` 表与 `RepoRegistry` 类先就绪，路由与探索才有数据可用。

| #   | PR 范围 | 优先级 | 描述 | 验收标准（DoD） |
| --- | --- | --- | --- | --- |
| 1   | `005_repo_registry.sql` migration | P0 | 落地 `repo_registry` 表（与 design-03 §3.6 spec 字段对齐）+ `pg_trgm` 扩展 + 3 个 GIN trigram 索引 | migration 可重放；`alembic upgrade head` 成功；表 + 索引 + COMMENT + pg_trgm 扩展全部创建 |
| 2   | `RepoRegistry` 类 + seed 脚本 | P0 | 从 `asyncpg.Pool` 查询，暴露 `list_active_repos()` / `get_repo_by_name()` / `list_repos_by_keyword(keyword, top_k, similarity_threshold)`；seed 脚本从 `config/ingestion.yaml` 读仓库 URL 并交互式录入 | 单测覆盖：DB 查询正常 / 表不存在 raise / `enabled=true` 0 行 raise / `MODULE_REGISTRY_OPTIONAL=true` 降级 4 种路径；`list_repos_by_keyword` 5 个 case（中文 / 英文 / tags 精确命中 / 阈值松弛 0.3→0.15 / 空查询）；seed 脚本幂等 |
| 3   | `route_repos` 改造 | P0 | 添加 `query` / `repo_registry` / `llm` / `two_stage_threshold`（默认 5）参数；实现 Stage 0/1/2 三段式 | 保留旧路径作为兜底；`route_method` 拆分为 `db_registry_match_single` / `db_registry_match_two_stage`；`repo_registry=None` 时行为完全兼容旧实现（回归测试通过） |
| 4   | Stage 0/1/2 端到端验证 | P0 | 任务 #1-#3 落地后，验证两阶段路由在真实场景下的准确率 | 离线 replay 测试集 ≥ 30 条，路由准确率 ≥ **80%**；覆盖 4 场景：① 仓库数 ≤ 5 走单阶段；② 仓库数 > 5 走两阶段；③ Stage 1 召回 < 3 阈值松弛；④ LLM 返回仓库不在 candidates 中降级 |
| 5   | 多轮探索前置补齐 | P0 | 在 `CodeExplorer` 主任务（任务 #6）前先把 3 个底层能力补齐：① `searcher.py` 新增 `glob_files` / `read_files` 方法；② `completeness.py` 从 3 级升级为 5+2 模式 + 新增 3 个参数；③ `graph.py` 默认 `max_rounds` 由 3 提到 6 | ① `RipgrepExecutor` 暴露 2 个新方法且单测通过；② 7 种 level 枚举各跑通一个 fixture；③ `build_code_agent_graph` 默认 `max_rounds=6` 且保留向后兼容；**3 个改动独立 commit** |
| 6   | `CodeExplorer` 抽离 + graph.py 薄包装 | P0 | 新增 `src/spma/agents/code/explorer.py`（约 250 行），实现 6 阶段方法；改造 `graph.py` 为 3 节点薄包装；解决 P1/P2/P3 三类问题 | Explorer 单测 8 项全过（含 `test_refine_terms_round1_degraded`）；7 种收敛模式各跑通一个 fixture；`on_round_complete` 回调在每轮触发；`previous_new_files` 跨轮正确传递；依赖任务 #5 全部完成 |

**完整 8 项 Explorer 单测矩阵**（与 design-13 §3.5.7 对齐）：

| 测试 | 覆盖点 |
| --- | --- |
| `test_init_from_graph_state` | 从 LangGraph state 正确转换字段 |
| `test_refine_terms_llm_fail` | LLM 超时 search_terms 保持上轮值 |
| `test_refine_terms_round1_degraded` | 第 1 轮 expanded_context 为空时退化用 query+entities，不再调 LLM |
| `test_glob_grep_read_integration` | 3 阶段串联，验证 P2 对策（glob/read 显式调用） |
| `test_assess_after_expand` | 验证 P1 对策：round 1 assess 看到真实 new_files_this_round |
| `test_converge_stuck` | round=2 起连续两轮 0 新文件 → `stuck`（boundary case：round=1 不触发） |
| `test_max_rounds_cap` | round=7（call_depth=7 ≥ max_rounds=6）触发 `cap_reached` |
| `test_callback_invoked` | 每轮结束触发 `on_round_complete` 回调 |

---

## §7 测试策略

### 7.1 单元测试（覆盖率目标 ≥ 85%）

| 模块 | 关键测试点 |
| --- | --- |
| `RepoRegistry` | ① 空 / 缺字段 / 重复 `name` 异常；② 正常加载后 `get_repo_by_name` 命中率 100%；③ `list_repos_by_keyword` 5 个 case（中文 / 英文 / tags 精确命中 / 阈值松弛 / 空查询） |
| `RipgrepExecutor` | ① `glob_files` / `read_files` 各 1 case；② `search` 4 层降级各 1 case；③ timeout 触发 kill 路径；④ 敏感路径黑名单过滤（`.env` / `secrets.*` / `.git/` / `*.pem` / `*.key`） |
| `assess_code_completeness` | 7 种收敛模式各 1 case：`goal_verified` / `stuck` / `regression` / `diminishing_returns` / `cap_reached` / `llm_judged` / `expand` |
| `route_repos` | ① LLM DB 主路径；② LLM 失败降级 `module_lookup`；③ RepoRegistry 返回空 → `broad_search` 兜底；④ Stage 0 决策（仓库数 ≤ 5 走 single）；⑤ Stage 0 决策（仓库数 > 5 走 two_stage）；⑥ LLM 返回仓库名不在 candidates → 过滤降级 |
| `CodeExplorer` | 见 §6 末尾 8 项单测矩阵 |
| `RepoRegistry` 启动期校验 | ① 表不存在 + optional=False → raise；② 0 enabled + optional=False → raise；③ 表不存在 + optional=True → warn 降级；④ 0 enabled + optional=True → warn 降级 |

### 7.2 路由降级覆盖率测试（独立单测，保证降级路径都被覆盖）

| 场景 | 触发方式 | 期望 `route_method` | 期望告警 |
| --- | --- | --- | --- |
| LLM 主路径成功（仓库数 ≤ 5） | mock LLM 返回合法 JSON + `two_stage_threshold=5` | `db_registry_match_single` | 无 |
| LLM 主路径成功（仓库数 > 5） | mock LLM + 6 个 mock 仓库 | `db_registry_match_two_stage` | `code_route_two_stage_results` histogram |
| LLM 主路径超时 | mock LLM `asyncio.TimeoutError` | `module_lookup` | `code_route_fallback_total` |
| LLM 主路径返回 JSON 解析错误 | mock LLM 返回 malformed JSON | `module_lookup` | `code_route_fallback_total` |
| LLM 返回仓库不在 candidates | mock LLM 返回未在 Stage 1 top-K 中的 repo_name | `broad_search` | `code_route_fallback_total` |
| Stage 1 召回 < 3 条 | mock `list_repos_by_keyword` 返回 1 条 | 仍走两阶段（自动放宽阈值 0.3→0.15 重试 / 全表兜底） | `code_route_two_stage_results` histogram |
| `repo_registry` 表为空 + `MODULE_REGISTRY_OPTIONAL=true` | 不执行 seed + 启动降级开关 | `module_lookup`（仅 repo_name 列表） | 启动 warn 日志 + `code_repo_registry_fallback_total{reason="empty"}` |
| Stage 1 SQL 失败 | mock `list_repos_by_keyword` 抛异常 | 降级到单阶段 LLM（用全表） | `code_repo_registry_query_seconds{op="keyword_filter", status="fail"}` |

### 7.3 集成测试（基于 Testcontainers）

- 端到端 fixture：用户查询 → `route_repos` → 多轮探索 → 收敛 → 返回结果
- 离线 replay 测试集：≥ 30 条标注样本（覆盖中英文混合查询、单仓库 / 多仓库命中、模糊查询场景）
- 准确率门槛：路由准确率 ≥ **80%**
- 7 种收敛 level 各跑通一个 fixture

### 7.4 `repo_registry` 数据完整性校验（CI 检查）

`scripts/check_repo_registry_integrity.py` 在 PR 检查阶段（连接 staging DB）必须通过：

| 检查项 | 校验规则 | 失败行为 |
| --- | --- | --- |
| 必填字段非空 | `description = '' OR tags = '{}' OR display_name = ''` 计数 = 0 | PR 检查 fail |
| `description` 长度 | LENGTH(description) BETWEEN 5 AND 500 | PR 检查 fail |
| `tags` 非空数组 | array_length(tags, 1) BETWEEN 1 AND 20 | PR 检查 fail |
| `enabled=true` 仓库数 | ≥ N（环境变量阈值，dev/staging/prod 不同） | PR 检查 fail |
| 与 `file_path_cache` 一致 | `repo_registry.repo_name` ⊆ `SELECT DISTINCT repo_name FROM file_path_cache` | PR 检查 fail |
| `last_indexed_at` 时效 | `last_indexed_at IS NULL OR last_indexed_at > now() - interval '7 days'` | warning |

### 7.5 回滚演练

- 注入 `code_route_llm_latency_seconds` 异常 → 验证 5 分钟内自动触发回滚
- 注入 `repo_registry` 表为空 → 验证 `_validate_startup()` 启动 fail-fast 行为（默认）；或 `MODULE_REGISTRY_OPTIONAL=true` 降级 warn 日志
- 演练频次：每次发版前必须跑通，记录到 release checklist

---

## §8 迁移与灰度

### 8.1 迁移路线图

#### 阶段 0：准备阶段（第 0-1 周）

| 任务 | 描述 | 依赖 |
| --- | --- | --- |
| 提交 `005_repo_registry.sql` | 落地 `repo_registry` 表（DDL + pg_trgm + 3 GIN 索引 + COMMENT） | 无 |
| 实现 `RepoRegistry` 类 | 从 `asyncpg.Pool` 查询，3 个 async 方法，启动期 fail-fast | 无 |
| 编写 `scripts/seed_repo_registry.py` | 从 `config/ingestion.yaml` 读仓库 URL 交互式录入；幂等 | 任务 1 |

#### 阶段 1：路由能力增强（第 1-3 周）

| 任务 | 描述 | 风险 | 回滚方案 |
| --- | --- | --- | --- |
| 修改 `route_repos` 实现 Stage 0/1/2 | 添加 4 个新参数；`route_method` 拆分为两个枚举值；`repo_registry=None` 时完全兼容 | 低 | 保留旧路径作 fallback |
| Stage 1 pg_trgm 关键词预筛 | `list_repos_by_keyword()` 在 3 字段做 trigram 相似度 + tags 精确命中加权 | 低 | Stage 1 SQL 失败降级到单阶段 LLM |
| Stage 2 LLM 精排 | 在 Stage 1 top-20 上调 LLM，过滤不在 candidates 中的仓库名 | 中 | LLM 失败 / 超时降级 `module_lookup`；候选为空降级 `broad_search` |
| A/B 测试 | DB 路由与旧路由并行 | 低 | 通过 `code_route_strategy` feature flag 切换 |

**阶段 1 切换条件**：新路由准确率 ≥ 80%，持续 1 周

#### 阶段 2：探索流程优化（第 3-4 周）

| 任务 | 描述 | 风险 | 回滚方案 |
| --- | --- | --- | --- |
| 实现多轮探索 | 基于现有代码扩展 | 低 | 保留单轮搜索作 fallback |
| 性能优化 | 并行化处理 | 中 | 关闭并行，恢复串行 |

### 8.2 快速滚出（10 分钟内）

| 场景 | 操作 |
| --- | --- |
| 新路由导致严重错误 | `code_route_strategy = fallback`，切换回旧路由逻辑（`file_path_cache.module_lookup`） |
| LLM 服务不可用 | 启用 `MODULE_REGISTRY_OPTIONAL=true` 降级到 `file_path_cache.list_repos()`，关闭 LLM 调用 |
| DB 不可用 | 同上（降级到 `file_path_cache`） |

### 8.3 完全回滚（1 小时内）

| 操作 | 描述 |
| --- | --- |
| 代码回滚 | 回滚 `router.py` 与 `repo_registry.py`（DB 查询版）的修改 |
| Migration 回滚 | `alembic downgrade -1` 删除 `repo_registry` 表 |
| Seed 数据保留 | 不动 `repo_registry` 表中已有数据（保留作为下次重新激活的种子） |

### 8.4 灰度发布策略

> **比例必须单调递增**：内部测试 → 1% → 10% → 50% → 100%。每个阶段最短持续 24 小时，且该阶段 SLO 全部达标才能进入下一阶段。

| 阶段 | 范围 | 比例 | 监控重点 | 阶段准入（必须全部满足） |
| --- | --- | --- | --- | --- |
| 内部测试 | 开发/测试人员 | 0% | 路由准确率、响应时间 | 离线 replay 准确率 ≥ 80% |
| 小流量灰度 | 1% 用户 | 1% | 用户反馈、错误率 | 错误率 < 1%、P99 延迟 < 5s |
| 中流量灰度 | 10% 用户 | 10% | 系统性能、LLM 调用成本 | LLM 调用成功率 ≥ 99%、单查询 LLM 成本 ≤ ¥0.05 |
| 大流量灰度 | 50% 用户 | 50% | 缓存命中率、CPU 负载 | L1 / L2 缓存命中率 ≥ 60% |
| 全量发布 | 100% 用户 | 100% | 全面监控 | 持续 7 天 SLO 全部达标（错误率 < 0.5%、P99 < 3s） |

### 8.5 自动回滚触发器（任一命中即立即回滚到上一阶段）

- 路由错误率 > 5%（5 分钟窗口）
- P99 响应时间 > 10s（5 分钟窗口）
- LLM 5xx 比例 > 1%（5 分钟窗口）
- YAML 加载失败或字段缺失导致 `RepoRegistry` 初始化失败

### 8.6 灰度比例实现机制（feature flag + 用户哈希分流）

```python
# 伪代码：路由层入口根据 feature flag 决定使用哪种路由策略
async def dispatch_route(user_id: str, query: str, ...) -> dict:
    """路由层根据 code_route_strategy 决定 Stage 0/1/2 主路径还是 fallback。"""
    strategy = await feature_flag.get(
        "code_route_strategy",
        default="db_registry_match_two_stage",   # v1 默认（按仓库数自动选 single/two_stage）
    )
    if not _in_rollout_bucket(user_id, rollout_percentage=_current_stage_percentage()):
        strategy = "fallback"  # 走 file_path_cache 的旧路径
    if strategy in ("db_registry_match_single", "db_registry_match_two_stage"):
        return await route_repos(
            query=query,
            entities=...,
            file_path_cache=...,
            repo_registry=...,
            llm=...,
            two_stage_threshold=5,   # v1 默认：仓库数 > 5 自动走两阶段
        )
    else:
        return await route_repos_legacy(entities=..., file_path_cache=...)


def _in_rollout_bucket(user_id: str, rollout_percentage: int) -> bool:
    """用户 ID 哈希 → 0-99 整数；小于 rollout_percentage 即命中灰度。"""
    return (int(hashlib.md5(user_id.encode()).hexdigest(), 16) % 100) < rollout_percentage
```

**关键点**：
- feature flag 值由部署系统写入（环境变量 / 配置中心），无需代码变更即可调整比例
- 用户 ID 哈希保证同一用户在不同阶段始终命中或始终不命中，**避免抖动**
- 与 qr 系统已有的 `qr_weights_history` 思路一致：版本 + 权重快照 + 历史可追溯

---

## §9 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 路由准确性（仓库描述不准确） | 路由错误 | 提供模板化元数据填写指南；定期审核；离线 replay 测试集持续回归 |
| LLM 调用成本 | 多轮探索每轮调 LLM | **LLM 调用上限 = 1（路由）+ 6（每轮 _refine_terms）+ 1（_assess 兜底）= 8 次/查询**；优化策略：① `_refine_terms` 仅在 `_assess` 判定 `expand` 后触发（节省 30% 调用）；② 设置 LLM 响应缓存（query+context 哈希 → refined_terms） |
| 响应时间 | 多轮探索增加延迟 | 限制每轮搜索范围，优先精确匹配；`RipgrepExecutor` 单次 timeout 5s；端到端 P99 SLO 5s |
| 仓库数量增长 | 规模线性扩展 | **v1 已防**：仓库数 > 5 自动走两阶段；扩展路径见附录 B |
| ripgrep subprocess 失败 | 进程崩溃、权限不足 | 单仓库失败仅跳过该仓库；返回非 0/1 退出码时记录 stderr 前 200 字符告警 |
| ripgrep timeout | 大仓库搜索超过 5s 阈值 | terminate → 2s grace → kill 三级兜底；超时计入 `searcher_timeout_total` 指标 |
| 文件 I/O 失败 | 读不到文件 | `errors="ignore"` 静默跳过；敏感路径黑名单过滤 `.env` / `secrets.*` / `.git/` 等 |
| `repo_registry` 表字段缺失 | 新增仓库时未填 description / tags | DB 层 `description NOT NULL` + admin API 写入校验；CI 完整性检查 |
| seed 脚本未执行 | 表为空 → 启动失败或路由准确率显著下降 | `RepoRegistry._validate_startup()` 启动期必 ≥ 1；CI 启动期烟雾测试 |
| admin API 写入失败 | 事务回滚或网络异常 | admin API 端到端事务 + audit log + idempotency key（v1.1 实现） |
| 探索发散 | 多轮探索陷入无效搜索 | `cap_reached` 硬截断；`previous_new_files` 状态维护使 `stuck` / `regression` 模式尽早触发 |
| 敏感文件泄露 | `read_files` 可能读取 `.env` / `credentials` | 读取前过滤路径黑名单；日志脱敏（`code_searcher_fail_total` 不记录文件内容） |

---

## 附录 A：与 design-13 章节对照表

| 本 spec 章节 | design-13 章节 | 内容 |
| --- | --- | --- |
| §0 TL;DR | §0 + §6 关键决策 | 1 段总结 + 5 个 bullet |
| §1 Context & Goals | §1 + §2 | 问题+目标 |
| §2 5 个设计点概览 | §3 全部 | 表格化概览 |
| §3 数据模型 | §3.2.1 + §3.2.2 + §3.2.3 + §3.2.4 | DDL + 类 + seed + 失败矩阵 |
| §4 核心接口契约 | §3.1 + §3.3 + §3.4 + §3.5 | 4 个接口 + 7 收敛模式 |
| §5 关键 SQL 与代码片段 | §3.3 + §3.4 + §3.5 | 实施参考代码 |
| §6 实施步骤 | §5.1 | 6 PR + DoD |
| §7 测试策略 | §7.1 + §7.2 + §7.3 | 单测+集成+CI |
| §8 迁移与灰度 | §10 全部 | 阶段0/1/2 + 灰度比例 |
| §9 风险与缓解 | §8 | 高/中风险 |
| 附录 B 规模化路径 | §4 | v1/v2/v3 简略 |
| 附录 C 可观测指标 | §7.1 | 完整指标 |
| 附录 D 7 种收敛模式 | §3.4 | 实施测试用 |
| 附录 E 降级路径矩阵 | §3.2.4 + §3.5.6 | 错误处理参考 |

---

## 附录 B：规模化路径（v1/v2/v3）

> **v1 决策**：v1 立即实现关键词两阶段路由（pg_trgm 预筛 + LLM 精排），`two_stage_threshold=5` 默认 always-on。原"向量预筛选 > 100 / 模块抽象 > 500"的旧触发条件已被本方案取代。

| 阶段 | Stage 1 实现 | 触发条件（仓库 `enabled=true` 数） | 引入时间 | 接口变更 |
| --- | --- | --- | --- | --- |
| **v1** | pg_trgm 关键词 + tags 精确命中 | **> 5**（默认 always-on） | 本期落地 | `route_repos` 新增 `two_stage_threshold`；`RepoRegistry` 新增 `list_repos_by_keyword()` |
| **v2** | pg_trgm + embedding 混合 RRF | 召回率 < 80%（离线 replay） | 中期按需 | `list_repos_by_keyword()` 加可选 `embedder` 参数 |
| **v3** | 后台 LLM summary + BM25 + embedding 三路 RRF | > 1000 | 远期 | 新增 `list_repos_by_summary()`，两阶段变三阶段 |

**v1 选 pg_trgm 而非向量的第一性原理**：
1. 元数据粒度（≤ 200 字符 + 结构化关键词）vs 代码片段粒度——embedding 对元数据级匹配的边际收益显著低于代码片段
2. pg_trgm 已在 `file_path_cache` 链路就绪，零新基础设施
3. trigram 相似度 deterministic，结果可解释、可单测、可调试
4. pg_trgm 索引 = DB 行 = 始终一致；embedding 需定期重新入库

**关键收益**：v1 起的两阶段架构在 v2 / v3 演进时**仅修改 Stage 1 内部信号叠加**，`route_repos()` 函数签名与 `route_method` 枚举值不变——下游消费者零侵入。

---

## 附录 C：可观测指标清单

> 参考项目已有 `qr_*` 指标命名，新增 `code_*` 指标。

| 指标名 | 类型 | 标签 | 用途 |
| --- | --- | --- | --- |
| `code_route_total` | counter | `route_method` | 各路由路径命中次数（`db_registry_match_single` / `db_registry_match_two_stage` / `exact_file_match` / `module_lookup` / `broad_search`） |
| `code_route_confidence` | counter | `confidence` | 各置信度档位命中次数 |
| `code_route_llm_latency_seconds` | histogram | `route_method` | LLM 路由调用延迟分布 |
| `code_route_total_latency_seconds` | histogram | `route_method` | `route_repos` 端到端延迟分布（P50/P95/P99 SLO） |
| `code_route_accuracy_sample` | counter | `verdict` | 人工标注 / 在线 A/B 评估的样本数 |
| `code_explore_rounds` | histogram | `converge_level` | 探索收敛轮数分布（按 level 区分） |
| `code_explorer_refine_errors_total` | counter | `op` | `_refine_terms` / `_assess` LLM 路径异常次数 |
| `code_searcher_timeout_total` | counter | `op`（search/glob/read） | ripgrep subprocess 超时次数 |
| `code_searcher_fail_total` | counter | `op` | ripgrep 失败次数（非 0/1 退出码 / 文件 I/O 失败） |
| `code_repo_registry_query_seconds` | histogram | `op`（list/get/keyword_filter） | `RepoRegistry` DB 查询耗时与成败 |
| `code_repo_registry_admin_ops_total` | counter | `op`, `status` | admin API 写入次数（`op=create/update/delete`，`status=ok/fail`） |
| `code_repo_registry_fallback_total` | counter | `reason`（table_missing/empty） | `MODULE_REGISTRY_OPTIONAL=true` 降级次数 |
| `code_route_fallback_total` | counter | `from_method`, `to_method` | 路由降级次数（用于统计 LLM 不可用率） |
| **`code_repo_registry_count`** | **gauge** | — | **当前 `enabled=true` 的仓库总数（触发"评估向量预筛选"告警用）** |
| **`code_route_two_stage_seconds`** | **histogram** | `op`（keyword_filter） | **Stage 1 pg_trgm 关键词预筛查询耗时** |
| **`code_route_two_stage_results`** | **histogram** | `op`（keyword_filter） | **Stage 1 关键词预筛召回数分布** |

**告警规则**：

- `code_route_llm_latency_seconds:p99 > 3s`（5 分钟窗口）
- `code_route_total_latency_seconds:p99 > 5s`（5 分钟窗口，端到端兜底）
- `rate(code_searcher_timeout_total[5m]) > 10`（按 op 拆分）
- `rate(code_route_fallback_total{from_method=~"db_registry_match.*"}[5m]) > 50`（DB 路由兜底率超过 10%）
- `rate(code_repo_registry_fallback_total[5m]) > 10`（registry 整体降级率过高）
- `code_repo_registry_admin_ops_total{status="fail"} increase > 0`（admin 写入失败即时告警）
- **`code_repo_registry_count > 50` 持续 1h → warning**（提示评估向量预筛选，触发 v2 升级评估）
- **`code_repo_registry_count > 100` 持续 1h → critical**（强制评估升级到 hybrid RRF）
- **`code_route_two_stage_results:p50 < 3` 持续 24h → warning**（关键词预筛召回率低，提示检查元数据质量）

---

## 附录 D：7 种收敛模式判定表

> 与 design-13 §3.4 / §3.5.4 完全对齐。**7 种 = 5 确定性 + 2 LLM 路径**。

| level | 类别 | 触发条件 | 进入下一轮? |
| --- | --- | --- | --- |
| `goal_verified` | 确定性 | `code_refs` 非空 + `total_results ≥ 3` + `fallback_layer = 0` | 否 → finalize |
| `stuck` | 确定性 | `round ≥ 2` 且 `new_files_this_round = 0` 且 `previous_new_files = 0`（首轮豁免） | 否 → finalize |
| `regression` | 确定性 | `round_over_round_ratio < 0.5` 且本轮 `total_results` 减少 | 否 → finalize |
| `diminishing_returns` | 确定性 | 连续两轮 `new_files_rate < 0.10` | 否 → finalize |
| `cap_reached` | 确定性 | `call_depth ≥ max_rounds` 或 `total_files ≥ max_files` | 否 → finalize |
| `llm_judged` | LLM 路径 | 5 种确定性全不命中 + LLM 判定 `sufficient` | 否 → finalize |
| `expand` | LLM 路径 | 5 种确定性全不命中 + LLM 判定 `insufficient`（或 LLM 调用失败兜底） | 是 → round++ + callback |

**轮次索引约定**：`state.round` 为 **1-indexed**，第 1 轮 round=1（首轮 `previous_new_files=0` 是初始化值而非"上一轮为 0"，因此 `stuck` 在 round=1 不触发）。

**LLM 路径分工**：`llm_judged` 与 `expand` 都是 LLM 路径产生的收敛模式，区别是 LLM 判定 `sufficient`（收敛）还是 `insufficient`（继续）；LLM 调用本身失败时也兜底为 `expand`。

**核心指标定义**：

| 指标 | 计算公式 | 说明 |
| --- | --- | --- |
| `new_files_this_round` | 本轮新增文件数 | 用于判断是否有新发现 |
| `new_files_rate` | `new_files_this_round / total_files`（除零时定义为 0） | 新文件占比，反映探索效率 |
| `round_over_round_ratio` | `new_files_this_round / previous_new_files`（previous=0 时定义为 1） | 轮间新文件数比率，反映趋势 |

**最大搜索限制**（v2 目标）：

- 最大轮数：6 轮（与 `graph.py` 默认 `max_rounds=6` 对齐）
- 最大文件数：50 个
- 每轮最大搜索词：10 个

---

## 附录 E：降级路径矩阵

### E.1 路由层降级（`route_repos`）

| 失败模式 | 降级目标 | route_method |
| --- | --- | --- |
| LLM 调用超时 | `file_path_cache.query_files(module)` | `module_lookup` |
| LLM 返回 JSON 解析错误 | 同上 | `module_lookup` |
| LLM 返回仓库不在 candidates | 过滤后若空 → 全部仓库 | `broad_search` |
| Stage 1 pg_trgm SQL 失败 | 用全表喂 LLM（单阶段） | `db_registry_match_single` |
| Stage 1 召回 < 3 条 | 阈值松弛 0.3→0.15 → 全表兜底 | 仍走两阶段（兜底全表） |
| `repo_registry` 表不存在 + `MODULE_REGISTRY_OPTIONAL=true` | 启动期 warn 降级 | 启动 warn 日志 |
| `get_repo_by_name` 未命中 | 路由主路径过滤后降级 | `broad_search` |

**降级路径优先级**：`db_registry_match_*` → `module_lookup` → `broad_search`

**降级指标**：`code_route_fallback_total{from_method, to_method}` 用于统计 LLM 不可用率（告警阈值见附录 C）。

### E.2 探索层降级（`CodeExplorer`）

| 失败模式 | Explorer 行为 | 告警 |
| --- | --- | --- |
| LLM 调用超时（`_refine_terms`） | `search_terms` 保持上轮值，继续 | `code_explorer_refine_errors_total` |
| `_glob` 全仓库失败 | 返回 `[]`，下一轮继续 | `code_searcher_timeout_total{op="glob"}` |
| `_grep` 单仓库失败 | 单仓库跳过，不中断整轮 | `code_searcher_timeout_total{op="grep"}` |
| `_read` 文件 I/O 失败 | `errors="ignore"` 静默跳过 | `code_searcher_fail_total{op="read"}` |
| `_assess` LLM 路径失败 | 内部 `except` 兜底为 `expand` | 无需额外处理 |
| 达到 `max_rounds` 仍不收敛 | 返回 `convergence.level="cap_reached"` | 记录 `code_explore_rounds` |

### E.3 启动期降级（`RepoRegistry`）

| 触发条件 | `optional=False`（默认） | `optional=True` |
| --- | --- | --- |
| `repo_registry` 表不存在 | `RuntimeError` 启动失败 | warn 日志，降级 `file_path_cache.list_repos()` |
| 表存在但 `enabled=true` 0 行 | `RuntimeError` 启动失败 | 同样降级（路由准确率显著下降） |
| 单条记录 `description` / `tags` 为空 | 不 fail-fast（DB 已校验 NOT NULL） | 行为一致 |
| DB 连接失败 | `asyncpg.PostgresError` 抛出 | 同样 raise（避免静默错误） |

---

> **本 spec 终态**：实施时**直接对照 §6 实施步骤**按 PR 顺序交付；测试时**直接对照 §7 测试策略**写断言；上线时**直接对照 §8 灰度发布**配置 feature flag。详细背景与设计哲学见 [design-13](../../designs/SPMA-design-13-industry-research-code-location.md)。

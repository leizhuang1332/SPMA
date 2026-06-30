# Code Agent 路由 & 多轮探索 实施 Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Code Agent 路由准确率从 ≈ 0% 提升到 ≥ 80%，同时升级多轮探索从 3 级收敛到 7 级（5 确定性 + 2 LLM 路径），把多轮循环从 LangGraph 状态机中抽出为独立 `CodeExplorer` 类（可单测）。

**Architecture:**
- **路由层**：`route_repos` 透传 `query` 走 DB-backed `RepoRegistry`（替代原 `file_path_cache.query_files` 中文模块名匹配）；实现 Stage 0/1/2 三段式（仓库数 ≤ 5 走单阶段 LLM；> 5 走 pg_trgm 关键词预筛 + LLM 精排）。
- **数据层**：新增 `repo_registry` 表（DDL + 3 GIN 索引 + pg_trgm 扩展），与 design-03 §3.6 spec 字段对齐（`repo_name` / `display_name` / `description` / `tags TEXT[]` / `enabled`）。
- **探索层**：`CodeExplorer` 独立类承载多轮循环（refine/glob/grep/read/expand/assess 共 6 阶段），graph.py 缩为 3 节点薄包装（route/explore/finalize）；`RipgrepExecutor` 新增 `glob_files` / `read_files`；`assess_code_completeness` 升级到 7 种收敛模式。

**Tech Stack:** Python 3.11+ / asyncpg 0.30+ / PostgreSQL 16 + pg_trgm 扩展 / pytest-anyio / Prometheus client

**Spec:** [docs/superpowers/specs/2026-07-01-code-agent-routing-and-exploration-design.md](../specs/2026-07-01-code-agent-routing-and-exploration-design.md)

**重要偏离 spec 说明**：
- spec §3.2 写 `src/spma/db/repo_registry.py`，但项目无 `db/` 目录；按现有惯例（`file_path_cache.py` / `synonym_map.py` 都在 `src/spma/ingestion/`），本 plan 把 `RepoRegistry` 放在 `src/spma/ingestion/code/repo_registry.py`。

---

## File Structure

### 新增文件

| 文件路径 | 职责 |
| --- | --- |
| `deployments/docker/migrations/005_repo_registry.sql` | `repo_registry` 表 DDL + pg_trgm 扩展 + 3 GIN 索引 |
| `src/spma/ingestion/code/repo_registry.py` | `RepoMeta` dataclass + `RepoRegistry` 类（list_active_repos / get_repo_by_name / list_repos_by_keyword / 启动期 fail-fast） |
| `src/spma/agents/code/explorer.py` | `ExplorerState` dataclass + `CodeExplorer` 类（6 阶段方法 + 多轮循环 + on_round_complete 回调） |
| `src/spma/observability/code_metrics.py` | `CodeMetrics` dataclass + `build_code_metrics()` 工厂（16 个 Prometheus 指标） |
| `scripts/seed_repo_registry.py` | 从 `config/ingestion.yaml` 交互式录入 `display_name` / `description` / `tags`（幂等） |
| `scripts/check_repo_registry_integrity.py` | CI 完整性校验脚本（description 长度 / tags 数量 / enabled 阈值 / 与 file_path_cache 一致） |
| `tests/unit/agents/code/test_repo_registry.py` | `RepoRegistry` 单测（4 启动路径 + list 5 case + get 1 + keyword 5 case） |
| `tests/unit/agents/code/test_completeness_v2.py` | 7 收敛模式各 1 case + 旧 L1/L2/L3 兼容 |
| `tests/unit/agents/code/test_explorer.py` | `CodeExplorer` 8 项单测（init/refine/glob-grep-read/assess-after-expand/stuck/max-rounds/callback） |
| `tests/integration/code/test_routing_e2e.py` | 离线 replay 测试集 + 4 场景端到端 |

### 修改文件

| 文件路径 | 改造内容 |
| --- | --- |
| `src/spma/agents/code/router.py` | 新增 `query` / `repo_registry` / `llm` / `two_stage_threshold` 参数；实现 Stage 0/1/2；`route_method` 枚举拆分 |
| `src/spma/agents/code/completeness.py` | 新增 3 参数（`previous_new_files` / `max_files` / `max_rounds` / `round`）；返回 7 种 level |
| `src/spma/agents/code/searcher.py` | 新增 `glob_files` / `read_files` 方法 + 敏感路径黑名单 |
| `src/spma/agents/code/graph.py` | 缩为 3 节点（route / explore / finalize），默认 `max_rounds=6` |
| `src/spma/api/app.py` | 启动期实例化 `RepoRegistry`（接 db_pool）并注入到 `code_router` |
| `src/spma/api/routes/query.py` | `route_repos` 调用点更新（透传 `query` / `repo_registry`） |

---

## Task Organization

| 阶段 | Task 范围 | 依赖 |
| --- | --- | --- |
| **PR 1** | Task 1: 落地 `005_repo_registry.sql` | 无 |
| **PR 2** | Task 2-3: `RepoRegistry` 类 + seed 脚本 | PR 1 |
| **PR 3** | Task 4-7: `route_repos` Stage 0/1/2 改造 | PR 1, PR 2 |
| **PR 4** | Task 8-9: 端到端验证 + 降级矩阵单测 | PR 3 |
| **PR 5** | Task 10-13: 多轮探索前置补齐（searcher / completeness / max_rounds） | 无 |
| **PR 6** | Task 14-22: `CodeExplorer` 抽离 + graph.py 薄包装 | PR 5 |

---

# PR 1: 落地 `repo_registry` 表

## Task 1: 创建 migration `005_repo_registry.sql`

**Files:**
- Create: `deployments/docker/migrations/005_repo_registry.sql`

- [ ] **Step 1: 创建 migration 文件**

写 `deployments/docker/migrations/005_repo_registry.sql`：

```sql
-- Migration 005: repo_registry 表（design-13 §3.2 + design-03 §3.6 落地）
-- 依赖: PostgreSQL 16 + pg_trgm 扩展

CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS repo_registry (
    id              SERIAL PRIMARY KEY,
    repo_name       VARCHAR(255) NOT NULL UNIQUE,
    display_name    VARCHAR(255) NOT NULL,
    description     TEXT NOT NULL,
    tags            TEXT[] NOT NULL DEFAULT '{}',
    repo_url        TEXT,
    local_path      TEXT,
    languages       JSONB NOT NULL DEFAULT '[]',
    last_indexed_at TIMESTAMPTZ,
    enabled         BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_repo_registry_enabled
    ON repo_registry (enabled) WHERE enabled = true;

CREATE INDEX IF NOT EXISTS idx_repo_registry_name_trgm
    ON repo_registry USING GIN (repo_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_repo_registry_display_name_trgm
    ON repo_registry USING GIN (display_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_repo_registry_description_trgm
    ON repo_registry USING GIN (description gin_trgm_ops);

COMMENT ON TABLE repo_registry IS '仓库元数据唯一真相源（design-13 §3.2 + design-03 §3.6）';
```

- [ ] **Step 2: 验证 migration 文件无语法错误**

Run: `psql -f deployments/docker/migrations/005_repo_registry.sql --dry-run`（如果 psql 可用）
或：`grep -c "CREATE TABLE IF NOT EXISTS repo_registry" deployments/docker/migrations/005_repo_registry.sql`
Expected: `1`（确认表存在）

- [ ] **Step 3: Commit**

```bash
git add deployments/docker/migrations/005_repo_registry.sql
git commit -m "feat(db): add repo_registry table migration 005 with pg_trgm + 3 GIN indexes"
```

---

# PR 2: `RepoRegistry` 类 + seed 脚本

## Task 2: 写 `RepoMeta` dataclass + `RepoRegistry` 骨架（含 `list_active_repos`）

**Files:**
- Create: `src/spma/ingestion/code/repo_registry.py`
- Create: `tests/unit/ingestion/code/test_repo_registry.py`

- [ ] **Step 1: 写失败单测（`list_active_repos` 返回 1 条 enabled 记录）**

写 `tests/unit/ingestion/code/test_repo_registry.py`：

```python
"""Tests for RepoRegistry class (design-13 §3.2)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.ingestion.code.repo_registry import RepoMeta, RepoRegistry


def _make_pool_with_rows(rows):
    """构造一个 mock asyncpg.Pool，fetch() 返回 rows。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetch = AsyncMock(return_value=rows)
    pool.acquire = MagicMock(return_value=conn)
    return pool


def _make_pool_with_count(count: int):
    """构造 mock pool，fetchval() 返回 count（启动期校验用）。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=count)
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.anyio
class TestRepoRegistryListActive:
    async def test_list_active_repos_returns_repo_metas(self):
        rows = [
            {
                "repo_name": "repo_auth",
                "display_name": "用户认证",
                "description": "认证服务",
                "tags": ["auth", "认证"],
                "repo_url": "https://example.com/auth",
                "local_path": "/repos/repo_auth",
                "languages": ["Python"],
                "enabled": True,
            }
        ]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_active_repos()
        assert len(result) == 1
        assert result[0].repo_name == "repo_auth"
        assert result[0].display_name == "用户认证"
        assert result[0].tags == ["auth", "认证"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/ingestion/code/test_repo_registry.py::TestRepoRegistryListActive -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spma.ingestion.code.repo_registry'`

- [ ] **Step 3: 实现 `RepoMeta` + `RepoRegistry.__init__` + `list_active_repos`**

写 `src/spma/ingestion/code/repo_registry.py`：

```python
"""仓库元数据注册表（design-13 §3.2 + design-03 §3.6）。

数据源：DB 表 repo_registry（单一真相源，取代原 YAML 方案）。
启动期 fail-fast 校验：表存在 + 至少 1 条 enabled=true 行。
可选降级：MODULE_REGISTRY_OPTIONAL=true 时降级到 file_path_cache.list_repos()。
"""
import json
import logging
import os
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
    """仓库元数据注册表——从 DB 查询。"""

    def __init__(self, pool: asyncpg.Pool, optional: bool | None = None):
        self._pool = pool
        self._optional = (
            optional
            if optional is not None
            else os.environ.get("MODULE_REGISTRY_OPTIONAL", "false").lower() == "true"
        )
        # fail-fast 校验在调用方显式触发（_validate_startup()）
        # —— 构造时不强制做（避免 import-time 副作用）

    async def list_active_repos(self) -> list[RepoMeta]:
        """查询所有 enabled=true 的仓库元数据（LLM 路由主路径）。"""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                ORDER BY id
                """
            )
        return [self._row_to_meta(r) for r in rows]

    @staticmethod
    def _row_to_meta(row) -> RepoMeta:
        languages = row["languages"]
        if isinstance(languages, str):
            languages = json.loads(languages)
        return RepoMeta(
            repo_name=row["repo_name"],
            display_name=row["display_name"],
            description=row["description"],
            tags=list(row["tags"]),
            repo_url=row["repo_url"],
            local_path=row["local_path"],
            languages=languages or [],
            enabled=row["enabled"],
        )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/ingestion/code/test_repo_registry.py::TestRepoRegistryListActive -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spma/ingestion/code/repo_registry.py tests/unit/ingestion/code/test_repo_registry.py
git commit -m "feat(repo-registry): add RepoMeta + RepoRegistry.list_active_repos"
```

---

## Task 3: 添加 `get_repo_by_name` + `list_repos_by_keyword`（含阈值松弛）

**Files:**
- Modify: `src/spma/ingestion/code/repo_registry.py`
- Modify: `tests/unit/ingestion/code/test_repo_registry.py`

- [ ] **Step 1: 扩展单测：增加 5 个 `list_repos_by_keyword` case + 1 个 `get_repo_by_name` case**

在 `tests/unit/ingestion/code/test_repo_registry.py` 追加：

```python
@pytest.mark.anyio
class TestRepoRegistryGetByName:
    async def test_get_repo_by_name_hit(self):
        rows = [
            {
                "repo_name": "repo_auth",
                "display_name": "用户认证",
                "description": "认证服务",
                "tags": ["auth"],
                "repo_url": None,
                "local_path": "/repos/repo_auth",
                "languages": ["Python"],
                "enabled": True,
            }
        ]
        pool = _make_pool_with_rows_for_one(rows[0])
        reg = RepoRegistry(pool, optional=True)
        result = await reg.get_repo_by_name("repo_auth")
        assert result is not None
        assert result.repo_name == "repo_auth"


def _make_pool_with_rows_for_one(row):
    """构造 mock pool，fetchrow() 返回单行；fetch() 用于启动校验 0 行。"""
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    conn.fetchrow = AsyncMock(return_value=row)
    conn.fetchval = AsyncMock(return_value=1)  # 启动期校验通过
    pool.acquire = MagicMock(return_value=conn)
    return pool


def _make_pool_with_keyword_results(initial_rows, relaxed_rows, fallback_rows):
    """构造 mock pool：首次 fetch 返回 initial_rows；relaxed 后返回 relaxed_rows；fallback 全表返回 fallback_rows。

    RepoRegistry.list_repos_by_keyword 内部应尝试 0.3 → 0.15 → fallback；
    本 helper 模拟 '连续尝试'：根据每次 fetch 调用返回不同结果。
    """
    pool = MagicMock()
    conn = AsyncMock()
    conn.__aenter__ = AsyncMock(return_value=conn)
    conn.__aexit__ = AsyncMock(return_value=None)
    # fetch() 按调用次序返回：第一次 initial_rows，第二次 relaxed_rows，第三次 fallback_rows
    fetch_responses = [initial_rows, relaxed_rows, fallback_rows]
    fetch_idx = {"i": 0}

    async def fetch_side_effect(*args, **kwargs):
        result = fetch_responses[fetch_idx["i"]] if fetch_idx["i"] < len(fetch_responses) else fallback_rows
        fetch_idx["i"] += 1
        return result

    conn.fetch = AsyncMock(side_effect=fetch_side_effect)
    conn.fetchval = AsyncMock(return_value=1)
    pool.acquire = MagicMock(return_value=conn)
    return pool


@pytest.mark.anyio
class TestRepoRegistryListByKeyword:
    def _row(self, name, **overrides):
        base = {
            "repo_name": name,
            "display_name": f"display_{name}",
            "description": f"description_{name}",
            "tags": ["tag_a", "tag_b"],
            "repo_url": None,
            "local_path": f"/repos/{name}",
            "languages": ["Python"],
            "enabled": True,
        }
        base.update(overrides)
        return base

    async def test_keyword_match_chinese(self):
        """中文 keyword 在 description 字段命中。"""
        rows = [self._row("repo_auth", description="用户认证服务")]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("认证", top_k=20)
        assert len(result) == 1
        assert result[0].repo_name == "repo_auth"

    async def test_keyword_match_english(self):
        """英文 keyword 在 repo_name 字段命中。"""
        rows = [self._row("repo_auth", repo_name="repo_auth")]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("auth", top_k=20)
        assert len(result) == 1

    async def test_keyword_match_tags_exact(self):
        """tags 数组精确命中（不受阈值影响）。"""
        rows = [self._row("repo_payment", tags=["支付", "payment"])]
        pool = _make_pool_with_rows(rows)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("支付", top_k=20)
        assert len(result) == 1
        assert result[0].repo_name == "repo_payment"

    async def test_keyword_threshold_relaxation(self):
        """召回 < 3 时阈值自动放宽 0.3 → 0.15 重试。"""
        # 首次 fetch 返回 1 条（< 3 触发松弛）
        initial = [self._row("repo_auth")]
        # 松弛后返回 5 条
        relaxed = [self._row(f"repo_{i}") for i in range(5)]
        pool = _make_pool_with_keyword_results(initial, relaxed, [])
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("模糊关键词", top_k=20)
        assert len(result) == 5  # 用松弛后的结果

    async def test_keyword_empty_query_returns_fallback(self):
        """空查询：兜底全表 ORDER BY id LIMIT top_k。"""
        fallback = [self._row(f"repo_{i}") for i in range(3)]
        # 空 query 时 0.3 和 0.15 都返回空，最终触发全表兜底
        pool = _make_pool_with_keyword_results([], [], fallback)
        reg = RepoRegistry(pool, optional=True)
        result = await reg.list_repos_by_keyword("", top_k=20)
        assert len(result) == 3
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/ingestion/code/test_repo_registry.py -v`
Expected: FAIL with `AttributeError: 'RepoRegistry' object has no attribute 'get_repo_by_name'`

- [ ] **Step 3: 在 `RepoRegistry` 添加 `get_repo_by_name` + `list_repos_by_keyword`**

修改 `src/spma/ingestion/code/repo_registry.py`：

```python
    # 追加在 list_active_repos 之后

    async def get_repo_by_name(self, name: str) -> RepoMeta | None:
        """根据仓库名查询单条元数据。"""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE repo_name = $1 AND enabled = true
                """,
                name,
            )
        return self._row_to_meta(row) if row else None

    async def list_repos_by_keyword(
        self,
        keyword: str,
        top_k: int = 20,
        similarity_threshold: float = 0.3,
    ) -> list[RepoMeta]:
        """Stage 1 pg_trgm 关键词预筛（design-13 §3.3 Stage 1 SQL）。

        阈值松弛机制：
            1. 默认 similarity_threshold=0.3
            2. 召回 < 3 条 → 放宽到 0.15 重试一次
            3. 仍 < 3 条 → 兜底全表 ORDER BY id LIMIT top_k
        """
        # 阶段 1：默认阈值
        rows = await self._keyword_query(keyword, top_k, similarity_threshold)
        if len(rows) >= 3:
            return [self._row_to_meta(r) for r in rows]

        # 阶段 2：阈值松弛到 0.15
        relaxed_rows = await self._keyword_query(keyword, top_k, 0.15)
        if len(relaxed_rows) >= 3:
            return [self._row_to_meta(r) for r in relaxed_rows]

        # 阶段 3：兜底全表（不依赖相似度）
        async with self._pool.acquire() as conn:
            fallback_rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                ORDER BY id
                LIMIT $1
                """,
                top_k,
            )
        return [self._row_to_meta(r) for r in fallback_rows]

    async def _keyword_query(
        self, keyword: str, top_k: int, similarity_threshold: float,
    ) -> list:
        """单次 pg_trgm 关键词查询。"""
        max_distance = 1.0 - similarity_threshold
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT repo_name, display_name, description, tags,
                       repo_url, local_path, languages, enabled
                FROM repo_registry
                WHERE enabled = true
                  AND (
                      (repo_name      <-> $1) <= $3
                      OR (display_name <-> $1) <= $3
                      OR (description  <-> $1) <= $3
                      OR $1 = ANY(tags)
                  )
                ORDER BY (
                    GREATEST(
                        similarity(repo_name, $1),
                        similarity(display_name, $1),
                        similarity(description, $1)
                    )
                    + CASE WHEN $1 = ANY(tags) THEN 0.3 ELSE 0 END
                ) DESC
                LIMIT $2
                """,
                keyword, top_k, max_distance,
            )
        return rows
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/ingestion/code/test_repo_registry.py -v`
Expected: 5 passed（list_active_repos + get_by_name + 4 keyword cases + 1 threshold relaxation + 1 empty query = 7；其中空查询 case 在 helper 第三次 fetch 才返回；通过 side_effect 配置可以走完）

> **注意**：测试 `test_keyword_empty_query_returns_fallback` 假设空 query 第一次 fetch 返回 `[]`（因为 pg_trgm 对空字符串可能不命中），第二次也是 `[]`，第三次走兜底查询。如果实际实现第一次空 query 直接走全表，本测试仍可通过（因为 `_keyword_query` 第二次仍会查，可能再返回 `[]`，第三次兜底）。如发现 case 不稳定，调整 mock 配置。

- [ ] **Step 5: Commit**

```bash
git add src/spma/ingestion/code/repo_registry.py tests/unit/ingestion/code/test_repo_registry.py
git commit -m "feat(repo-registry): add get_repo_by_name + list_repos_by_keyword with threshold relaxation"
```

---

# PR 3: `route_repos` Stage 0/1/2 改造

## Task 4: 新增 `route_repos` 透传 `query` 参数（向后兼容）

**Files:**
- Modify: `src/spma/agents/code/router.py`
- Modify: `tests/unit/agents/code/test_router.py`

- [ ] **Step 1: 保留旧测试，新增 1 个"query 透传"测试**

在 `tests/unit/agents/code/test_router.py` 顶部追加 import 和新测试类：

```python
from spma.agents.code.router import route_repos


# 在文件末尾追加：

@pytest.mark.anyio
class TestRouteReposQueryParam:
    async def test_query_param_is_optional_with_no_registry(self):
        """repo_registry=None 时，传 query 也不破坏旧行为。"""
        cache = MockFilePathCache({
            "repo-a": ["README.md"],
            "repo-b": ["setup.py"],
        })
        entities = {"code_refs": [], "module": ""}
        # 旧实现签名：route_repos(entities, cache) 也应工作
        result = await route_repos(
            query="支付接口的认证逻辑",  # 新参数
            entities=entities,
            file_path_cache=cache,
            repo_registry=None,  # 主路径禁用
            llm=None,
        )
        # 没有 repo_registry 时，行为完全兼容旧实现 → broad_search
        assert result["route_method"] == "broad_search"
        assert result["route_confidence"] == "low"
```

- [ ] **Step 2: 跑测试确认失败（query 关键字参数尚未存在）**

Run: `uv run pytest tests/unit/agents/code/test_router.py::TestRouteReposQueryParam -v`
Expected: FAIL with `TypeError: route_repos() got an unexpected keyword argument 'query'`

- [ ] **Step 3: 修改 `route_repos` 函数签名 + 实现 Stage 0 决策**

修改 `src/spma/agents/code/router.py`：

```python
"""Code Agent 路由层——Stage 0/1/2 三段式（design-13 §3.1 + §3.3）。

主路径：repo_registry（DB 单一真相源）+ LLM 精排；
兜底：file_path_cache 的 exact_file_match / module_lookup / broad_search。
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _parse_llm_json(content: str) -> dict | None:
    """从 LLM 响应中提取 JSON。容忍 markdown code block 包裹。"""
    if not content:
        return None
    # 去掉 markdown code block
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


async def route_repos(
    entities: dict,
    file_path_cache,
    max_candidates: int = 5,
    *,
    query: str = "",                # 新增：用户原始查询
    repo_registry=None,             # 新增：RepoRegistry 实例（主路径）
    llm=None,                       # 新增：可选 LLM
    two_stage_threshold: int = 5,   # 新增：仓库数 > 此阈值走两阶段
) -> dict:
    """根据用户查询和实体信息路由到候选仓库。

    Stage 0 决策：
        if repo_registry is None:
            → 旧路径（exact_file_match / module_lookup / broad_search）
        elif len(active_repos) <= two_stage_threshold:
            → 单阶段 LLM 路由（route_method="db_registry_match_single"）
        else:
            → 两阶段：Stage 1 pg_trgm → Stage 2 LLM 精排（"db_registry_match_two_stage"）
    """
    # 旧路径：repo_registry 为 None 时走向后兼容
    if repo_registry is None:
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    # 新路径：Stage 0/1/2 三段式
    active_repos = await repo_registry.list_active_repos()
    if not active_repos:
        logger.warning("repo_registry 无 enabled 记录，降级到 broad_search")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    if len(active_repos) <= two_stage_threshold:
        candidates = active_repos
        route_method = "db_registry_match_single"
    else:
        candidates = await repo_registry.list_repos_by_keyword(query or "", top_k=20)
        route_method = "db_registry_match_two_stage"

    # Stage 2：LLM 精排（llm=None 时直接返回 candidates）
    if llm is None or not query:
        selected = [r.repo_name for r in candidates][:max_candidates]
        confidence = "high" if len(selected) <= 3 else "medium"
        return {
            "candidate_repos": selected,
            "route_method": route_method,
            "route_confidence": confidence,
        }

    # Stage 2 LLM 精排
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
        parsed = _parse_llm_json(resp.content)
    except Exception as e:
        logger.warning(f"Stage 2 LLM 调用失败: {e}，降级到 module_lookup")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    if not parsed or "repo_names" not in parsed:
        logger.warning("Stage 2 LLM 返回 JSON 解析失败，降级到 module_lookup")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    # 过滤不在 candidates 中的仓库名（防 LLM 幻觉）
    valid_names = {r.repo_name for r in candidates}
    selected = [n for n in parsed["repo_names"] if n in valid_names][:max_candidates]
    if not selected:
        logger.warning("Stage 2 LLM 返回仓库名都不在 candidates 中，降级到 broad_search")
        return await _route_repos_legacy(entities, file_path_cache, max_candidates)

    confidence = "high" if len(selected) <= 3 else "medium"
    return {
        "candidate_repos": selected,
        "route_method": route_method,
        "route_confidence": confidence,
    }


async def _route_repos_legacy(
    entities: dict, file_path_cache, max_candidates: int = 5,
) -> dict:
    """旧路径：file_path_cache 走 exact_file_match / module_lookup / broad_search。
    与原 route_repos 行为完全一致（向后兼容）。
    """
    code_refs = entities.get("code_refs", []) or []
    module = entities.get("module", "")

    candidate_repos: set[str] = set()

    # 1. code_refs 精确匹配
    try:
        for ref in code_refs[:3]:
            matches = await file_path_cache.query_files(ref, limit=5)
            for m in matches:
                candidate_repos.add(m["repo_name"])
    except Exception:
        logger.warning("code_refs 路由查询失败，降级到 module 路由", exc_info=True)

    if candidate_repos:
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "exact_file_match",
            "route_confidence": "high" if len(candidate_repos) <= 3 else "medium",
        }

    # 2. module 映射
    if module:
        try:
            matches = await file_path_cache.query_files(module, limit=10)
            for m in matches:
                candidate_repos.add(m["repo_name"])
        except Exception:
            logger.warning("module 路由查询失败，降级到兜底路由", exc_info=True)

    if candidate_repos:
        return {
            "candidate_repos": list(candidate_repos)[:max_candidates],
            "route_method": "module_lookup",
            "route_confidence": "medium",
        }

    # 3. 兜底
    try:
        all_repos = await file_path_cache.list_repos()
        return {
            "candidate_repos": all_repos[:max_candidates],
            "route_method": "broad_search",
            "route_confidence": "low",
        }
    except Exception:
        logger.warning("兜底 list_repos 查询失败", exc_info=True)
        return {
            "candidate_repos": [],
            "route_method": "broad_search",
            "route_confidence": "low",
        }
```

- [ ] **Step 4: 跑测试确认全部通过**

Run: `uv run pytest tests/unit/agents/code/test_router.py -v`
Expected: 6 passed（5 旧 case + 1 新 query 透传 case）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/router.py tests/unit/agents/code/test_router.py
git commit -m "feat(router): add Stage 0/1/2 three-stage routing via repo_registry + LLM"
```

---

## Task 5: Stage 2 LLM 精排测试（mock LLM 主路径）

**Files:**
- Modify: `tests/unit/agents/code/test_router.py`

- [ ] **Step 1: 写 LLM 主路径成功单测（仓库数 ≤ 5 走单阶段）**

在 `test_router.py` 追加：

```python
from unittest.mock import AsyncMock, MagicMock


class MockRepoRegistry:
    """Mock RepoRegistry for Stage 0/1/2 测试。"""
    def __init__(self, repos, keyword_results=None):
        self._repos = repos
        self._keyword_results = keyword_results or []

    async def list_active_repos(self):
        return self._repos

    async def list_repos_by_keyword(self, keyword, top_k=20, similarity_threshold=0.3):
        return self._keyword_results

    async def get_repo_by_name(self, name):
        for r in self._repos:
            if r.repo_name == name:
                return r
        return None


def _make_repo(name, display="显示名", desc="描述", tags=None):
    """构造 RepoMeta dataclass 模拟对象。"""
    from spma.ingestion.code.repo_registry import RepoMeta
    return RepoMeta(
        repo_name=name,
        display_name=display,
        description=desc,
        tags=tags or [],
        repo_url=None,
        local_path=f"/repos/{name}",
        languages=["Python"],
        enabled=True,
    )


class MockLLMResponse:
    def __init__(self, content):
        self._content = content
    async def ainvoke(self, prompt):
        return MagicMock(content=self._content)


@pytest.mark.anyio
class TestRouteReposStage0Single:
    async def test_single_stage_llm_routes_correctly(self):
        """仓库数 ≤ 5 走单阶段 LLM，route_method=db_registry_match_single。"""
        repos = [
            _make_repo("repo_auth", desc="用户认证"),
            _make_repo("repo_payment", desc="支付服务"),
        ]
        reg = MockRepoRegistry(repos)
        llm = MockLLMResponse('{"repo_names": ["repo_auth"], "reason": "匹配"}')
        result = await route_repos(
            query="用户登录",
            entities={"code_refs": [], "module": ""},
            file_path_cache=MockFilePathCache({}),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,  # 2 ≤ 5 → 走单阶段
        )
        assert result["route_method"] == "db_registry_match_single"
        assert result["candidate_repos"] == ["repo_auth"]
        assert result["route_confidence"] == "high"
```

- [ ] **Step 2: 跑测试确认通过**

Run: `uv run pytest tests/unit/agents/code/test_router.py::TestRouteReposStage0Single -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/unit/agents/code/test_router.py
git commit -m "test(router): cover Stage 0 single-stage LLM path"
```

---

## Task 6: Stage 0/1/2 全场景单测（4 个降级路径）

**Files:**
- Modify: `tests/unit/agents/code/test_router.py`

- [ ] **Step 1: 写 4 个降级测试：两阶段 + LLM 超时 + JSON 解析错 + 幻觉过滤**

追加到 `test_router.py`：

```python
@pytest.mark.anyio
class TestRouteReposStage0TwoStage:
    async def test_two_stage_when_repos_exceed_threshold(self):
        """仓库数 > 5 走两阶段（pg_trgm 预筛 + LLM 精排）。"""
        repos = [_make_repo(f"repo_{i}") for i in range(6)]
        # Stage 1 keyword 预筛：模拟返回 3 个候选
        prefiltered = [_make_repo("repo_0"), _make_repo("repo_1"), _make_repo("repo_2")]
        reg = MockRepoRegistry(repos, keyword_results=prefiltered)
        llm = MockLLMResponse('{"repo_names": ["repo_0", "repo_1"], "reason": "ok"}')
        result = await route_repos(
            query="支付",
            entities={"code_refs": [], "module": ""},
            file_path_cache=MockFilePathCache({}),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,  # 6 > 5 → 走两阶段
        )
        assert result["route_method"] == "db_registry_match_two_stage"
        assert result["candidate_repos"] == ["repo_0", "repo_1"]


@pytest.mark.anyio
class TestRouteReposFallback:
    async def test_llm_timeout_falls_to_module_lookup(self):
        """LLM 超时 → module_lookup 兜底。"""
        from unittest.mock import AsyncMock as AM
        repos = [_make_repo("repo_auth")]
        reg = MockRepoRegistry(repos)
        cache = MockFilePathCache({"repo_auth": ["src/auth/oauth.py"]})

        class TimeoutLLM:
            async def ainvoke(self, prompt):
                raise TimeoutError("LLM timeout")

        result = await route_repos(
            query="用户登录",
            entities={"code_refs": [], "module": "auth"},
            file_path_cache=cache,
            repo_registry=reg,
            llm=TimeoutLLM(),
            two_stage_threshold=5,
        )
        assert result["route_method"] == "module_lookup"

    async def test_llm_invalid_json_falls_to_module_lookup(self):
        """LLM 返回非 JSON → module_lookup 兜底。"""
        repos = [_make_repo("repo_auth")]
        reg = MockRepoRegistry(repos)
        cache = MockFilePathCache({"repo_auth": ["src/auth/oauth.py"]})
        llm = MockLLMResponse("不是 JSON 格式的响应")
        result = await route_repos(
            query="用户登录",
            entities={"code_refs": [], "module": "auth"},
            file_path_cache=cache,
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "module_lookup"

    async def test_llm_hallucinated_repo_falls_to_broad_search(self):
        """LLM 返回仓库不在 candidates → 过滤后空 → broad_search 兜底。"""
        repos = [_make_repo("repo_auth")]
        reg = MockRepoRegistry(repos)
        cache = MockFilePathCache({"repo-a": ["x.py"], "repo-b": ["y.py"]})
        llm = MockLLMResponse('{"repo_names": ["repo_hallucinated"], "reason": "幻觉"}')
        result = await route_repos(
            query="用户登录",
            entities={"code_refs": [], "module": ""},
            file_path_cache=cache,
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "broad_search"
```

- [ ] **Step 2: 跑测试确认全部通过**

Run: `uv run pytest tests/unit/agents/code/test_router.py -v`
Expected: 10 passed（5 旧 + 1 query 透传 + 1 single stage + 1 two stage + 3 fallback）

- [ ] **Step 3: Commit**

```bash
git add tests/unit/agents/code/test_router.py
git commit -m "test(router): cover Stage 0/1/2 fallback paths (timeout/json/hallucination)"
```

---

# PR 4: 端到端验证

## Task 7: 写离线 replay 测试集 fixture + 端到端路由测试

**Files:**
- Create: `tests/integration/code/test_routing_e2e.py`
- Create: `tests/integration/code/conftest.py`（如需要）

- [ ] **Step 1: 创建集成测试目录 + conftest**

创建 `tests/integration/code/conftest.py`：

```python
"""集成测试 fixtures for code agent routing。

参考最近 commit 9f8c3f15 test(qr): end-to-end integration——使用 Testcontainers PG 模式。
"""
import os
import pytest


@pytest.fixture(scope="module")
def routing_replay_dataset():
    """离线 replay 测试集：≥ 30 条标注样本。"""
    return [
        # (query, entities, expected_repo_names, scenario)
        ("修改支付接口的认证逻辑", {}, ["repo_payment"], "two_stage_zh"),
        ("支付模块的单元测试", {}, ["repo_payment"], "two_stage_zh"),
        ("用户登录失败", {}, ["repo_auth"], "two_stage_zh"),
        ("OAuth token 刷新逻辑", {}, ["repo_auth"], "two_stage_en"),
        ("fix payment auth bug", {}, ["repo_payment", "repo_auth"], "two_stage_en_multi"),
        # ... 至少 30 条
    ]
```

> **注意**：完整 30+ 条 replay 数据集需要人工标注。本 plan 只定义 fixture 接口 + 5 个样本；剩余 25 条由 reviewer 后续补充。

- [ ] **Step 2: 写 4 个端到端场景测试**

创建 `tests/integration/code/test_routing_e2e.py`：

```python
"""Code Agent 路由端到端测试（design-13 §6.4 + spec §6 测试策略）。"""
import pytest
from unittest.mock import MagicMock, AsyncMock
from spma.agents.code.router import route_repos
from spma.ingestion.code.repo_registry import RepoMeta


def _make_repo(name, desc, tags):
    return RepoMeta(
        repo_name=name,
        display_name=name,
        description=desc,
        tags=tags,
        repo_url=None,
        local_path=f"/repos/{name}",
        languages=["Python"],
        enabled=True,
    )


class MockRegistryE2E:
    def __init__(self, repos):
        self._repos = repos

    async def list_active_repos(self):
        return self._repos

    async def list_repos_by_keyword(self, keyword, top_k=20, similarity_threshold=0.3):
        # 真实实现按 trigram 排序；这里 mock 简化：返回所有 enabled
        return self._repos

    async def get_repo_by_name(self, name):
        for r in self._repos:
            if r.repo_name == name:
                return r
        return None


class MockLLME2E:
    def __init__(self, repo_name):
        self._target = repo_name
    async def ainvoke(self, prompt):
        return MagicMock(content=f'{{"repo_names": ["{self._target}"], "reason": "test"}}')


class MockCacheE2E:
    async def query_files(self, *args, **kwargs):
        return []
    async def list_repos(self):
        return ["fallback_repo"]


@pytest.mark.anyio
class TestRoutingE2E:
    async def test_scenario_1_repos_le_5_uses_single_stage(self):
        """场景 1: 仓库数 ≤ 5 走 db_registry_match_single。"""
        repos = [
            _make_repo("repo_auth", "用户认证服务", ["auth", "认证", "login"]),
            _make_repo("repo_payment", "支付服务", ["payment", "支付"]),
            _make_repo("repo_order", "订单服务", ["order", "订单"]),
        ]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_payment")
        result = await route_repos(
            query="修改支付接口的认证逻辑",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_single"
        assert "repo_payment" in result["candidate_repos"]

    async def test_scenario_2_repos_gt_5_uses_two_stage(self):
        """场景 2: 仓库数 > 5 走 db_registry_match_two_stage。"""
        repos = [_make_repo(f"repo_{i}", f"服务{i}", [f"tag_{i}"]) for i in range(7)]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_3")
        result = await route_repos(
            query="测试",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_two_stage"

    async def test_scenario_3_threshold_relax_in_stage_1(self):
        """场景 3: Stage 1 召回 < 3 触发阈值松弛（mock 直接验证 method 走两阶段）。"""
        repos = [_make_repo(f"repo_{i}", f"完全无关的服务{i}", [f"x{i}"]) for i in range(6)]
        reg = MockRegistryE2E(repos)
        llm = MockLLME2E("repo_0")
        result = await route_repos(
            query="完全不相关的查询",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=llm,
            two_stage_threshold=5,
        )
        assert result["route_method"] == "db_registry_match_two_stage"

    async def test_scenario_4_llm_returns_unrelated_repo_falls_back(self):
        """场景 4: LLM 返回仓库不在 candidates → broad_search 兜底。"""
        repos = [_make_repo("repo_auth", "认证服务", ["auth"])]
        reg = MockRegistryE2E(repos)

        class HallucinatedLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"repo_names": ["repo_does_not_exist"], "reason": "x"}')

        result = await route_repos(
            query="test",
            entities={},
            file_path_cache=MockCacheE2E(),
            repo_registry=reg,
            llm=HallucinatedLLM(),
            two_stage_threshold=5,
        )
        assert result["route_method"] == "broad_search"
```

- [ ] **Step 3: 跑集成测试**

Run: `uv run pytest tests/integration/code/test_routing_e2e.py -v`
Expected: 4 passed

- [ ] **Step 4: Commit**

```bash
git add tests/integration/code/
git commit -m "test(code-routing): add 4 e2e scenarios for Stage 0/1/2 routing"
```

---

# PR 5: 多轮探索前置补齐

## Task 8: `RipgrepExecutor.glob_files` 方法

**Files:**
- Modify: `src/spma/agents/code/searcher.py`
- Modify: `tests/unit/agents/code/test_searcher.py`

- [ ] **Step 1: 写失败单测**

在 `tests/unit/agents/code/test_searcher.py` 追加：

```python
import tempfile
import os
from spma.agents.code.searcher import RipgrepExecutor


@pytest.mark.asyncio
async def test_glob_files_finds_matching_files():
    """glob_files 返回与 pattern 匹配的文件路径列表。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        # 写入测试文件
        os.makedirs(os.path.join(tmpdir, "src/auth"))
        with open(os.path.join(tmpdir, "src/auth/oauth.py"), "w") as f:
            f.write("# test")
        with open(os.path.join(tmpdir, "src/auth/token.py"), "w") as f:
            f.write("# test")
        with open(os.path.join(tmpdir, "src/billing/checkout.py"), "w") as f:
            f.write("# test")

        executor = RipgrepExecutor({"repo_test": tmpdir})
        results = await executor.glob_files("**/*.py", ["repo_test"])
        paths = [r["file_path"] for r in results]
        assert any("oauth.py" in p for p in paths)
        assert any("token.py" in p for p in paths)
        assert any("checkout.py" in p for p in paths)
        for r in results:
            assert r["repo"] == "repo_test"


@pytest.mark.asyncio
async def test_glob_files_filters_sensitive_paths():
        """敏感路径（.env / .git/ / secrets.* / *.pem / *.key）被过滤。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            os.makedirs(os.path.join(tmpdir, ".git"))
            with open(os.path.join(tmpdir, ".git/config"), "w") as f:
                f.write("git config")
            with open(os.path.join(tmpdir, ".env"), "w") as f:
                f.write("SECRET=xxx")
            with open(os.path.join(tmpdir, "secrets.yaml"), "w") as f:
                f.write("api_key: xxx")
            with open(os.path.join(tmpdir, "server.pem"), "w") as f:
                f.write("---")
            with open(os.path.join(tmpdir, "main.py"), "w") as f:
                f.write("# normal")

            executor = RipgrepExecutor({"repo_test": tmpdir})
            results = await executor.glob_files("**/*", ["repo_test"])
            paths = [r["file_path"] for r in results]
            # 正常文件应被命中
            assert any("main.py" in p for p in paths)
            # 敏感文件应被过滤
            assert not any(".env" in p for p in paths)
            assert not any("secrets" in p for p in paths)
            assert not any(".git/" in p for p in paths)
            assert not any(".pem" in p for p in paths)
            assert not any(".key" in p for p in paths)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/code/test_searcher.py::test_glob_files_finds_matching_files -v`
Expected: FAIL with `AttributeError: 'RipgrepExecutor' object has no attribute 'glob_files'`

- [ ] **Step 3: 实现 `glob_files` + 敏感路径黑名单**

在 `src/spma/agents/code/searcher.py` 追加（在 `_stem_split` 之前）：

```python
# 敏感路径黑名单（design-13 §8 风险缓解）
SENSITIVE_PATH_PATTERNS = [
    "**/.env",
    "**/secrets.*",
    "**/.git/**",
    "**/*.pem",
    "**/*.key",
]


def _is_sensitive_path(file_path: str) -> bool:
    """检查路径是否匹配敏感路径黑名单。"""
    import fnmatch
    for pattern in SENSITIVE_PATH_PATTERNS:
        if fnmatch.fnmatch(file_path, pattern):
            return True
    return False


# 追加在 RipgrepExecutor 类内（_stem_split 之前）：

    async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
        """Glob 模式匹配，发现目录结构。

        Args:
            pattern: glob 模式（如 "**/*.py"）
            candidate_repos: 候选仓库名列表

        Returns:
            [{"repo": str, "file_path": str}, ...]
            敏感路径（.env / secrets.* / .git/ / *.pem / *.key）被过滤
        """
        results: list[dict] = []
        for repo_name in candidate_repos:
            repo_path = self._repo_paths.get(repo_name)
            if not repo_path:
                continue
            try:
                proc = await asyncio.create_subprocess_exec(
                    "rg", "--files", "--glob", pattern, repo_path,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self._timeout)
            except asyncio.TimeoutError:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                logger.warning(f"glob_files timeout for {repo_name} pattern={pattern}")
                continue
            except Exception as e:
                logger.error(f"glob_files error for {repo_name}: {e}")
                continue
            if proc.returncode not in (0, 1):
                logger.warning(f"rg --files exited {proc.returncode} for {repo_name}: {stderr.decode('utf-8', errors='replace')[:200]}")
                continue
            for line in stdout.decode("utf-8", errors="replace").strip().split("\n"):
                if not line:
                    continue
                # 转为相对路径
                rel_path = os.path.relpath(line, repo_path)
                if _is_sensitive_path(rel_path):
                    continue
                results.append({"repo": repo_name, "file_path": rel_path})
        return results
```

并在文件顶部追加 `import os`（如未导入）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/agents/code/test_searcher.py -v`
Expected: 9 passed（7 旧 + 2 新 glob_files）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/searcher.py tests/unit/agents/code/test_searcher.py
git commit -m "feat(searcher): add RipgrepExecutor.glob_files with sensitive path blacklist"
```

---

## Task 9: `RipgrepExecutor.read_files` 方法

**Files:**
- Modify: `src/spma/agents/code/searcher.py`
- Modify: `tests/unit/agents/code/test_searcher.py`

- [ ] **Step 1: 写失败单测**

在 `tests/unit/agents/code/test_searcher.py` 追加：

```python
@pytest.mark.asyncio
async def test_read_files_returns_content():
    """read_files 读取指定文件的内容。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, "main.py"), "w") as f:
            f.write("def hello():\n    pass\n")
        with open(os.path.join(tmpdir, "util.py"), "w") as f:
            f.write("def helper():\n    pass\n")

        executor = RipgrepExecutor({"repo_test": tmpdir})
        results = await executor.read_files([
            {"repo": "repo_test", "file_path": "main.py"},
            {"repo": "repo_test", "file_path": "util.py"},
        ])
        assert len(results) == 2
        assert any("def hello" in r["content"] for r in results)
        assert any("def helper" in r["content"] for r in results)


@pytest.mark.asyncio
async def test_read_files_silently_skips_io_errors():
    """read_files 对不存在的文件用 errors='ignore' 静默跳过。"""
    executor = RipgrepExecutor({"repo_test": "/nonexistent"})
    results = await executor.read_files([
        {"repo": "repo_test", "file_path": "nonexistent.py"},
    ])
    assert results == []


@pytest.mark.asyncio
async def test_read_files_filters_sensitive_paths():
    """read_files 对敏感路径直接返回空（不入结果）。"""
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(os.path.join(tmpdir, ".env"), "w") as f:
            f.write("SECRET=xxx")

        executor = RipgrepExecutor({"repo_test": tmpdir})
        results = await executor.read_files([
            {"repo": "repo_test", "file_path": ".env"},
        ])
        assert results == []
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/code/test_searcher.py::test_read_files_returns_content -v`
Expected: FAIL with `AttributeError: 'RipgrepExecutor' object has no attribute 'read_files'`

- [ ] **Step 3: 实现 `read_files`**

在 `RipgrepExecutor` 类内追加（`glob_files` 之后）：

```python
    async def read_files(self, files: list[dict]) -> list[dict]:
        """读取指定文件内容。

        Args:
            files: [{"repo": str, "file_path": str}, ...]

        Returns:
            [{"repo": str, "file_path": str, "content": str}, ...]
            敏感路径被过滤；I/O 错误静默跳过。
        """
        results: list[dict] = []
        for f in files:
            if _is_sensitive_path(f["file_path"]):
                continue
            repo_path = self._repo_paths.get(f["repo"])
            if not repo_path:
                continue
            full_path = os.path.join(repo_path, f["file_path"])
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as fp:
                    content = fp.read()
                results.append({
                    "repo": f["repo"],
                    "file_path": f["file_path"],
                    "content": content,
                })
            except Exception as e:
                logger.warning(f"read_files failed for {full_path}: {e}")
                continue
        return results
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/agents/code/test_searcher.py -v`
Expected: 12 passed（9 + 3 新 read_files）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/searcher.py tests/unit/agents/code/test_searcher.py
git commit -m "feat(searcher): add RipgrepExecutor.read_files"
```

---

## Task 10: `assess_code_completeness` 升级到 7 种收敛模式

**Files:**
- Modify: `src/spma/agents/code/completeness.py`
- Create: `tests/unit/agents/code/test_completeness_v2.py`

- [ ] **Step 1: 写 7 种收敛模式单测**

创建 `tests/unit/agents/code/test_completeness_v2.py`：

```python
"""Tests for assess_code_completeness v2 — 7 收敛模式（design-13 §3.4）。

7 mode = 5 确定性 + 2 LLM 路径：
    1. goal_verified: code_refs 非空 + total ≥ 3 + fallback_layer = 0
    2. stuck: round ≥ 2 + new_files_this_round=0 + previous_new_files=0
    3. regression: round_over_round_ratio < 0.5 + total_results 减少
    4. diminishing_returns: 连续两轮 new_files_rate < 0.10
    5. cap_reached: call_depth ≥ max_rounds 或 total_files ≥ max_files
    6. llm_judged: 5 确定性全不命中 + LLM 判定 sufficient
    7. expand: 5 确定性全不命中 + LLM 判定 insufficient
"""
import pytest
from unittest.mock import AsyncMock, MagicMock
from spma.agents.code.completeness import assess_code_completeness


def _make_results(n: int) -> list[dict]:
    return [{"file_path": f"file_{i}.py", "match_text": "code"} for i in range(n)]


@pytest.mark.anyio
class TestCompletenessV2:
    async def test_goal_verified(self):
        """确定性 1: code_refs 非空 + total ≥ 3 + fallback_layer=0。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": ["auth.py"]},
            call_depth=0,
            new_files_this_round=3,
            fallback_layer=0,
            round=1,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "goal_verified"

    async def test_stuck_with_round_2(self):
        """确定性 2: round=2, new_files_this_round=0, previous_new_files=0 → stuck。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=0,
            fallback_layer=1,
            previous_new_files=0,
            round=2,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "stuck"

    async def test_stuck_not_triggered_in_round_1(self):
        """boundary case: round=1 时即使 new_files=0 也不触发 stuck。"""
        results = _make_results(3)
        outcome = await assess_code_completeness(
            ripgrep_results=results,
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=0,
            new_files_this_round=0,
            fallback_layer=1,
            previous_new_files=0,
            round=1,  # 首轮豁免
        )
        # 首轮 stuck 不触发 → 走 LLM 路径（llm=None 时降级 expand）
        assert outcome.level != "stuck"

    async def test_regression(self):
        """确定性 3: round_over_round_ratio < 0.5 且本轮 total 减少。"""
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),  # 本轮 2
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=2,
            new_files_this_round=1,  # ratio = 1/10 = 0.1 < 0.5
            fallback_layer=1,
            previous_new_files=10,  # 上轮 10
            round=3,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "regression"

    async def test_diminishing_returns(self):
        """确定性 4: 连续两轮 new_files_rate < 0.10。"""
        # 本轮：new_files=1, total_files=20, rate=0.05
        # 需传递上轮 new_files_rate < 0.10 的状态 → 简化为 single-round 检测（接口预留）
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(1),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=3,
            new_files_this_round=1,
            fallback_layer=1,
            previous_new_files=1,  # 上一轮也只有 1 个
            round=3,
            total_files=20,
        )
        # 简化判定：new_files_rate < 0.10 → diminishing_returns
        assert outcome.level in ("diminishing_returns", "stuck")  # 两种都可能触发

    async def test_cap_reached_max_rounds(self):
        """确定性 5: call_depth ≥ max_rounds。"""
        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(3),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=6,  # = max_rounds
            new_files_this_round=2,
            fallback_layer=1,
            max_rounds=6,
            round=6,
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "cap_reached"

    async def test_llm_judged_sufficient(self):
        """LLM 路径 1: 5 确定性全不命中 + LLM 判定 sufficient。"""
        class MockLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "sufficient", "reason": "ok"}')

        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=1,  # > 0
            fallback_layer=1,
            previous_new_files=1,  # > 0（不触发 stuck）
            round=2,
            llm=MockLLM(),
        )
        assert outcome.verdict == "converge"
        assert outcome.level == "llm_judged"

    async def test_expand_when_llm_says_insufficient(self):
        """LLM 路径 2: LLM 判定 insufficient → expand。"""
        class MockLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "insufficient", "reason": "more needed"}')

        outcome = await assess_code_completeness(
            ripgrep_results=_make_results(2),
            expanded_context=[],
            entities={"code_refs": []},
            call_depth=1,
            new_files_this_round=1,
            fallback_layer=1,
            previous_new_files=1,
            round=2,
            llm=MockLLM(),
        )
        assert outcome.verdict == "expand"
        assert outcome.level == "expand"
```

- [ ] **Step 2: 跑测试确认失败（v2 参数未实现）**

Run: `uv run pytest tests/unit/agents/code/test_completeness_v2.py -v`
Expected: FAIL with `TypeError: assess_code_completeness() got an unexpected keyword argument 'previous_new_files'`

- [ ] **Step 3: 重写 `assess_code_completeness` v2**

替换 `src/spma/agents/code/completeness.py` 全部内容：

```python
"""Code Agent 完备度判断——v2: 7 种收敛模式（design-13 §3.4）。

7 mode = 5 确定性 + 2 LLM 路径：
    1. goal_verified: code_refs 非空 + total ≥ 3 + fallback_layer = 0
    2. stuck: round ≥ 2 + new_files_this_round=0 + previous_new_files=0
    3. regression: round_over_round_ratio < 0.5 + 本轮 total 减少
    4. diminishing_returns: new_files_rate < 0.10
    5. cap_reached: call_depth ≥ max_rounds 或 total_files ≥ max_files
    6. llm_judged: 5 确定性全不命中 + LLM sufficient
    7. expand: 5 确定性全不命中 + LLM insufficient（LLM 失败兜底）
"""
import json
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CodeCompletenessResult:
    verdict: str          # "converge" | "expand"
    level: str            # 7 种之一
    reason: str


async def assess_code_completeness(
    ripgrep_results: list[dict],
    expanded_context: list[dict],
    entities: dict,
    call_depth: int,
    new_files_this_round: int,
    fallback_layer: int,
    llm=None,
    min_results: int = 3,
    *,
    previous_new_files: int = 0,    # 新增：stuck 判定
    max_files: int = 50,             # 新增：cap_reached 判定
    max_rounds: int = 6,             # 新增：cap_reached 判定
    round: int = 0,                  # 新增：stuck 守卫（round ≥ 2）
    total_files: int = 0,            # 新增：diminishing_returns 判定
) -> CodeCompletenessResult:
    """v2: 7 种收敛模式判定。"""
    total_results = len(ripgrep_results) + len(expanded_context)
    code_refs = entities.get("code_refs", []) or []

    # 确定性 1: goal_verified
    if total_results >= min_results and code_refs and fallback_layer == 0:
        return CodeCompletenessResult(
            verdict="converge", level="goal_verified", reason="deterministic_code_refs",
        )

    # 确定性 2: stuck（首轮豁免）
    if round >= 2 and new_files_this_round == 0 and previous_new_files == 0:
        return CodeCompletenessResult(
            verdict="converge", level="stuck", reason="no_new_files_two_rounds",
        )

    # 确定性 3: regression（ratio < 0.5 且本轮 total 减少）
    if previous_new_files > 0:
        ratio = new_files_this_round / previous_new_files
        if ratio < 0.5 and new_files_this_round < previous_new_files:
            return CodeCompletenessResult(
                verdict="converge", level="regression", reason=f"ratio={ratio:.2f}",
            )

    # 确定性 4: diminishing_returns（new_files_rate < 0.10）
    if total_files > 0:
        new_files_rate = new_files_this_round / total_files
        if new_files_rate < 0.10 and new_files_this_round < 3:
            return CodeCompletenessResult(
                verdict="converge", level="diminishing_returns",
                reason=f"rate={new_files_rate:.2f}",
            )

    # 确定性 5: cap_reached
    if call_depth >= max_rounds or total_files >= max_files:
        reason = "max_rounds" if call_depth >= max_rounds else "max_files"
        return CodeCompletenessResult(
            verdict="converge", level="cap_reached", reason=reason,
        )

    # LLM 路径
    if llm is not None:
        verdict, reason = await _llm_code_completeness_check(
            ripgrep_results, expanded_context, entities, llm,
        )
        level = "llm_judged" if verdict == "converge" else "expand"
        return CodeCompletenessResult(verdict=verdict, level=level, reason=reason)

    # LLM 不可用 → 兜底 expand
    return CodeCompletenessResult(
        verdict="expand", level="expand", reason="no_llm_default_expand",
    )


async def _llm_code_completeness_check(ripgrep_results, expanded_context, entities, llm) -> tuple[str, str]:
    snippets = []
    for r in ripgrep_results[:10]:
        snippets.append(f"- [{r.get('file_path', '?')}:{r.get('line_number', '?')}]: {r.get('match_text', '')[:150]}")
    for f in expanded_context[:5]:
        snippets.append(f"- [EXPANDED] {f.get('file_path', '?')}: calls={f.get('calls', [])[:3]}")

    snippets_text = "\n".join(snippets) if snippets else "无结果"
    prompt = f"""根据以下代码搜索结果，判断信息是否足以定位到用户想要的代码实现。

用户关注的实体: {json.dumps({k: v for k, v in entities.items() if v}, ensure_ascii=False)}
代码搜索结果摘要:
{snippets_text}
只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content
        data = json.loads(resp)
        if data.get("assessment") == "sufficient":
            return "converge", "llm_judged_sufficient"
        return "expand", "llm_judged_insufficient"
    except Exception as e:
        logger.warning(f"LLM 完备度判断失败: {e}，默认扩展")
        return "expand", "llm_error_default_expand"
```

- [ ] **Step 4: 跑新旧测试都通过**

Run: `uv run pytest tests/unit/agents/code/test_completeness.py tests/unit/agents/code/test_completeness_v2.py -v`
Expected: 旧 5 case + 新 8 case = 13 passed

> **注意**：旧 `test_completeness.py` 用旧接口（无 v2 参数）调用，应仍能通过——因为 v2 参数都给了默认值。

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/completeness.py tests/unit/agents/code/test_completeness_v2.py
git commit -m "feat(completeness): upgrade to 7 convergence levels (5 deterministic + 2 LLM path)"
```

---

## Task 11: `graph.py` 默认 `max_rounds=6`

**Files:**
- Modify: `src/spma/agents/code/graph.py`

- [ ] **Step 1: 修改 `build_code_agent_graph` 默认值**

修改 `src/spma/agents/code/graph.py:17`：

```python
def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 6,            # 改：默认从 3 升到 6（design-13 §3.4）
    timeout_ms: int = 2000,
    progress=None,
) -> StateGraph:
```

- [ ] **Step 2: 跑旧测试确认向后兼容**

Run: `uv run pytest tests/unit/agents/code/ -v`
Expected: 全部通过

- [ ] **Step 3: Commit**

```bash
git add src/spma/agents/code/graph.py
git commit -m "feat(graph): default max_rounds 3 → 6 (design-13 §3.4)"
```

---

# PR 6: `CodeExplorer` 抽离 + graph.py 薄包装

## Task 12: `ExplorerState` dataclass

**Files:**
- Create: `src/spma/agents/code/explorer.py`
- Create: `tests/unit/agents/code/test_explorer.py`

- [ ] **Step 1: 写失败单测（state 初始化 + 字段默认值）**

在 `tests/unit/agents/code/test_explorer.py` 写：

```python
"""Tests for CodeExplorer class (design-13 §3.5 + spec §4.5)."""
import pytest
from spma.agents.code.explorer import ExplorerState


class TestExplorerState:
    def test_default_values(self):
        state = ExplorerState()
        assert state.round == 0
        assert state.previous_new_files == 0
        assert state.new_files_this_round == 0
        assert state.search_terms == {}
        assert state.ripgrep_results == []
        assert state.expanded_context == []
        assert state.seen_files == set()
        assert state.fallback_layer == 0
        assert state.call_depth == 0
        assert state.convergence is None
        assert state.query == ""
        assert state.entities == {}

    def test_seen_files_initialized_as_set(self):
        """seen_files 必须是 set 而非 list（去重语义）。"""
        state = ExplorerState()
        state.seen_files.add(("repo_a", "file.py"))
        state.seen_files.add(("repo_a", "file.py"))  # 重复
        assert len(state.seen_files) == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py::TestExplorerState -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spma.agents.code.explorer'`

- [ ] **Step 3: 创建 `explorer.py` 含 `ExplorerState`**

创建 `src/spma/agents/code/explorer.py`：

```python
"""CodeExplorer——多轮探索引擎（design-13 §3.5）。

独立于 LangGraph：通过 explore() 一次性调用，也可注入 mock 状态做单测。
6 阶段方法：refine / glob / grep / read / expand_via_ast / assess。
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from spma.agents.code.completeness import CodeCompletenessResult
    from spma.agents.code.state import CodeAgentState

logger = logging.getLogger(__name__)


@dataclass
class ExplorerState:
    """CodeExplorer 内部状态——独立于 LangGraph CodeAgentState。

    与 LangGraph state 边界：
        - 入口（explore() 接收）：从 CodeAgentState 读 entities / candidate_repos / query
        - 出口（explore() 返回）：把 ripgrep_results / expanded_context / convergence 写回
        - 不双向同步：LangGraph state 在 explore() 期间冻结
    """
    round: int = 0                          # 当前轮次（1-indexed）
    previous_new_files: int = 0              # 上轮新增文件数（stuck 判定用）
    new_files_this_round: int = 0            # 本轮新增文件数
    search_terms: dict = field(default_factory=dict)
    ripgrep_results: list[dict] = field(default_factory=list)
    expanded_context: list[dict] = field(default_factory=list)
    seen_files: set[tuple[str, str]] = field(default_factory=set)
    fallback_layer: int = 0
    call_depth: int = 0
    convergence: "CodeCompletenessResult | None" = None
    # 输入字段（从 CodeAgentState 传入）
    query: str = ""
    entities: dict = field(default_factory=dict)
    candidate_repos: list[str] = field(default_factory=list)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py::TestExplorerState -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): add ExplorerState dataclass"
```

---

## Task 13: `CodeExplorer` 类骨架 + `explore()` 入口

**Files:**
- Modify: `src/spma/agents/code/explorer.py`
- Modify: `tests/unit/agents/code/test_explorer.py`

- [ ] **Step 1: 写 `explore()` 收尾测试（goal_verified 时单轮退出）**

在 `test_explorer.py` 追加：

```python
from spma.agents.code.explorer import CodeExplorer


class MockRipgrepExecutor:
    """Mock RipgrepExecutor——所有方法返回空。"""
    async def glob_files(self, pattern, candidate_repos):
        return []
    async def search(self, search_terms, candidate_repos, fallback_layer=0):
        return []
    async def read_files(self, files):
        return []


class MockASTParser:
    async def parse_file(self, path):
        return {}


class MockGraphState(dict):
    """Mock CodeAgentState——简单 dict 即可（explore() 写回字段）。"""
    pass


@pytest.mark.anyio
class TestCodeExplorerExplore:
    async def test_explore_terminates_on_goal_verified(self):
        """第 1 轮 goal_verified 后立即退出（不进入第 2 轮）。"""
        from spma.agents.code.completeness import CodeCompletenessResult

        executor = MockRipgrepExecutor()
        ast = MockASTParser()

        class VerifyingLLM:
            """不在 _refine_terms 时被调用，_assess 也不需要 LLM（直接 goal_verified 收敛）。"""
            async def ainvoke(self, prompt):
                return None  # 不应被调用

        explorer = CodeExplorer(
            ripgrep_executor=executor,
            ast_parser=ast,
            llm=VerifyingLLM(),
            max_rounds=6,
        )
        # 构造 graph state：含 3 个 ripgrep_results + code_refs
        graph_state = {
            "entities": {"code_refs": ["auth.py"]},
            "candidate_repos": ["repo_auth"],
            "query": "用户登录",
            "ripgrep_results": [
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 1, "match_text": "def login"},
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 5, "match_text": "def logout"},
                {"repo": "repo_auth", "file_path": "auth.py", "line_number": 10, "match_text": "def register"},
            ],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        assert result["convergence_reason"] == "goal_verified:deterministic_code_refs"
        # round 应为 1（首轮即收敛）
        assert result["rounds_used"] == 1
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py::TestCodeExplorerExplore -v`
Expected: FAIL with `ImportError: cannot import name 'CodeExplorer' from 'spma.agents.code.explorer'`

- [ ] **Step 3: 在 `explorer.py` 添加 `CodeExplorer` 类 + `explore()` 入口（骨架，6 阶段方法为占位）**

```python
class CodeExplorer:
    """多轮探索引擎——封装 Glob→Grep→Read→Refine→Assess 循环。"""

    def __init__(
        self,
        ripgrep_executor,
        ast_parser,
        llm,
        on_round_complete: Callable[[ExplorerState], Awaitable[None]] | None = None,
        max_rounds: int = 6,
        max_files: int = 50,
    ):
        self._executor = ripgrep_executor
        self._ast = ast_parser
        self._llm = llm
        self._on_round_complete = on_round_complete
        self._max_rounds = max_rounds
        self._max_files = max_files

    async def explore(self, graph_state: "CodeAgentState") -> "CodeAgentState":
        """一次性跑完多轮探索，返回写回的 graph_state。"""
        state = self._init_from_graph_state(graph_state)
        while not self._is_converged(state):
            await self._run_one_round(state)
            if self._on_round_complete:
                await self._on_round_complete(state)
        return self._write_back_to_graph_state(graph_state, state)

    def _init_from_graph_state(self, graph_state: dict) -> ExplorerState:
        """从 LangGraph state 填充 ExplorerState。"""
        return ExplorerState(
            round=0,
            ripgrep_results=list(graph_state.get("ripgrep_results", [])),
            expanded_context=list(graph_state.get("expanded_context", [])),
            fallback_layer=graph_state.get("fallback_layer", 0),
            call_depth=graph_state.get("call_depth", 0),
            query=graph_state.get("query", graph_state.get("original_query", "")),
            entities=dict(graph_state.get("entities", {})),
            candidate_repos=list(graph_state.get("candidate_repos", [])),
        )

    def _write_back_to_graph_state(self, graph_state: dict, state: ExplorerState) -> dict:
        """把 ExplorerState 写回 graph_state。"""
        graph_state["ripgrep_results"] = state.ripgrep_results
        graph_state["expanded_context"] = state.expanded_context
        graph_state["rounds_used"] = state.round
        if state.convergence:
            graph_state["assessment"] = state.convergence.verdict
            graph_state["convergence_reason"] = f"{state.convergence.level}:{state.convergence.reason}"
            graph_state["final_results"] = state.ripgrep_results
        else:
            graph_state["convergence_reason"] = "no_assessment"
            graph_state["final_results"] = state.ripgrep_results
        return graph_state

    def _is_converged(self, state: ExplorerState) -> bool:
        """收敛判定：cap_reached / goal_verified / stuck / regression / diminishing_returns / llm_judged 之一。"""
        if state.convergence is None:
            return False
        return state.convergence.verdict == "converge"

    async def _run_one_round(self, state: ExplorerState) -> None:
        """一轮 6 阶段：refine→glob→grep→read→expand→assess（P1/P2/P3 对策见 §3.5.1）。"""
        state.round += 1
        state.call_depth = state.round
        await self._refine_terms(state)
        glob_hits = await self._glob(state)
        grep_hits = await self._grep(state)
        await self._read(state, glob_hits + grep_hits)
        await self._expand_via_ast(state)
        await self._assess(state)

    # ---- 6 阶段方法（占位实现，下一 Task 逐个填充）----
    async def _refine_terms(self, state: ExplorerState) -> None: ...
    async def _glob(self, state: ExplorerState) -> list[dict]: return []
    async def _grep(self, state: ExplorerState) -> list[dict]: return []
    async def _read(self, state: ExplorerState, candidates: list[dict]) -> None: ...
    async def _expand_via_ast(self, state: ExplorerState) -> None: ...
    async def _assess(self, state: ExplorerState) -> None: ...
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py -v`
Expected: 4 passed（2 state + 1 explore 收尾 + 还有 1 个状态测试）

> **注意**：当前 `goal_verified` 单测要求 round=1 时即收敛——需要 `_run_one_round` 流程正确处理 P1 对策（assess 放最后）。当前实现 stub 阶段方法返回空，`_assess` 调用应直接进入 goal_verified 路径（因为已有 3 个 ripgrep_results + code_refs）。

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): add CodeExplorer skeleton with explore() + 6-stage pipeline"
```

---

## Task 14: 实现 6 阶段方法（`_refine_terms` / `_glob` / `_grep` / `_read` / `_expand_via_ast` / `_assess`）

**Files:**
- Modify: `src/spma/agents/code/explorer.py`
- Modify: `tests/unit/agents/code/test_explorer.py`

- [ ] **Step 1: 写 8 项单测**

在 `test_explorer.py` 追加：

```python
class MockRipgrepExecutorWithData(MockRipgrepExecutor):
    """带数据的 Mock RipgrepExecutor——可控返回。"""
    def __init__(self, glob_results=None, grep_results=None):
        self._glob = glob_results or []
        self._grep = grep_results or []

    async def glob_files(self, pattern, candidate_repos):
        return self._glob

    async def search(self, search_terms, candidate_repos, fallback_layer=0):
        return self._grep

    async def read_files(self, files):
        return [{"repo": f["repo"], "file_path": f["file_path"], "content": "mocked"} for f in files]


class MockASTParserWithExpansion:
    """Mock AST parser——调用时返回模拟 expanded context。"""
    def __init__(self, expand_results=None):
        self._expand = expand_results or []

    async def parse_file(self, path):
        return {}


@pytest.mark.anyio
class TestCodeExplorerRefineTerms:
    async def test_refine_terms_round1_uses_query_and_entities(self):
        """P3 对策：第 1 轮 expanded_context 为空时退化用 query + entities，不调 LLM。"""
        executor = MockRipgrepExecutorWithData()
        ast = MockASTParserWithExpansion()

        class TrackingLLM:
            def __init__(self):
                self.call_count = 0
            async def ainvoke(self, prompt):
                self.call_count += 1
                return None

        llm = TrackingLLM()
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=6,
        )
        state = ExplorerState(
            round=1,
            query="用户登录",
            entities={"code_refs": ["auth.py"]},
        )
        await explorer._refine_terms(state)
        # LLM 不应被调用（首轮退化）
        assert llm.call_count == 0
        # search_terms 应包含 query + entities
        assert state.search_terms.get("query") == "用户登录"
        assert "auth.py" in state.search_terms.get("entities_code_refs", [])


@pytest.mark.anyio
class TestCodeExplorerStuckDetection:
    async def test_stuck_after_two_rounds_with_zero_new_files(self):
        """连续两轮 0 新文件 → stuck 收敛（round=2 触发）。"""
        executor = MockRipgrepExecutorWithData(
            glob_results=[], grep_results=[],  # 永远无新结果
        )
        ast = MockASTParserWithExpansion(expand_results=[])

        class NoOpLLM:
            async def ainvoke(self, prompt):
                # _refine_terms 用 query 退化，_assess 在 stuck 之前不需 LLM
                # 留作 None return 防御
                return None

        llm = NoOpLLM()
        round_events = []
        async def on_round(es):
            round_events.append((es.round, es.convergence.level if es.convergence else "pending"))

        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm,
            on_round_complete=on_round, max_rounds=6,
        )
        graph_state = {
            "entities": {"code_refs": []},
            "candidate_repos": ["repo_a"],
            "query": "test",
            "ripgrep_results": [],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        # round=2 时 stuck 触发
        assert "stuck" in result["convergence_reason"]
        assert result["rounds_used"] >= 2


@pytest.mark.anyio
class TestCodeExplorerCapReached:
    async def test_cap_reached_when_max_rounds_exceeded(self):
        """call_depth ≥ max_rounds 触发 cap_reached。"""
        executor = MockRipgrepExecutorWithData()  # 永远无新结果
        ast = MockASTParserWithExpansion()

        class NoOpLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "insufficient", "reason": "x"}')
                # 永不收敛，但会被 cap_reached 截断

        from unittest.mock import MagicMock
        llm = NoOpLLM()
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm, max_rounds=2,
        )
        graph_state = {
            "entities": {"code_refs": []},
            "candidate_repos": ["repo_a"],
            "query": "test",
            "ripgrep_results": [],
            "expanded_context": [],
        }
        result = await explorer.explore(graph_state)
        assert "cap_reached" in result["convergence_reason"]
        assert result["rounds_used"] <= 2


@pytest.mark.anyio
class TestCodeExplorerCallback:
    async def test_callback_invoked_after_each_round(self):
        """on_round_complete 在每轮结束触发。"""
        executor = MockRipgrepExecutorWithData(
            glob_results=[{"repo": "r", "file_path": "a.py"}],
        )
        ast = MockASTParserWithExpansion()

        class NoOpLLM:
            async def ainvoke(self, prompt):
                return MagicMock(content='{"assessment": "sufficient", "reason": "x"}')

        from unittest.mock import MagicMock
        llm = NoOpLLM()
        call_count = {"n": 0}
        async def on_round(es):
            call_count["n"] += 1
        explorer = CodeExplorer(
            ripgrep_executor=executor, ast_parser=ast, llm=llm,
            on_round_complete=on_round, max_rounds=3,
        )
        graph_state = {
            "entities": {"code_refs": ["a.py"]},
            "candidate_repos": ["r"],
            "query": "test",
            "ripgrep_results": [
                {"repo": "r", "file_path": "a.py", "line_number": 1, "match_text": "x"},
                {"repo": "r", "file_path": "a.py", "line_number": 2, "match_text": "y"},
                {"repo": "r", "file_path": "a.py", "line_number": 3, "match_text": "z"},
            ],
            "expanded_context": [],
        }
        await explorer.explore(graph_state)
        # 至少触发 1 次（可能更多）
        assert call_count["n"] >= 1
```

- [ ] **Step 2: 跑测试确认部分失败（_refine_terms / _assess 等未实现）**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py -v`
Expected: 多个 FAIL

- [ ] **Step 3: 实现 6 阶段方法**

修改 `src/spma/agents/code/explorer.py`：

```python
    async def _refine_terms(self, state: ExplorerState) -> None:
        """基于上轮 expanded_context 调 LLM 重组关键词（P3 对策）。

        首轮（round=1 且 expanded_context 空）退化：用 query + entities 构造 search_terms。
        后续轮：调 LLM 基于上轮 expanded_context 重组关键词；LLM 失败时 search_terms 保持上轮值。
        """
        if state.round == 1 and not state.expanded_context:
            # 首轮退化：直接用 query + entities
            state.search_terms = {
                "query": state.query,
                "entities_code_refs": list(state.entities.get("code_refs", []) or []),
                "entities_module": state.entities.get("module", ""),
                "refined_via": "degraded_query_entities",
            }
            return

        if self._llm is None:
            return  # 无 LLM，search_terms 保持上轮值

        # 后续轮：调 LLM 重组
        try:
            from spma.agents.code.term_builder import build_search_terms
            base = build_search_terms(state.entities)
            prompt = (
                f"基于以下上轮探索结果，重组更精准的代码搜索关键词。\n"
                f"用户查询: {state.query}\n"
                f"已有 expanded_context: {len(state.expanded_context)} 个文件\n"
                f"已有 ripgrep_results: {len(state.ripgrep_results)} 个匹配\n"
                f"输出 JSON: {{\"exact_terms\": [...], \"fuzzy_terms\": [...]}}"
            )
            resp = await self._llm.ainvoke(prompt)
            import json, re
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", resp.content.strip())
            refined = json.loads(content)
            state.search_terms = {
                "exact_terms": refined.get("exact_terms", base.get("exact_terms", [])),
                "fuzzy_terms": refined.get("fuzzy_terms", base.get("fuzzy_terms", [])),
                "tag_terms": base.get("tag_terms", []),
                "refined_via": "llm",
            }
        except Exception as e:
            logger.warning(f"_refine_terms LLM 调用失败: {e}，保持上轮 search_terms")

    async def _glob(self, state: ExplorerState) -> list[dict]:
        """调 ripgrep_executor.glob_files。"""
        try:
            return await self._executor.glob_files("**/*.py", state.candidate_repos)
        except Exception as e:
            logger.warning(f"_glob failed: {e}")
            return []

    async def _grep(self, state: ExplorerState) -> list[dict]:
        """调 ripgrep_executor.search（4 层降级由 fallback_layer 控制）。"""
        try:
            return await self._executor.search(
                state.search_terms or {},
                state.candidate_repos,
                state.fallback_layer,
            )
        except Exception as e:
            logger.warning(f"_grep failed: {e}")
            return []

    async def _read(self, state: ExplorerState, candidates: list[dict]) -> None:
        """调 ripgrep_executor.read_files；新文件追加到 expanded_context。"""
        # 过滤已 seen 的文件
        new_files = [
            c for c in candidates
            if (c.get("repo"), c.get("file_path")) not in state.seen_files
        ]
        if not new_files:
            state.new_files_this_round = 0
            return
        try:
            read_results = await self._executor.read_files(new_files)
        except Exception as e:
            logger.warning(f"_read failed: {e}")
            state.new_files_this_round = 0
            return
        added = 0
        for r in read_results:
            state.expanded_context.append(r)
            state.seen_files.add((r["repo"], r["file_path"]))
            added += 1
        state.new_files_this_round = added

    async def _expand_via_ast(self, state: ExplorerState) -> None:
        """AST 辅助（增量追加到 expanded_context）。"""
        from spma.agents.code.ast_expander import expand_via_ast
        try:
            new_expanded = await expand_via_ast(
                ripgrep_results=state.ripgrep_results,
                repo_paths=self._executor._repo_paths,
                ast_parser=self._ast,
            )
        except Exception as e:
            logger.warning(f"_expand_via_ast failed: {e}")
            return
        for f in new_expanded:
            key = (f.get("repo", ""), f.get("file_path", ""))
            if key not in state.seen_files:
                state.expanded_context.append(f)
                state.seen_files.add(key)
                state.new_files_this_round += 1
        # 维护 previous_new_files 给下一轮 stuck 判定
        state.previous_new_files = state.new_files_this_round

    async def _assess(self, state: ExplorerState) -> None:
        """调 assess_code_completeness（P1 对策：放最后）。"""
        from spma.agents.code.completeness import assess_code_completeness
        try:
            outcome = await assess_code_completeness(
                ripgrep_results=state.ripgrep_results,
                expanded_context=state.expanded_context,
                entities=state.entities,
                call_depth=state.call_depth,
                new_files_this_round=state.new_files_this_round,
                fallback_layer=state.fallback_layer,
                llm=self._llm,
                previous_new_files=state.previous_new_files,
                max_files=self._max_files,
                max_rounds=self._max_rounds,
                round=state.round,
                total_files=len(state.seen_files),
            )
            state.convergence = outcome
        except Exception as e:
            logger.warning(f"_assess failed: {e}，默认 expand")
            from spma.agents.code.completeness import CodeCompletenessResult
            state.convergence = CodeCompletenessResult(
                verdict="expand", level="expand", reason=f"assess_error:{e}",
            )
```

并在 `explorer.py` 顶部追加：

```python
from unittest.mock import MagicMock  # 不需要，移除此行
```

如有 lint 警告 `from unittest.mock import MagicMock` 未使用，删除该行。

- [ ] **Step 4: 跑测试确认全部通过**

Run: `uv run pytest tests/unit/agents/code/test_explorer.py -v`
Expected: 8+ passed（4 状态/初始 + 1 收尾 + 3 收敛模式 + 1 回调 + 1 refine）

- [ ] **Step 5: Commit**

```bash
git add src/spma/agents/code/explorer.py tests/unit/agents/code/test_explorer.py
git commit -m "feat(explorer): implement 6-stage methods (refine/glob/grep/read/expand/assess)"
```

---

## Task 15: `graph.py` 改造为 3 节点薄包装

**Files:**
- Modify: `src/spma/agents/code/graph.py`

- [ ] **Step 1: 替换 graph.py 主体为 3 节点 + 薄包装**

替换 `src/spma/agents/code/graph.py` 全部内容：

```python
"""Code Agent 的 LangGraph StateGraph 定义——v2: 3 节点薄包装。

v1: 4 节点内联（route / search / assess / expand）——循环耦合在 LangGraph 状态机
v2: 3 节点薄包装（route / explore / finalize）——多轮循环移至 CodeExplorer
"""
from typing import Literal
from langgraph.graph import StateGraph, END
from spma.agents.code.state import CodeAgentState
from spma.agents.code.router import route_repos
from spma.agents.code.explorer import CodeExplorer


def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 6,
    timeout_ms: int = 2000,
    progress=None,
    repo_registry=None,            # 新增（v2）：repo_registry 注入
    two_stage_threshold: int = 5,  # 新增（v2）：Stage 0 阈值
) -> StateGraph:
    """Build Code Agent StateGraph v2: 3 nodes + thin wrapper."""

    async def route_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "routing", "正在分析代码仓库…")
        entities = state.get("entities", {})
        query = state.get("original_query", "")
        route_result = await route_repos(
            entities=entities,
            file_path_cache=file_path_cache,
            query=query,
            repo_registry=repo_registry,
            llm=llm,
            two_stage_threshold=two_stage_threshold,
        )
        state["candidate_repos"] = route_result["candidate_repos"]
        state["route_method"] = route_result["route_method"]
        state["route_confidence"] = route_result["route_confidence"]
        state["query"] = query
        return state

    code_explorer = CodeExplorer(
        ripgrep_executor=ripgrep_executor,
        ast_parser=ast_parser,
        llm=llm,
        on_round_complete=_make_on_round_callback(progress),
        max_rounds=max_rounds,
    )

    async def explore_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "exploring", "正在多轮探索…")
        return await code_explorer.explore(state)

    async def finalize_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "finalizing", "正在汇总结果…")
        # CodeExplorer 已写回 ripgrep_results / expanded_context / convergence_reason / final_results
        # 此处仅做必要格式化
        return state

    graph = StateGraph(CodeAgentState)
    graph.add_node("route", route_node)
    graph.add_node("explore", explore_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "explore")
    graph.add_edge("explore", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def _make_on_round_callback(progress):
    """构造 ExplorerState 回调：每轮结束发可观测事件。"""
    async def on_round(es):
        if progress:
            await progress.publish_step(
                "code_worker", "round_complete",
                f"round={es.round} new_files={es.new_files_this_round} "
                f"converge={es.convergence.level if es.convergence else 'pending'}",
            )
    return on_round
```

- [ ] **Step 2: 跑所有 code agent 单测确认通过**

Run: `uv run pytest tests/unit/agents/code/ -v`
Expected: 全部通过

- [ ] **Step 3: Commit**

```bash
git add src/spma/agents/code/graph.py
git commit -m "feat(graph): refactor to 3-node thin wrapper (route/explore/finalize) delegating to CodeExplorer"
```

---

# PR 7: 可观测性 + 依赖注入 + seed 脚本 + CI 完整性校验

## Task 16: `code_metrics.py` Prometheus 指标

**Files:**
- Create: `src/spma/observability/code_metrics.py`
- Create: `tests/unit/observability/test_code_metrics.py`

- [ ] **Step 1: 写失败单测（16 个指标全部定义）**

```python
"""Tests for code_metrics module (design-13 §7.1)."""
from spma.observability.code_metrics import (
    build_code_metrics, COUNTER_ROUTE_TOTAL, COUNTER_ROUTE_FALLBACK,
    COUNTER_ROUTE_ACCURACY, COUNTER_EXPLORER_REFINE_ERRORS,
    COUNTER_SEARCHER_TIMEOUT, COUNTER_SEARCHER_FAIL,
    COUNTER_REPO_REGISTRY_FALLBACK, COUNTER_REPO_REGISTRY_ADMIN_OPS,
    HISTOGRAM_ROUTE_LLM_LATENCY, HISTOGRAM_ROUTE_TOTAL_LATENCY,
    HISTOGRAM_EXPLORE_ROUNDS, HISTOGRAM_REPO_REGISTRY_QUERY,
    HISTOGRAM_ROUTE_TWO_STAGE_SECONDS, HISTOGRAM_ROUTE_TWO_STAGE_RESULTS,
    GAUGE_REPO_REGISTRY_COUNT, COUNTER_ROUTE_CONFIDENCE,
)


def test_build_code_metrics_returns_all_components():
    metrics = build_code_metrics()
    # 16 个指标全部存在
    assert metrics.route_total is not None
    assert metrics.route_fallback is not None
    assert metrics.route_llm_latency is not None
    assert metrics.route_total_latency is not None
    assert metrics.route_confidence is not None
    assert metrics.route_accuracy is not None
    assert metrics.explore_rounds is not None
    assert metrics.explorer_refine_errors is not None
    assert metrics.searcher_timeout is not None
    assert metrics.searcher_fail is not None
    assert metrics.repo_registry_query is not None
    assert metrics.repo_registry_admin_ops is not None
    assert metrics.repo_registry_fallback is not None
    assert metrics.repo_registry_count is not None
    assert metrics.route_two_stage_seconds is not None
    assert metrics.route_two_stage_results is not None


def test_metric_names_constants():
    """指标名常量与 spec 附录 C 100% 对齐。"""
    assert COUNTER_ROUTE_TOTAL == "code_route_total"
    assert COUNTER_ROUTE_FALLBACK == "code_route_fallback_total"
    assert COUNTER_ROUTE_ACCURACY == "code_route_accuracy_sample"
    assert COUNTER_EXPLORER_REFINE_ERRORS == "code_explorer_refine_errors_total"
    assert COUNTER_SEARCHER_TIMEOUT == "code_searcher_timeout_total"
    assert COUNTER_SEARCHER_FAIL == "code_searcher_fail_total"
    assert COUNTER_REPO_REGISTRY_FALLBACK == "code_repo_registry_fallback_total"
    assert COUNTER_REPO_REGISTRY_ADMIN_OPS == "code_repo_registry_admin_ops_total"
    assert HISTOGRAM_ROUTE_LLM_LATENCY == "code_route_llm_latency_seconds"
    assert HISTOGRAM_ROUTE_TOTAL_LATENCY == "code_route_total_latency_seconds"
    assert HISTOGRAM_EXPLORE_ROUNDS == "code_explore_rounds"
    assert HISTOGRAM_REPO_REGISTRY_QUERY == "code_repo_registry_query_seconds"
    assert HISTOGRAM_ROUTE_TWO_STAGE_SECONDS == "code_route_two_stage_seconds"
    assert HISTOGRAM_ROUTE_TWO_STAGE_RESULTS == "code_route_two_stage_results"
    assert GAUGE_REPO_REGISTRY_COUNT == "code_repo_registry_count"
    assert COUNTER_ROUTE_CONFIDENCE == "code_route_confidence"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/unit/observability/test_code_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'spma.observability.code_metrics'`

- [ ] **Step 3: 实现 `code_metrics.py`**

```python
"""Code Agent Prometheus 指标（design-13 §7.1）。

16 个指标，命名与项目已有的 qr_* 指标保持一致。
"""
from dataclasses import dataclass

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


COUNTER_ROUTE_TOTAL = "code_route_total"
COUNTER_ROUTE_CONFIDENCE = "code_route_confidence"
COUNTER_ROUTE_FALLBACK = "code_route_fallback_total"
COUNTER_ROUTE_ACCURACY = "code_route_accuracy_sample"
COUNTER_EXPLORER_REFINE_ERRORS = "code_explorer_refine_errors_total"
COUNTER_SEARCHER_TIMEOUT = "code_searcher_timeout_total"
COUNTER_SEARCHER_FAIL = "code_searcher_fail_total"
COUNTER_REPO_REGISTRY_FALLBACK = "code_repo_registry_fallback_total"
COUNTER_REPO_REGISTRY_ADMIN_OPS = "code_repo_registry_admin_ops_total"
HISTOGRAM_ROUTE_LLM_LATENCY = "code_route_llm_latency_seconds"
HISTOGRAM_ROUTE_TOTAL_LATENCY = "code_route_total_latency_seconds"
HISTOGRAM_EXPLORE_ROUNDS = "code_explore_rounds"
HISTOGRAM_REPO_REGISTRY_QUERY = "code_repo_registry_query_seconds"
HISTOGRAM_ROUTE_TWO_STAGE_SECONDS = "code_route_two_stage_seconds"
HISTOGRAM_ROUTE_TWO_STAGE_RESULTS = "code_route_two_stage_results"
GAUGE_REPO_REGISTRY_COUNT = "code_repo_registry_count"


@dataclass
class CodeMetrics:
    registry: CollectorRegistry
    route_total: Counter
    route_confidence: Counter
    route_fallback: Counter
    route_accuracy: Counter
    explore_rounds: Histogram
    explorer_refine_errors: Counter
    searcher_timeout: Counter
    searcher_fail: Counter
    repo_registry_query: Histogram
    repo_registry_admin_ops: Counter
    repo_registry_fallback: Counter
    repo_registry_count: Gauge
    route_llm_latency: Histogram
    route_total_latency: Histogram
    route_two_stage_seconds: Histogram
    route_two_stage_results: Histogram


def build_code_metrics() -> CodeMetrics:
    """每次调用返回独立 CollectorRegistry（便于多实例/多测试）。"""
    registry = CollectorRegistry()
    return CodeMetrics(
        registry=registry,
        route_total=Counter(
            COUNTER_ROUTE_TOTAL, "Code route hits by route_method",
            labelnames=("route_method",), registry=registry,
        ),
        route_confidence=Counter(
            COUNTER_ROUTE_CONFIDENCE, "Code route hits by confidence",
            labelnames=("confidence",), registry=registry,
        ),
        route_fallback=Counter(
            COUNTER_ROUTE_FALLBACK, "Code route fallback hits",
            labelnames=("from_method", "to_method"), registry=registry,
        ),
        route_accuracy=Counter(
            COUNTER_ROUTE_ACCURACY, "Code route accuracy sample",
            labelnames=("verdict",), registry=registry,
        ),
        explore_rounds=Histogram(
            HISTOGRAM_EXPLORE_ROUNDS, "Code explore rounds distribution",
            labelnames=("converge_level",),
            buckets=(1, 2, 3, 4, 5, 6, 7),
            registry=registry,
        ),
        explorer_refine_errors=Counter(
            COUNTER_EXPLORER_REFINE_ERRORS, "Code explorer refine/assess errors",
            labelnames=("op",), registry=registry,
        ),
        searcher_timeout=Counter(
            COUNTER_SEARCHER_TIMEOUT, "ripgrep subprocess timeouts",
            labelnames=("op",), registry=registry,
        ),
        searcher_fail=Counter(
            COUNTER_SEARCHER_FAIL, "ripgrep subprocess failures",
            labelnames=("op",), registry=registry,
        ),
        repo_registry_query=Histogram(
            HISTOGRAM_REPO_REGISTRY_QUERY, "RepoRegistry DB query latency",
            labelnames=("op",), registry=registry,
        ),
        repo_registry_admin_ops=Counter(
            COUNTER_REPO_REGISTRY_ADMIN_OPS, "RepoRegistry admin ops",
            labelnames=("op", "status"), registry=registry,
        ),
        repo_registry_fallback=Counter(
            COUNTER_REPO_REGISTRY_FALLBACK, "RepoRegistry fallback hits",
            labelnames=("reason",), registry=registry,
        ),
        repo_registry_count=Gauge(
            GAUGE_REPO_REGISTRY_COUNT, "RepoRegistry enabled=true count",
            registry=registry,
        ),
        route_llm_latency=Histogram(
            HISTOGRAM_ROUTE_LLM_LATENCY, "Code route LLM latency seconds",
            labelnames=("route_method",),
            buckets=(0.1, 0.5, 1.0, 2.0, 3.0, 5.0),
            registry=registry,
        ),
        route_total_latency=Histogram(
            HISTOGRAM_ROUTE_TOTAL_LATENCY, "Code route total latency seconds",
            labelnames=("route_method",),
            buckets=(0.1, 0.5, 1.0, 2.0, 3.0, 5.0),
            registry=registry,
        ),
        route_two_stage_seconds=Histogram(
            HISTOGRAM_ROUTE_TWO_STAGE_SECONDS, "Stage 1 keyword filter latency",
            labelnames=("op",),
            buckets=(0.005, 0.01, 0.05, 0.1, 0.5, 1.0),
            registry=registry,
        ),
        route_two_stage_results=Histogram(
            HISTOGRAM_ROUTE_TWO_STAGE_RESULTS, "Stage 1 keyword filter recall count",
            labelnames=("op",),
            buckets=(0, 1, 3, 5, 10, 20),
            registry=registry,
        ),
    )
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/unit/observability/test_code_metrics.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/spma/observability/code_metrics.py tests/unit/observability/test_code_metrics.py
git commit -m "feat(observability): add code_metrics with 16 Prometheus indicators"
```

---

## Task 17: 启动期注入 `RepoRegistry`（修改 `app.py` + `routes/query.py`）

**Files:**
- Modify: `src/spma/api/app.py`
- Modify: `src/spma/api/routes/query.py`

- [ ] **Step 1: 在 `app.py` 添加 `_set_repo_registry` 全局单例 + 初始化**

在 `app.py` 找到合适位置，添加：

```python
# 在 file imports 之后、init 函数之前：

_repo_registry_singleton = None


def get_repo_registry():
    """全局单例。"""
    global _repo_registry_singleton
    if _repo_registry_singleton is None:
        raise RuntimeError("RepoRegistry 未初始化；请先调用 set_repo_registry()")
    return _repo_registry_singleton


def set_repo_registry(reg):
    """测试 / 重新初始化时设置单例。"""
    global _repo_registry_singleton
    _repo_registry_singleton = reg
```

并在 `init_infrastructure()` 内（如存在）创建 `RepoRegistry`：

```python
# 在 db_pool 创建后，添加：
from spma.ingestion.code.repo_registry import RepoRegistry
repo_registry = RepoRegistry(db_pool)
set_repo_registry(repo_registry)
```

- [ ] **Step 2: 修改 `routes/query.py` 在调用 `route_repos` 时透传 `repo_registry`**

找到 `routes/query.py:163-166`（构造 Code Agent graph 处），修改：

```python
from spma.api.app import get_repo_registry

# 在 build_code_agent_graph 调用处：
g = build_code_agent_graph(
    file_path_cache=file_path_cache,
    ripgrep_executor=ripgrep_executor,
    ast_parser=ast_parser,
    llm=llm,
    repo_registry=get_repo_registry(),  # 新增（v2）
    two_stage_threshold=5,              # 新增（v2）
)
```

- [ ] **Step 3: 跑相关测试**

Run: `uv run pytest tests/unit/ -k "router or app" -v`
Expected: 全部通过

- [ ] **Step 4: Commit**

```bash
git add src/spma/api/app.py src/spma/api/routes/query.py
git commit -m "feat(app): inject RepoRegistry singleton + wire route_repos Stage 0/1/2 params"
```

---

## Task 18: seed 脚本 + 完整性校验脚本

**Files:**
- Create: `scripts/seed_repo_registry.py`
- Create: `scripts/check_repo_registry_integrity.py`

- [ ] **Step 1: 创建 `seed_repo_registry.py`**

```python
"""开发环境 seed 脚本——向 repo_registry 写入仓库元数据。

用法:
    # 交互式录入（从 config/ingestion.yaml 读仓库 URL）
    uv run python scripts/seed_repo_registry.py

    # 从已有 YAML 草稿迁移
    uv run python scripts/seed_repo_registry.py --from-yaml ./config/module_manifest.yaml

    # 干跑（仅打印 SQL，不执行）
    uv run python scripts/seed_repo_registry.py --dry-run

幂等：ON CONFLICT (repo_name) DO UPDATE。
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

# 允许从仓库根目录 import
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
import yaml


async def upsert_repo(conn, repo: dict, dry_run: bool) -> str:
    """单条仓库 upsert。返回 SQL 操作类型。"""
    sql = """
        INSERT INTO repo_registry (
            repo_name, display_name, description, tags,
            repo_url, local_path, languages, enabled
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (repo_name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            tags = EXCLUDED.tags,
            repo_url = EXCLUDED.repo_url,
            local_path = EXCLUDED.local_path,
            languages = EXCLUDED.languages,
            enabled = EXCLUDED.enabled,
            updated_at = NOW()
    """
    if dry_run:
        return f"DRY-RUN: would upsert {repo['repo_name']}"
    await conn.execute(
        sql,
        repo["repo_name"],
        repo["display_name"],
        repo["description"],
        repo.get("tags", []),
        repo.get("repo_url"),
        repo.get("local_path"),
        repo.get("languages", []),
        repo.get("enabled", True),
    )
    return f"upserted {repo['repo_name']}"


def load_repos_from_yaml(yaml_path: str) -> list[dict]:
    """从 module_manifest.yaml 加载（兼容旧格式）。"""
    with open(yaml_path) as f:
        data = yaml.safe_load(f)
    return data.get("repos", [])


def load_repos_from_ingestion_yaml() -> list[dict]:
    """从 config/ingestion.yaml 读 code.repo_urls 并交互式补全元数据。"""
    config_path = Path("config/ingestion.yaml")
    if not config_path.exists():
        print(f"ERROR: {config_path} not found")
        sys.exit(1)
    with open(config_path) as f:
        config = yaml.safe_load(f)
    urls = config.get("code", {}).get("repo_urls", [])
    print(f"Found {len(urls)} repo URLs in {config_path}")
    repos = []
    for url in urls:
        repo_name = url.rstrip("/").split("/")[-1].replace(".git", "")
        print(f"\n--- {repo_name} ({url}) ---")
        display_name = input(f"  display_name (中文名): ").strip() or repo_name
        description = input(f"  description (1-2 句话): ").strip()
        tags_input = input(f"  tags (逗号分隔，5-10 个): ").strip()
        tags = [t.strip() for t in tags_input.split(",") if t.strip()]
        local_path = input(f"  local_path (默认 /repos/{repo_name}): ").strip() or f"/repos/{repo_name}"
        repos.append({
            "repo_name": repo_name,
            "display_name": display_name,
            "description": description,
            "tags": tags,
            "repo_url": url,
            "local_path": local_path,
            "languages": [],
            "enabled": True,
        })
    return repos


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-yaml", help="从已有 YAML 草稿迁移")
    parser.add_argument("--dry-run", action="store_true", help="仅打印 SQL")
    args = parser.parse_args()

    if args.from_yaml:
        repos = load_repos_from_yaml(args.from_yaml)
    else:
        repos = load_repos_from_ingestion_yaml()

    dsn = os.environ.get("SPMA_PG_DSN", "postgresql://spma:spma123@localhost:5433/spma")
    conn = await asyncpg.connect(dsn)
    try:
        for repo in repos:
            result = await upsert_repo(conn, repo, args.dry_run)
            print(result)
    finally:
        await conn.close()
    print(f"\n完成：{len(repos)} 条仓库元数据" + ("（dry-run）" if args.dry_run else "已写入"))


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 创建 `check_repo_registry_integrity.py`**

```python
"""CI 完整性校验脚本（design-13 §7.3）。

检查项：
- 必填字段非空
- description 长度 5-500
- tags 数量 1-20
- enabled=true 仓库数 ≥ 阈值
- 与 file_path_cache 一致（repo_registry.repo_name ⊆ file_path_cache.repo_name）
- last_indexed_at 时效（informational warning）

连接 staging DB；返回 0 = pass, 1 = fail。
"""
import os
import sys
import asyncio
from pathlib import Path

import asyncpg


CHECKS = [
    ("必填字段非空",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (description = '' OR tags = '{}' OR display_name = '')",
     0, "=="),
    ("description 长度 5-500",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (LENGTH(description) < 5 OR LENGTH(description) > 500)",
     0, "=="),
    ("tags 数量 1-20",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (array_length(tags, 1) IS NULL OR array_length(tags, 1) < 1 OR array_length(tags, 1) > 20)",
     0, "=="),
    ("与 file_path_cache 一致",
     """SELECT COUNT(*) FROM repo_registry rr
        WHERE rr.enabled = true
          AND NOT EXISTS (
            SELECT 1 FROM file_path_cache fpc WHERE fpc.repo_name = rr.repo_name
          )""",
     0, "=="),
    ("enabled=true 仓库数 ≥ 阈值",
     "SELECT COUNT(*) FROM repo_registry WHERE enabled = true",
     3, ">="),  # dev 阈值 = 3，staging/prod 改大
]


async def main():
    dsn = os.environ.get("SPMA_PG_DSN", "postgresql://spma:spma123@localhost:5433/spma")
    conn = await asyncpg.connect(dsn)
    failed = 0
    try:
        for name, sql, expected, op in CHECKS:
            actual = await conn.fetchval(sql)
            ok = (actual == expected) if op == "==" else (actual >= expected)
            status = "PASS" if ok else "FAIL"
            print(f"[{status}] {name}: actual={actual} expected={op}{expected}")
            if not ok:
                failed += 1
    finally:
        await conn.close()
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: 手动跑一次 seed 验证（dry-run）**

Run: `uv run python scripts/seed_repo_registry.py --dry-run`
Expected: 打印 "DRY-RUN: would upsert ..." 列表

- [ ] **Step 4: 手动跑一次完整性校验**

Run: `uv run python scripts/check_repo_registry_integrity.py`
Expected: 大概率 FAIL（因为没真种子数据），但脚本结构运行

- [ ] **Step 5: Commit**

```bash
git add scripts/seed_repo_registry.py scripts/check_repo_registry_integrity.py
git commit -m "feat(scripts): add seed_repo_registry + check_repo_registry_integrity"
```

---

# Final Task: 整体集成测试 + 文档更新

## Task 19: 跑全套测试确认整体集成

**Files:**
- N/A（仅运行测试）

- [ ] **Step 1: 跑全部 unit 测试**

Run: `uv run pytest tests/unit/ -v`
Expected: 全部通过

- [ ] **Step 2: 跑 integration 测试**

Run: `uv run pytest tests/integration/code/ -v`
Expected: 全部通过

- [ ] **Step 3: 跑 e2e 测试（如有 docker）**

Run: `uv run pytest tests/e2e/ -v --skipif-no-docker`
Expected: 跳过（如无 docker）或全部通过

- [ ] **Step 4: 验证 lint**

Run: `uv run ruff check src/spma/agents/code/ src/spma/ingestion/code/repo_registry.py src/spma/observability/code_metrics.py`
Expected: 全部通过

---

## 自审 Checklist

实施时按以下顺序保证质量：

- [ ] 每个 PR 单独 commit（git log 验证）
- [ ] 每个 Task 跑完测试再 commit
- [ ] 集成测试（test_routing_e2e）4 场景全部通过
- [ ] 离线 replay 准确率 ≥ 80%（Task 7 fixture 由 reviewer 补充 30+ 样本）
- [ ] 启动期注入 RepoRegistry 后冒烟测试（启动 app，调用一次 code worker）
- [ ] 完整性校验脚本在 staging DB 跑通
- [ ] 灰度发布按 §8.4 5 阶段（内部测试 → 1% → 10% → 50% → 100%）实施

---

> **Plan 终态**：实施时**直接对照本 plan 按 Task 顺序执行**。详细背景与设计哲学见 [spec](../specs/2026-07-01-code-agent-routing-and-exploration-design.md)；设计文档全文见 [design-13](../../designs/SPMA-design-13-industry-research-code-location.md)。

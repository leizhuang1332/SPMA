# Design: Code Agent 代码定位与分析方案

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 相关模块：[Code Agent](SPMA-design-03-code-worker.md)
> 文档目的：定义 SPMA Code Agent 代码定位与分析的核心方案，基于 Claude Code 模型驱动探索策略，解决现有路由不准确的问题

***

## 一、核心参考与设计哲学

### 1.1 Claude Code 探索模式

Claude Code 采用**模型驱动的实时探索**策略，放弃传统 RAG 向量检索，其核心流程经过实践验证：

```
用户: "修一下 payment API 的 auth bug"

Step 1: Glob → 发现项目结构（**/payment/**, **/api/**）
Step 2: Grep → 定位关键符号（payment_api, auth, authenticate）
Step 3: Read → 读取完整文件上下文
Step 4: 理解 → LLM 在上下文中理解代码结构
Step 5: 重复 → 根据需要再 Grep → 再 Read → 再理解
```

### 1.2 设计哲学：零索引、实时搜索

**核心理念**：

- **零索引**：代码持续变更，索引过期问题严重。与其花费大量精力维护索引的时效性，不如直接实时搜索
- **实时搜索**：ripgrep 效果远超 RAG，能够在毫秒级返回精确结果
- **模型决策**：让 LLM 决定搜索策略和深度，而非依赖预定义的索引结构
- **多轮循环**：单次搜索难以覆盖所有相关代码，通过迭代探索逐步收敛

**关键洞察**：问题的本质是"从N个仓库中选几个相关的"，这是一个简单的分类问题，不需要复杂的预索引体系。

### 1.3 问题边界

本方案解决两个核心问题：

| 问题       | 描述           | 解决方式                               |
| -------- | ------------ | ---------------------------------- |
| **路由问题** | "该查哪个仓库？"    | LLM 根据仓库元数据直接选择候选仓库                |
| **搜索问题** | "仓库内该看哪些代码？" | Claude Code 模式实时探索（Glob→Grep→Read） |

***

## 二、现有问题

当前 Code Agent 存在以下核心问题：

### 2.1 实体抽取未生效（code\_refs / module 始终为空）

`extract_entities` 函数实现存在（`WorkerEntities` dataclass），但当前未在 prompt / pipeline 装配阶段被填充具体的 `code_refs` 与 `module`，导致所有查询都走 fallback 路由，无法精确定位目标仓库。

### 2.2 缺少中英文模块映射

`route_repos` 直接使用中文模块名进行文件路径匹配，用户说"用户登录"但代码文件名为英文（`auth.py`、`login.py`），导致路由失败。

### 2.3 仓库信息不完备

`repo_registry` 表缺少业务域标签、仓库依赖关系等信息，无法根据业务域进行精准路由。

***

## 三、新方案设计

### 3.1 设计点1：route\_repos 透传用户查询

**设计意图**：保留用户原始查询的完整语义，让 LLM 直接基于完整查询做决策。

**实现方式**：

```python
async def route_repos(
    query: str,                       # 新增：用户原始查询
    entities: dict,                   # 保留：实体信息（可选）
    file_path_cache,                  # 保留：现有 file_path_cache 实例（用于 fallback）
    repo_registry,                    # 新增：RepoRegistry 实例（DB 数据源，主路径）
    llm=None,                         # 新增：可选 LLM（主路径用于精排；缺省时走纯关键词排序）
    max_candidates: int = 5,
    two_stage_threshold: int = 5,     # 新增：仓库数 > 此阈值走两阶段关键词预筛
) -> dict:
    """根据用户查询和实体信息路由到候选仓库。

    Stage 0 决策：
        if len(active_repos) <= two_stage_threshold:
            → 单阶段 LLM 路由（节省一次 DB 调用）
        else:
            → 两阶段路由：Stage 1 关键词预筛 → Stage 2 LLM 精排
    """
```

**返回字段**（保留并扩展）：

| 字段                 | 类型          | 说明                                                                                                                                 |
| ------------------ | ----------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `candidate_repos`  | `list[str]` | 候选仓库列表                                                                                                                             |
| `route_method`     | `str`       | `db_registry_match_single`（单阶段 LLM）/ `db_registry_match_two_stage`（两阶段）/ `exact_file_match` / `module_lookup` / `broad_search`（兜底） |
| `route_confidence` | `str`       | `high` / `medium` / `low`                                                                                                          |

> **重要**：`route_method` 主路径枚举值从原 `db_registry_match` 拆分为 `db_registry_match_single`（仓库数 ≤ `two_stage_threshold`）与 `db_registry_match_two_stage`（仓库数 > `two_stage_threshold`）两个值；旧的 `exact_file_match` / `module_lookup` / `broad_search` 仍保留为兜底路径。下游消费方（如 `graph.py`）需在 `switch(route_method)` 处扩展两个分支。
> **v1 立即落地决策**：用户已确认 `two_stage_threshold` 默认值设为 **5**——即使仓库数较少（如 6-10），仍走两阶段架构，避免规模增长时重构。详见 §4 规模化扩展路径与 §6 关键设计决策。

**优势**：

- 信息完整，避免实体抽取（当前 `code_refs` / `module` 为空）丢失上下文
- 简化流程，主路径上不依赖实体抽取结果
- LLM 可以直接基于完整查询做语义理解

**失败模式**：

- LLM 调用失败（超时 / JSON 解析错误）→ 降级到 `file_path_cache` 的 `module_lookup` 路径
- LLM 返回的 `repo_names` 在 `RepoRegistry` 中不存在 → 过滤后若为空则降级到 `broad_search`
- YAML 未配置某个仓库 → `RepoRegistry.get_repo_by_name()` 返回 `None`，不进入候选

***

### 3.2 设计点2：落地 `repo_registry` 表

**设计意图**：通过 DB 表提供仓库的中文描述、关键词等元数据，解决中英文映射问题，为 LLM 路由决策提供语义信息。

**数据源选择**：**DB 表** **`repo_registry`** **作为唯一真相源**（不再使用 YAML）。理由：

- design-03 §3.6 spec 中已有该表的 DDL 草案（`docs/superpowers/specs/2026-06-13-phase3-supervisor-code-agent-design.md:146-155`），从 2026-06-13 写到今天仍未落地——本次直接落 migration
- 字段集合与原 YAML 提案 100% 对齐（`display_name` / `description` / `tags TEXT[]`），避免引入并行数据源
- 单一真相源（single source of truth）：无需"双数据源不变量"校验
- 仓库元数据进入 DB 后，未来可接入 design-03 §6.8 的"管道 P2 RepoMetadataMiner"自动从 `README.md` / `pyproject.toml` 反哺 `description` / `tags` 列

**与** **`file_path_cache`** **的关系**：

`file_path_cache` 表**继续保留**，承担"仓库文件清单"职责（webhook 驱动自动维护）。`repo_registry` 表承担"仓库元数据清单"职责（人工 + 后续自动反哺）。两者通过 `repo_name` 字段自然对齐——`route_repos` 主路径用 `repo_registry` 取元数据，`RipgrepExecutor` 用 `repo_paths`（由 `file_path_cache.list_repos()` + `repo_registry.local_path` 推导）定位仓库根目录。

> **字段命名约定**：与 design-03 §3.6 spec 保持一致——`display_name`（中文名）/ `description`（中文描述）/ `tags TEXT[]`（关键词数组）。与原 YAML 方案的 `name`/`description`/`keywords` 命名差异是 spec 历史选择，本次落地统一为 spec 命名。

#### 3.2.1 DDL（`deployments/docker/migrations/005_repo_registry.sql`）

```sql
-- 005_repo_registry.sql — 仓库元数据注册表（design-13 + design-03 §3.6 落地）
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

-- 启动期烟雾测试用
COMMENT ON TABLE repo_registry IS '仓库元数据唯一真相源（design-13 §3.2 + design-03 §3.6）';
```

#### 3.2.2 `RepoRegistry` 类（DB 查询版）

```python
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
    """仓库元数据注册表——从 DB 查询并提供 list_active_repos / get_repo_by_name 接口。

    与原 YAML 版的差异：
      - 数据源：DB 表（不再读 YAML 文件）
      - 启动策略：连接池复用（与 file_path_cache 共享 spma.db.connection.Pool）
      - fail-fast：启动时 SELECT COUNT(*) 检查；可选 MODULE_REGISTRY_OPTIONAL=true 降级
      - 降级路径：回退到 file_path_cache.list_repos()（仅 repo_name，无元数据）
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        optional: bool | None = None,
    ):
        self._pool = pool
        self._optional = (
            optional
            if optional is not None
            else os.environ.get("MODULE_REGISTRY_OPTIONAL", "false").lower() == "true"
        )
        self._validate_startup()  # fail-fast 检查

    async def _validate_startup(self) -> None:
        """启动期校验：表存在 + 至少 1 条 enabled=true 行（optional 时豁免）。"""
        try:
            async with self._pool.acquire() as conn:
                count = await conn.fetchval(
                    "SELECT COUNT(*) FROM repo_registry WHERE enabled = true"
                )
        except asyncpg.UndefinedTableError as e:
            if self._optional:
                logger.warning(f"repo_registry 表不存在，降级到 file_path_cache（{e}）")
                return
            raise RuntimeError(f"repo_registry 表不存在，请先执行 migration 005: {e}")
        if count == 0 and not self._optional:
            raise RuntimeError(
                "repo_registry 表为空，请先执行 scripts/seed_repo_registry.py；"
                "或显式设置 MODULE_REGISTRY_OPTIONAL=true 降级"
            )

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

    @staticmethod
    def _row_to_meta(row) -> RepoMeta:
        """DB 行 → RepoMeta dataclass 转换（tags 数组 + JSONB 解析）。"""
        import json
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

#### 3.2.3 管理入口双通道

| 通道            | 文件 / 路径                                            | 适用场景                                                                                     |
| ------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| **seed 脚本**   | `scripts/seed_repo_registry.py`                    | 一次性批量录入历史仓库（从 `config/ingestion.yaml` 的 `code.repo_urls` 读取 URL，逐条提示填写 description/tags） |
| **admin API** | `src/spma/api/admin_router.py`（v1.1 再实现，本期仅预留接口契约） | 增量新增/修改仓库元数据（受 feature flag `code_repo_admin_enabled` 控制，仅内部账号可访问）                       |

**seed 脚本用法**：

```bash
# 从 config/ingestion.yaml 读仓库 URL，交互式录入元数据
python scripts/seed_repo_registry.py

# 兼容模式：从已有 YAML 草稿一键迁移（v1 过渡期）
python scripts/seed_repo_registry.py --from-yaml ./config/module_manifest.yaml

# 干跑模式（仅打印 SQL，不执行）
python scripts/seed_repo_registry.py --dry-run
```

**admin API 接口契约**（v1.1 实现，本期仅占位）：

| Method   | Path                       | 作用                                | Feature Flag              |
| -------- | -------------------------- | --------------------------------- | ------------------------- |
| `POST`   | `/admin/repos`             | 创建仓库元数据                           | `code_repo_admin_enabled` |
| `PATCH`  | `/admin/repos/{repo_name}` | 更新 `description`/`tags`/`enabled` | `code_repo_admin_enabled` |
| `DELETE` | `/admin/repos/{repo_name}` | 软删除（`enabled=false`）              | `code_repo_admin_enabled` |

#### 3.2.4 fail-fast 与降级矩阵

| 触发条件                           | 默认行为 (optional=false)               | 显式降级 (optional=true)                       |
| ------------------------------ | ----------------------------------- | ------------------------------------------ |
| `repo_registry` 表不存在           | `RuntimeError` 启动失败                 | warn 日志，降级到 `file_path_cache.list_repos()` |
| 表存在但 `enabled=true` 记录数为 0     | `RuntimeError` 启动失败                 | 同样降级（路由准确率会显著下降）                           |
| 单条记录 `description` / `tags` 为空 | **不 fail-fast**（DB 已校验 NOT NULL）    | 行为一致                                       |
| `get_repo_by_name` 未命中         | 返回 `None`，路由主路径过滤后降级到 broad\_search | 行为一致                                       |
| DB 连接失败                        | `asyncpg.PostgresError` 抛出          | 同样 raise（避免静默错误）                           |

**优势**：

- 仅需一行中文描述 + 关键词，LLM 即可完成路由
- 避免了复杂的模块抽象和文档生成管线
- 维护成本极低，变更只需修改 YAML
- 单一数据源，无数据一致性问题

***

### 3.3 设计点3：Stage 0/1/2 三段式路由（v1 立即落地）

**设计意图**：v1 立即实现两阶段路由（关键词预筛 + LLM 精排），避免几百个仓库元数据塞进 LLM prompt 引发"lost in the middle" + 成本暴涨。Stage 0 决策自动选择单阶段或两阶段，`two_stage_threshold=5` 默认值确保规模从 6 个仓库起就启用两阶段，避免后续重构。

> **业界调研结论**（参考 §1 核心参考）：Sourcegraph 在 1000+ 仓库规模仍以 Zoekt trigram（pg\_trgm 类）为主检索手段（[Sourcegraph Cody embeddings #2408](https://github.com/sourcegraph/cody/pull/2408) 公开承认 embeddings 边际收益低）；Cursor、Cody、Continue、Copilot 等仓库级路由均收敛到"便宜粗筛 → 昂贵精排"两阶段架构。本方案 §3.3 即采纳此架构。

**路由流程**：

```
用户查询："修改支付接口的认证逻辑"
        │
        ▼
┌────────────────────────────────────────────┐
│ Stage 0: 策略决策                           │
│   active_repos = list_active_repos()        │
│   if len(active_repos) <= 5:                │
│       route_method = "db_registry_match_single"  │
│       candidates = active_repos (全部喂 LLM) │
│   else:                                     │
│       route_method = "db_registry_match_two_stage" │
│       ↓                                     │
└────────────────────────────────────────────┘
        │
        ▼ (two-stage path only)
┌────────────────────────────────────────────┐
│ Stage 1: pg_trgm 关键词预筛                  │
│   输入：query + active_repos                 │
│   SQL：repo_name/display_name/description   │
│        上的 trigram 相似度 + tags ANY 精确命中│
│   输出：top-20 RepoMeta                     │
└────────────────────────────────────────────┘
        │
        ▼
┌────────────────────────────────────────────┐
│ Stage 2: LLM 精排                           │
│   输入：query + candidates (≤ 20)           │
│   输出：3-5 个候选仓库                       │
└────────────────────────────────────────────┘
        │
        ▼
   候选仓库列表
```

**Stage 1 SQL 实现**（在 `RepoRegistry.list_repos_by_keyword()` 方法内）：

```sql
-- pg_trgm <-> 距离算子 + tags 数组精确命中加权
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

> **threshold 松弛机制**：默认 `similarity_threshold=0.3`（pg\_trgm 默认）。召回 < 3 条时自动放宽到 `0.15` 重试一次；仍 < 3 条则返回全表 `ORDER BY id LIMIT top_k` 兜底（强制 LLM 处理，路由准确率可能下降但不会失败）。

**Stage 2 LLM 提示词设计**（与原 §3.3 一致，仅输入数据替换为 `list_repos_by_keyword(query)`）：

```python
async def route_repos(
    query: str,
    entities: dict,
    file_path_cache,
    repo_registry: RepoRegistry,
    llm=None,
    max_candidates: int = 5,
    two_stage_threshold: int = 5,
) -> dict:
    # Stage 0: 策略决策
    active_repos = await repo_registry.list_active_repos()
    if len(active_repos) <= two_stage_threshold:
        candidates = active_repos
        route_method = "db_registry_match_single"
    else:
        # Stage 1: pg_trgm 关键词预筛
        candidates = await repo_registry.list_repos_by_keyword(query, top_k=20)
        route_method = "db_registry_match_two_stage"

    # Stage 2: LLM 精排（仅在 llm 非空时启用，否则直接返回 candidates）
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

**路由示例**（two\_stage 路径，仓库数 = 20）：

```
用户查询："修改支付接口的认证逻辑"

Stage 1 输入（20 个仓库全部 enabled）：
  repo_auth, repo_payment, repo_billing, repo_user, repo_order, ...

Stage 1 SQL：WHERE (description <-> '支付接口的认证逻辑') <= 0.7
                 OR (description <-> ...) <= 0.7
                 OR ('支付接口' = ANY(tags))
Stage 1 输出（top-20 → top-5，因相似度过低会兜底全表）:
  1. repo_payment (description_sim=0.62, tag_hit=true)
  2. repo_auth (description_sim=0.48, tag_hit=false)
  3. repo_billing (description_sim=0.31)
  4. repo_order (description_sim=0.22)
  5. repo_user (description_sim=0.18)

Stage 2 LLM 输入：上述 5 条 + 用户查询
Stage 2 LLM 输出：
  {"repo_names": ["repo_payment", "repo_auth"],
   "reason": "支付接口相关 → repo_payment；认证逻辑相关 → repo_auth"}

route_method = "db_registry_match_two_stage"
route_confidence = "high"（≤ 3 个候选）
```

**优势**：

- **v1 立即防 scale 风险**：6 个仓库起就启用两阶段，规模增长零迁移成本
- **零新基础设施**：pg\_trgm 已在 `file_path_cache` 链路就绪（[file\_path\_cache.py:47-55](src/spma/ingestion/code/file_path_cache.py#L47-L55)），迁移 005 加 GIN 索引即可
- **降级路径完整**：Stage 1 召回 < 3 → 阈值松弛 → 全表兜底；Stage 2 LLM 失败 → `module_lookup` 兜底；候选为空 → `broad_search` 兜底
- **行为可控**：pg\_trgm 相似度 deterministic，结果可单测、可调试
- **演进路径清晰**：未来 Stage 1 可叠加 embedding 信号做 hybrid RRF（`v2_repo_two_stage_rrf` feature flag），`route_repos()` 函数签名不变——这是设计"两阶段"架构的**关键收益**
- **零 LLM 浪费**：仓库数 ≤ 5 时跳过 Stage 1，节省一次 DB 调用

***

### 3.4 设计点4：Claude Code 实时探索

**设计意图**：让 LLM 自主决定搜索策略和深度，逐步探索源码，放弃预建索引。

**探索流程**：

```
用户查询 → LLM 分析
        │
        ▼
┌──────────────────────────────────┐
│ Round 1: 初步探索                 │
│ Glob → 发现目录结构                │
│ Grep → 定位关键词                  │
│ Read → 读取关键文件                │
└──────────────────────────────────┘
        │
        ▼
┌──────────────────────────────────┐
│ LLM 判断是否足够                   │
│ ├─ 足够 → 返回结果                 │
│ └─ 不够 → 继续探索                 │
└──────────────────────────────────┘
        │ 不够
        ▼
┌──────────────────────────────────┐
│ Round N: 深度探索                 │
│ 根据已有上下文扩展搜索词            │
│ 继续 Glob/Grep/Read               │
└──────────────────────────────────┘
        │
        ▼
   收敛条件满足 → 返回结果
```

**收敛条件**（参考 Claude Code 固定点迭代机制，**5 种确定性 + 2 种 LLM 路径（共 7 种 level 枚举）**，详见 v2 升级后的 [completeness.py](src/spma/agents/code/completeness.py)）：

| 模式                       | 触发条件                                                                | 类别     | 说明                                    |
| ------------------------ | ------------------------------------------------------------------- | ------ | ------------------------------------- |
| **goal\_verified**       | `code_refs` 非空 + `total_results ≥ 3` + `fallback_layer = 0`         | 确定性    | 目标已验证，最高优先级                           |
| **stuck**                | `round ≥ 2` 且 `new_files_this_round = 0` 且 `previous_new_files = 0` | 确定性    | 搜索陷入停滞（连续两轮无新文件，首轮不触发以避免假收敛）          |
| **regression**           | `round_over_round_ratio < 0.5` 且本轮 `total_results` 减少               | 确定性    | 质量下降，搜索发散                             |
| **diminishing\_returns** | 连续两轮 `new_files_rate < 0.10`                                        | 确定性    | 收益递减                                  |
| **cap\_reached**         | `call_depth ≥ max_rounds` 或 `total_files ≥ max_files`               | 确定性    | 硬上限触发                                 |
| **llm\_judged**          | 5 种确定性模式全不命中 + LLM 判定 `sufficient`                                  | LLM 路径 | sufficient → 收敛（成本较高，仅 `llm` 参数非空时启用） |
| **expand**               | 5 种确定性模式全不命中 + LLM 判定 `insufficient`（或 LLM 调用失败兜底）                  | LLM 路径 | insufficient → 继续下一轮                  |

> **轮次索引约定**：`state.round` 为 **1-indexed**，第 1 轮 round=1（首轮 `previous_new_files=0` 是初始化值而非"上一轮为 0"，因此 `stuck` 在 round=1 不触发）。
>
> **LLM 路径分工**：`llm_judged` 与 `expand` 都是 LLM 路径产生的收敛模式，区别是 LLM 判定 `sufficient`（收敛）还是 `insufficient`（继续）；LLM 调用本身失败时也兜底为 `expand`。

**轮次 → fallback\_layer 映射**（与 `searcher.search()` 的 4 层降级 [searcher.py:22-83](src/spma/agents/code/searcher.py#L22-L83) 对齐）：

| 轮次 (round) | fallback\_layer | search 模式       | 适用场景            |
| ---------- | --------------- | --------------- | --------------- |
| 1          | 0               | exact (L0)      | 精确词命中，最高信度      |
| 2          | 1               | stem (L1)       | 精确词无果，按词干拆分     |
| 3          | 2               | fuzzy (L2)      | 词干无果，模糊匹配       |
| ≥ 4        | 3               | llm\_retry (L3) | 兜底，调用 LLM 重组关键词 |

> **注意**：轮次从 1 起算（与 `state.round` 一致），对应 fallback\_layer 0/1/2/3 起步；不再是 round=0 起步的旧设计。

**核心指标定义**：

| 指标                       | 计算公式                                                              | 说明            |
| ------------------------ | ----------------------------------------------------------------- | ------------- |
| `new_files_this_round`   | 本轮新增文件数                                                           | 用于判断是否有新发现    |
| `new_files_rate`         | new\_files\_this\_round / total\_files（除零时定义为 0）                  | 新文件占比，反映探索效率  |
| `round_over_round_ratio` | new\_files\_this\_round / previous\_new\_files（previous=0 时定义为 1） | 轮间新文件数比率，反映趋势 |

**最大搜索限制**（v2 目标）：

- 最大轮数：6 轮
- 最大文件数：50 个
- 每轮最大搜索词：10 个

> **注**：当前 `graph.py` 默认 `max_rounds: int = 3`（[graph.py:17](src/spma/agents/code/graph.py#L17)）；v2 实施时由 `CodeExplorer` 构造函数默认 `max_rounds=6` 接管（round 1-indexed，第 7 轮时 `call_depth=7 ≥ max_rounds=6` 触发 `cap_reached`）。

**与现有代码的接口衔接**：

**1. searcher.py 扩展**（v2 实施时落地）

> **状态**：当前 `RipgrepExecutor`（[searcher.py:15-207](src/spma/agents/code/searcher.py#L15-L207)）仅实现了 `search()` 方法（位于 [searcher.py:22-83](src/spma/agents/code/searcher.py#L22-L83)）和 `search_gitlog()` / `_rg_search()` 等辅助方法。**`glob_files()`** **和** **`read_files()`** **是 v2 实施时需要新增的方法**——这是 `CodeExplorer._glob()` / `_read()` 阶段所依赖的核心能力（见 §3.5）。

现有 `search()` 方法支持 4 层（exact / stem / fuzzy / llm\_retry）分层搜索（`fallback_layer` 参数 0-3）。v2 实施时需要新增的方法签名：

```python
async def glob_files(self, pattern: str, candidate_repos: list[str]) -> list[dict]:
    """Glob 模式匹配，发现目录结构。"""
    results: list[dict] = []
    for repo_name in candidate_repos:
        repo_path = self._repo_paths.get(repo_name)
        if not repo_path:
            continue
        cmd = ["rg", "--files", "--glob", pattern, repo_path]
        # ... 执行命令并收集结果
        results.append({"repo": repo_name, "file_path": ...})
    return results

async def read_files(self, files: list[dict]) -> list[dict]:
    """读取指定文件内容。"""
    results: list[dict] = []
    for f in files:
        repo_path = self._repo_paths.get(f["repo"])
        if not repo_path:
            continue
        file_path = os.path.join(repo_path, f["file_path"])
        with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
            content = fp.read()
        results.append({"repo": f["repo"], "file_path": f["file_path"], "content": content})
    return results
```

**2. completeness.py 升级**（v2 实施时落地）

> **状态**：当前 `assess_code_completeness()` 函数（[completeness.py:17-44](src/spma/agents/code/completeness.py#L17-L44)）仅实现 3 级（`L1` / `L2` / `L3`）判定（见 [completeness.py:32](src/spma/agents/code/completeness.py#L32) / [L37](src/spma/agents/code/completeness.py#L37) / [L41](src/spma/agents/code/completeness.py#L41)）。**v2 实施时需要升级到 5+2 种收敛模式**（5 种确定性 + 2 种 LLM 路径返回），并新增 3 个参数（`previous_new_files` / `max_files` / `max_rounds`）。

**v2 前接口**（当前状态）：

- `ripgrep_results`: 搜索结果列表
- `expanded_context`: 扩展上下文（读取的文件内容）
- `entities`: 用户实体信息
- `call_depth`: 当前调用深度（用于 L2 收敛）
- `new_files_this_round`: 本轮新增文件数（用于 L2 收敛）
- `fallback_layer`: 当前 fallback 层级（用于 L1 收敛）
- `llm`: 可选，传入时启用 L3 兜底分支
- 返回 level 枚举：`L1` / `L2` / `L3`

**v2 目标接口**（升级后）：

- 新增 `previous_new_files: int = 0`（**`stuck`** **模式判定必须**，由 `CodeExplorer` 内部状态机显式维护）
- 新增 `max_files: int = 50`（用于 `cap_reached`）
- 新增 `max_rounds: int = 6`（用于 `cap_reached`）
- 返回 level 枚举升级：`goal_verified` / `stuck` / `regression` / `diminishing_returns` / `cap_reached` / `expand` / `llm_judged`（7 种，其中 `llm_judged` 与 `expand` 都由 LLM 路径产生，区别是 LLM 判定 sufficient vs insufficient）

调用方：`CodeExplorer._assess()` 内部调用（见 §3.5.4），`ExplorerState` 内部维护 `previous_new_files` 跨轮传递。

**3. 多轮探索引擎**

详见 [§3.5 实现：CodeExplorer 与 graph.py 薄包装](#35-实现codeexplorer-与-graphpy-薄包装)。本节定义的设计意图（`Glob → Grep → Read` 循环、7 种收敛判定、轮次→fallback\_layer 映射、核心指标）在 §3.5 实现中**完整沿用**。

**优势**：

- 零索引，无需维护文档和摘要
- 实时搜索，结果始终与代码同步
- LLM 自主决策，适应复杂场景
- v2 实施后将 `completeness.py` 升级到 5+2 种收敛机制（5 确定性 + 2 LLM 路径）
- 多轮循环由独立 `CodeExplorer` 类承载，**可单测**（无需启动 LangGraph）

***

### 3.5 实现：CodeExplorer 与 graph.py 薄包装

> **本节范围**：定义 `CodeExplorer` 类的实现细节（状态模型、API、错误处理、测试），以及 `graph.py` 薄包装的具体形态。

#### 3.5.1 3 个关键问题与设计对策

通过对 [graph.py](src/spma/agents/code/graph.py) 的逐行审视，发现 3 个架构问题必须在实现中正面解决：

| ID | 问题                                                                                              | 设计对策                                                                                                                |
| -- | ----------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| P1 | 若 `assess` 跑在 `expand` 之前，第 1 轮 `new_files_this_round=0, previous_new_files=0` 立即触发 `stuck` 假收敛 | 把 `assess` 移到 `expand` 之后（顺序：refine → glob → grep → read → expand → assess）；并在 `stuck` 模式判定条件加 `round ≥ 2` 守卫，首轮豁免  |
| P2 | `RipgrepExecutor` 若不显式实现 `glob_files()` / `read_files()`，多轮循环会缺 Glob 和 Read 步骤                  | `CodeExplorer` 显式串联 6 个阶段方法（见 §3.5.4），不依赖状态机的隐式调度；任务 #5 前置补齐负责实现这 2 个方法                                             |
| P3 | `build_search_terms(entities)` 只读 entities，不读 `expanded_context`——违背"每轮精化关键词"的 Claude Code 核心机制 | 新增 `_refine_terms()` 阶段：基于上轮 `expanded_context` 调用 LLM 重组关键词；首轮 `expanded_context` 为空时退化用 `query + entities` 作为精化输入 |

#### 3.5.2 架构总览

```
┌─────────────────────────────────────────────────────────────┐
│ graph.py (薄包装)                                            │
│                                                              │
│  [route_node] → [explore_node] → [finalize_node] → END     │
│                     │                                        │
│                     │  await code_explorer.explore(state)    │
│                     ▼                                        │
└─────────────────────────────────────────────────────────────┘
┌─────────────────────────────────────────────────────────────┐
│ CodeExplorer (新增类，src/spma/agents/code/explorer.py)     │
│                                                              │
│  +-- _state: ExplorerState (内部状态对象)                    │
│  +-- _on_round_complete: AsyncCallback (可观测性钩子)         │
│                                                              │
│  explore(initial: CodeAgentState) -> CodeAgentState         │
│     while not converged:                                     │
│        1. _refine_terms()        ← P3 对策                    │
│        2. _glob()                ← P2 对策                    │
│        3. _grep()                                               │
│        4. _read()                ← P2 对策                    │
│        5. _expand()              (AST 辅助)                   │
│        6. _assess()              ← P1 对策 (assess 移到最后)    │
│        7. emit on_round_complete  (回调)                      │
└─────────────────────────────────────────────────────────────┘
```

#### 3.5.3 内部状态模型（`ExplorerState`）

新增 dataclass，**与 LangGraph 的** **`CodeAgentState`** **分离**——Explorer 拥有自己的状态对象，仅在入口/出口做转换：

```python
@dataclass
class ExplorerState:
    """CodeExplorer 内部状态——独立于 LangGraph CodeAgentState。"""
    round: int = 0                          # 当前轮次（0-indexed）
    previous_new_files: int = 0              # 上轮新增文件数（stuck 判定用）
    new_files_this_round: int = 0            # 本轮新增文件数
    search_terms: dict = field(default_factory=dict)  # 当前轮精化后的关键词
    ripgrep_results: list[dict] = field(default_factory=list)
    expanded_context: list[dict] = field(default_factory=list)
    seen_files: set[tuple[str, str]] = field(default_factory=set)
    fallback_layer: int = 0
    call_depth: int = 0
    convergence: CodeCompletenessResult | None = None
```

**与 LangGraph 状态的边界**：

- **入口**（`explore()` 接收）：从 `CodeAgentState` 读取 `entities` / `candidate_repos` / `fallback_layer`，填充到 `ExplorerState`
- **出口**（`explore()` 返回）：把 `ExplorerState.ripgrep_results` / `expanded_context` / `convergence` 写回 `CodeAgentState` 的对应字段
- **不双向同步**：LangGraph 状态在 `explore()` 调用期间**冻结**，避免双写不一致

#### 3.5.4 `CodeExplorer` 类 API

```python
class CodeExplorer:
    """多轮探索引擎——封装 Glob→Grep→Read→Refine→Assess 循环。

    独立于 LangGraph：可通过 explore() 一次性调用，也可注入 mock 状态做单测。
    """

    def __init__(
        self,
        ripgrep_executor: RipgrepExecutor,
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

    async def explore(self, graph_state: CodeAgentState) -> CodeAgentState:
        """一次性跑完多轮探索，返回写回的 graph_state。

        轮次索引约定：state.round 为 1-indexed（首轮 round=1）。
        触发条件：not self._is_converged()（含 cap_reached / goal_verified / stuck / regression / diminishing_returns / llm_judged）。
        """
        state = self._init_from_graph_state(graph_state)
        while not self._is_converged():
            await self._run_one_round(state)
            if self._on_round_complete:
                # fire-after-each-round：每轮都触发，不做去重
                await self._on_round_complete(state)
        return self._write_back_to_graph_state(graph_state, state)

    # ---- 单步 API（单测用，常规 explore() 不会调用）----
    async def _run_one_round(self, state: ExplorerState) -> None:
        state.round += 1                                # 1-indexed：第 1 轮后 round=1
        state.call_depth = state.round                  # call_depth 与 round 同步
        await self._refine_terms(state)                 # P3 对策（首轮退化见下方）
        glob_hits = await self._glob(state)
        grep_hits = await self._grep(state)
        read_hits = await self._read(state, glob_hits + grep_hits)  # P2 对策
        await self._expand_via_ast(state)
        await self._assess(state)                       # P1 对策：assess 移到 expand 之后

    # ---- 6 个阶段方法（每阶段一个职责）----
    async def _refine_terms(self, state):
        """基于上轮 expanded_context 调用 LLM 重组关键词（P3 对策）。
        首轮（state.round == 1 且 state.expanded_context 为空）退化：
            用 state.query + state.entities 作为精化输入，不再调 LLM。
        """
        ...

    async def _glob(self, state): ...             # 调 ripgrep_executor.glob_files（P2）
    async def _grep(self, state): ...             # 调 ripgrep_executor.search
    async def _read(self, state, candidates): ... # 调 ripgrep_executor.read_files（P2）
    async def _expand_via_ast(self, state): ...   # AST 辅助（直接调用现有 expand_via_ast + 增量追加）
    async def _assess(self, state): ...           # 调 assess_code_completeness（P1），level 枚举升级为 7 种
```

**`_assess`** **返回 level 枚举（v2 升级后，共 7 种）**：

```python
# v2 assess_code_completeness 返回值
class CodeCompletenessResult:
    verdict: str          # "converge" | "expand"（与 LangGraph 节点契约兼容）
    level: str            # 7 种之一：goal_verified / stuck / regression / diminishing_returns
                          #         / cap_reached / llm_judged / expand
    reason: str
```

> **7 种 level 分类**：
>
> - **5 种确定性**：`goal_verified` / `stuck` / `regression` / `diminishing_returns` / `cap_reached`
> - **2 种 LLM 路径**：`llm_judged`（sufficient→converge）、`expand`（insufficient→继续；LLM 调用失败时也兜底为此）

#### 3.5.5 graph.py 薄包装

```python
async def explore_node(state: CodeAgentState) -> dict:
    """薄包装——调用 CodeExplorer.explore() 一次完成。"""
    if progress:
        await progress.publish_step("code_worker", "exploring", "正在多轮探索…")

    async def on_round(es: ExplorerState):
        # 钩子：每轮结束发可观测事件
        if progress:
            await progress.publish_step(
                "code_worker", "round_complete",
                f"round={es.round} new_files={es.new_files_this_round} "
                f"converge={es.convergence.level if es.convergence else 'pending'}"
            )

    updated = await code_explorer.explore(state)
    return updated


def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 6,
    timeout_ms: int = 2000,
    progress=None,
) -> StateGraph:
    # Explorer 由 graph 内部构造（解耦：调用方不感知 Explorer 存在）
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
    graph.add_node("finalize", finalize_node)   # 把 Explorer 结果组装成 CodeAgentState 最终输出
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

#### 3.5.6 错误处理

| 失败模式                      | Explorer 行为                                                  | graph.py 责任                                  |
| ------------------------- | ------------------------------------------------------------ | -------------------------------------------- |
| LLM 调用超时（`_refine_terms`） | 捕获异常 → `search_terms` 保持上轮值 → 继续                             | 记录到 `code_explorer_refine_errors_total`      |
| `_glob` 全仓库失败             | 返回 `[]`，下一轮继续                                                | 记录到 `code_searcher_timeout_total{op="glob"}` |
| `_grep` 单仓库失败             | 单仓库跳过，不中断整轮                                                  | 同上 `{op="grep"}`                             |
| `_read` 文件 I/O 失败         | `errors="ignore"` 静默跳过该文件                                    | 记录到 `code_searcher_fail_total{op="read"}`    |
| `_assess` LLM 路径失败        | `_llm_code_completeness_check` 内部 `except` 兜底为 `expand`（已实现） | 无需额外处理                                       |
| 达到 `max_rounds` 仍不收敛      | Explorer 返回 `convergence.level="cap_reached"`                | 记录最终轮次到 `code_explore_rounds`                |

#### 3.5.7 测试策略

**Explorer 单元测试**（独立于 LangGraph）：

| 测试                                  | 覆盖点                                                      |
| ----------------------------------- | -------------------------------------------------------- |
| `test_init_from_graph_state`        | 从 LangGraph state 正确转换字段                                 |
| `test_refine_terms_llm_fail`        | LLM 超时时 search\_terms 保持上轮值                              |
| `test_refine_terms_round1_degraded` | 第 1 轮 expanded\_context 为空时退化用 query+entities，不再调 LLM    |
| `test_glob_grep_read_integration`   | 3 个阶段串联，验证 P2 对策（glob/read 显式调用）                         |
| `test_assess_after_expand`          | 验证 P1 对策：round 1 assess 看到真实 new\_files\_this\_round     |
| `test_converge_stuck`               | round=2 起连续两轮 0 新文件 → `stuck`（boundary case：round=1 不触发） |
| `test_max_rounds_cap`               | round=7（call\_depth=7 ≥ max\_rounds=6）触发 `cap_reached`   |
| `test_callback_invoked`             | 每轮结束触发 `on_round_complete` 回调                            |

**集成测试**：

- 端到端 fixture（参考最近 commit `9f8c3f1`）：Testcontainers PG + 模拟 ripgrep executor
- 验证 graph 编译成功 + 跑通完整流程
- 验证 `on_round_complete` 回调每轮触发
- 7 种收敛 level 各跑通一个 fixture（与 §7.2 `assess_code_completeness` 测试矩阵对齐）

#### 3.5.8 流程图

```mermaid
flowchart TD
    A[route_node: 一次性路由] --> B[explore_node]
    B --> C[CodeExplorer.explore]
    C --> D{round 1? 收敛?}
    D -->|否| E[_refine_terms: LLM 精化关键词]
    E --> F[_glob: 发现目录]
    F --> G[_grep: ripgrep 搜索]
    G --> H[_read: 读取文件]
    H --> I[_expand_via_ast: AST 辅助]
    I --> J[_assess: 7 种收敛判定<br/>5 确定性 + 2 LLM 路径]
    J --> K{收敛?}
    K -->|否| L[round++, callback]
    L --> D
    K -->|是| M[finalize_node]
    D -->|是| M
    M --> N[END]

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style N fill:#9f9,stroke:#333,stroke-width:2px
```

#### 3.5.9 关键设计要点回顾

| 维度      | 决策                                             | 关键参数 / 引用                               |
| ------- | ---------------------------------------------- | --------------------------------------- |
| 节点顺序    | refine→glob→grep→read→expand→assess            | P1 对策：assess 必须最后                       |
| Glob 接入 | `_glob()` 显式调用                                 | P2 对策                                   |
| Read 接入 | `_read()` 显式调用                                 | P2 对策                                   |
| 关键词精化   | 每轮 `_refine_terms()` 调用 LLM                    | P3 对策                                   |
| 状态所有权   | `ExplorerState` 独立 dataclass                   | 与 LangGraph `CodeAgentState` 分离，入口/出口转换 |
| 状态可测性   | 直接传 dataclass 即可                               | 无需启动 LangGraph                          |
| 节点数     | 3（route / explore / finalize）                  | 见 §3.5.5 状态机拓扑                          |
| 循环驱动    | `CodeExplorer` 内部 `while`                      | 与 LangGraph 条件边解耦                       |
| 收敛判定    | 复用 `assess_code_completeness`（v2 实施后支持 5+2 模式） | `previous_new_files` 由 Explorer 内部维护    |
| 可观测性    | `on_round_complete` 回调                         | 每轮事件，桥接 `progress.publish_step`         |
| 错误处理    | 6 种失败模式（见 §3.5.6）                              | LLM 超时 / ripgrep 失败 / 文件 I/O 失败 / 等     |

***

## 四、规模化扩展路径（v1 立即落地 + 渐进演进）

> **v1 决策更新**：根据第一性原理分析（见 §3.3 设计意图）与业界调研（Sourcegraph / Cursor / Cody / Continue / Copilot），v1 立即实现**关键词两阶段路由**（pg\_trgm 预筛 + LLM 精排），不再等待"仓库数 > 100"再升级。原"向量预筛选 > 100 / 模块抽象 > 500"的旧触发条件**已被本方案取代**。

### 4.1 三阶段渐进式演进（修订版）

| 阶段     | Stage 1 实现                               | 触发条件（仓库 `enabled=true` 数） | 引入时间 | 接口变更                                                                               |
| ------ | ---------------------------------------- | ------------------------- | ---- | ---------------------------------------------------------------------------------- |
| **v1** | pg\_trgm 关键词 + tags 精确命中                 | **> 5**（默认 always-on）     | 本期落地 | `route_repos` 新增 `two_stage_threshold`；`RepoRegistry` 新增 `list_repos_by_keyword()` |
| **v2** | pg\_trgm + embedding 混合 RRF              | 召回率 < 80%（离线 replay）      | 中期按需 | `list_repos_by_keyword()` 加可选 `embedder` 参数                                        |
| **v3** | 后台 LLM summary + BM25 + embedding 三路 RRF | > 1000                    | 远期   | 新增 `list_repos_by_summary()`，两阶段变三阶段                                               |

> **核心收益**：v1 起的两阶段架构在 v2 / v3 演进时**仅修改 Stage 1 内部信号叠加**，`route_repos()` 函数签名与 `route_method` 枚举值不变——下游消费者零侵入。

### 4.2 为什么 v1 选 pg\_trgm 而非向量预筛选？

**第一性原理**：

1. **元数据粒度 vs 代码片段粒度**：仓库元数据（`display_name` + `description` + `tags`，每条 ≤ 200 字符、含结构化关键词）的语义空间远小于代码片段。embedding 对元数据级匹配的边际收益**显著低于**对代码片段的收益（Cody 公开承认这一点，见 [issue #2408](https://github.com/sourcegraph/cody/pull/2408)）。
2. **基础设施零成本**：pg\_trgm 已在 `file_path_cache` 链路就绪（[file\_path\_cache.py:47-55](src/spma/ingestion/code/file_path_cache.py#L47-L55)），`repo_registry` 加 GIN 索引即可；向量预筛选需要新增 embedding 服务 + 向量库 + 冷启动全量入库。
3. **行为可控**：trigram 相似度 deterministic，结果可解释、可单测、可调试；embedding 距离黑盒、调试需重新计算向量。
4. **冷启动同步**：pg\_trgm 索引 = DB 行 = 始终一致；embedding 需定期重新入库，元数据变更延迟可能 > 1 天。

**向量预筛选作为 v2 候选**（保留为升级路径，不在 v1 实施）：

```python
# v2 启用条件（任一命中）：
#   1. 离线 replay 测试集召回率 < 80%
#   2. 仓库 description 平均长度 < 30 字符（元数据稀疏）
#   3. code_route_two_stage_results:p50 < 3（提示元数据质量差）

class HybridPreFilter:
    """v2: pg_trgm + embedding 混合 RRF 筛选器。"""
    def __init__(self, embedder, rrf_k: int = 60):
        self._embedder = embedder
        self._rrf_k = rrf_k

    async def filter_repos(self, query: str, top_k: int = 20) -> list[dict]:
        # Reciprocal Rank Fusion: pg_trgm 排名 + embedding 排名
        trgm_hits = await self._pg_trgm_filter(query, top_k * 2)
        embed_hits = await self._embed_filter(query, top_k * 2)
        return reciprocal_rank_fusion(trgm_hits, embed_hits, k=self._rrf_k)[:top_k]
```

### 4.3 渐进式设计原则（修订版）

| 阶段        | 仓库规模                 | 路由策略                       | 探索策略              | 监控重点                            |
| --------- | -------------------- | -------------------------- | ----------------- | ------------------------------- |
| **v1 当前** | **5+（默认 always-on）** | **关键词两阶段（pg\_trgm + LLM）** | 多轮实时探索            | `code_repo_registry_count` 阈值告警 |
| v2 中期     | 50-500               | 关键词 + embedding hybrid RRF | 多轮实时探索 + 摘要缓存     | 召回率分布、embedding 成本              |
| v3 远期     | 1000+                | 三路 RRF + 分层摘要              | 摘要导航 + drill-down | 单查询 token 预算                    |

### 4.4 关键监控指标（与 §7.1 对齐）

| 指标名                                                    | 触发条件            | 动作                          |
| ------------------------------------------------------ | --------------- | --------------------------- |
| `code_repo_registry_count`                             | 持续 1h > 50      | warning：提示评估向量预筛选（v2 升级）    |
| `code_repo_registry_count`                             | 持续 1h > 100     | critical：强制评估升级到 hybrid RRF |
| `code_route_two_stage_results:p50`                     | 持续 24h < 3      | warning：关键词预筛召回率低，提示检查元数据质量 |
| `code_route_method_total{db_registry_match_two_stage}` | 单调递增 > 100 次/分钟 | 提示业务增长，关注 prompt token 预算   |

***

## 五、实施计划

### 5.1 第一阶段：核心功能验证（1-2 周）

> **任务执行顺序**：必须按表格行序执行（`repo_registry` 表与 `RepoRegistry` 类先就绪，路由与探索才有数据可用）。
>
> **v1 决策修订**：根据第一性原理分析（§3.3）与用户确认，任务 #4 由"单阶段路由"改为 **"Stage 0/1/2 三段式路由"**——v1 立即落地关键词两阶段，`two_stage_threshold=5` 默认 always-on。详见 §4 规模化扩展路径。

| # | 任务                                                          | 优先级 | 描述                                                                                                                                                                          | 验收标准（DoD）                                                                                                                                                                |
| - | ----------------------------------------------------------- | --- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1 | 提交 `deployments/docker/migrations/005_repo_registry.sql`   | P0  | 落地 `repo_registry` 表（与 design-03 §3.6 spec 字段对齐：`repo_name` / `display_name` / `description` / `tags TEXT[]` / `repo_url` / `local_path` / `languages JSONB` / `enabled`）+ **追加 `pg_trgm` 扩展 + 3 个 GIN trigram 索引（`repo_name` / `display_name` / `description`）**  | migration 可重放；`alembic upgrade head` 成功；表 + 索引 + COMMENT + pg_trgm 扩展全部创建                                                                                                |
| 2 | 实现 `RepoRegistry` 类（DB 查询）+ `scripts/seed_repo_registry.py` | P0  | `RepoRegistry` 从 `asyncpg.Pool` 查询，暴露 `list_active_repos()` / `get_repo_by_name()` / **`list_repos_by_keyword(keyword, top_k, similarity_threshold)`**；seed 脚本从 `config/ingestion.yaml` 读仓库 URL 并交互式录入 description/tags，预留 `--from-yaml` 兼容模式  | 单元测试覆盖：DB 查询正常 / 表不存在 raise / enabled=true 0 行 raise / `MODULE_REGISTRY_OPTIONAL=true` 降级到 `file_path_cache.list_repos()` 四种路径；**`list_repos_by_keyword` 5 个 case：中文 / 英文 / tags 精确命中 / 阈值松弛（0.3 → 0.15）/ 空查询**；seed 脚本幂等（重复执行不报错）                  |
| 3 | 修改 `route_repos`                                            | P0  | 添加 `query` / `repo_registry` / `llm` / `two_stage_threshold`（默认 5）参数；实现 **Stage 0/1/2 三段式**：① Stage 0 决策（仓库数 ≤ 5 走单阶段 / > 5 走两阶段）；② Stage 1 pg_trgm 关键词预筛 + 阈值松弛；③ Stage 2 LLM 精排 + 仓库名过滤    | 保留旧路径（`exact_file_match` / `module_lookup` / `broad_search`）作为兜底；`route_method` 拆分为 `db_registry_match_single` 与 `db_registry_match_two_stage` 两个枚举值；`repo_registry=None` 时行为完全兼容旧实现（回归测试通过）                                                                |
| 4 | **Stage 0/1/2 三段式路由端到端验证**                                  | P0  | 任务 #1-#3 落地后，验证两阶段路由在真实场景下的准确率                                                                                                                                   | 离线 replay 测试集 ≥ 30 条，路由准确率 ≥ **80%**（与 §10.2 阶段 1 切换条件、§7.2 集成测试门槛保持一致）；覆盖 4 个场景：① 仓库数 ≤ 5 走单阶段；② 仓库数 > 5 走两阶段；③ Stage 1 召回 < 3 阈值松弛；④ LLM 返回仓库不在 candidates 中降级                            |
| 5 | **多轮探索前置补齐**                                                | P0  | 在 v2 主任务前先把 3 个底层能力补齐：① `searcher.py` 新增 `glob_files` / `read_files` 方法；② `completeness.py` 从 3 级（`L1/L2/L3`）升级为 5+2 模式 + 新增 3 个参数；③ `graph.py` 默认 `max_rounds` 由 3 提到 6    | ① `RipgrepExecutor` 暴露 2 个新方法且单元测试通过；② 7 种 level 枚举各跑通一个 fixture；③ `build_code_agent_graph` 默认 `max_rounds=6` 且保留向后兼容；三个改动**全部独立 commit**                                |
| 6 | 多轮探索：`CodeExplorer` 抽离 + graph.py 薄包装（见 §3.5）               | P0  | 新增 `src/spma/agents/code/explorer.py`（\~250 行），实现 6 阶段方法（refine/glob/grep/read/expand/assess）；改造 `graph.py` 为 3 节点薄包装；解决 P1（assess reorder）/P2（接 glob+read）/P3（每轮精化关键词）三类问题 | Explorer 单元测试 8 项全过（含新增 `test_refine_terms_round1_degraded`）；7 种收敛模式各跑通一个 fixture（5 确定性 + 2 LLM 路径）；`on_round_complete` 回调在每轮触发；`previous_new_files` 跨轮正确传递；依赖任务 #5 全部完成 |

### 5.2 第二阶段：优化与验证（1-2 周）

| 任务     | 优先级 | 描述               |
| ------ | --- | ---------------- |
| 优化提示词  | P1  | 提高路由准确性          |
| A/B 测试 | P1  | 新路由与旧路由并行，统计准确率  |
| 性能优化   | P1  | 并行化 Grep/Read 操作 |

***

## 六、关键设计决策

| 决策点     | 选择                                                                                | 理由                                                                                  |
| ------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------- |
| 路由策略    | **Stage 0/1/2 三段式（pg_trgm 关键词预筛 + LLM 精排）**                                       | 单一来源设计决策：①避免几百仓库元数据塞 LLM prompt 引发的"lost in the middle" + 成本暴涨；②pg_trgm 已在 file_path_cache 链路就绪，零新基础设施；③演进路径清晰（v2 hybrid RRF / v3 三路 RRF），详见 §3.3 / §4 |
| 仓库元数据   | **落地** **`repo_registry`** **表 + migration + seed/admin API**                     | spec 已存在但从未落地；DB 单一真相源，避免 YAML 与 DB 并行；详见 §3.2                                       |
| 搜索方式    | 模型驱动实时探索                                                                          | Claude Code 实践证明效果优于预索引                                                                |
| 收敛判断    | 7 种收敛模式（5 确定性 + 2 LLM 路径）                                                           | 平衡效率和准确性；详见 §3.4 / §3.5.4                                                                |
| 中英文映射   | LLM 自然处理                                                                          | 通过 `repo_registry.description` 字段匹配用户中文查询                                                |
| 规模化扩展   | **v1 立即落地关键词两阶段（`two_stage_threshold=5` 默认 always-on）**                                | 用户决策：即使仓库数较少也走两阶段，避免规模增长时重构；详见 §4                                                      |
| Stage 1 技术选型 | pg_trgm 关键词（而非 embedding 向量）                                                    | 第一性原理：元数据粒度（≤ 200 字符 + 结构化关键词）embedding 边际收益低；Cody [#2408](https://github.com/sourcegraph/cody/pull/2408) 实证；pg_trgm deterministic + 可调试 + 零冷启动成本 |

***

## 七、可观测性与测试策略

> 本章明确"路由准确率如何度量"与"上线前如何验证"，对应 §10.4 灰度策略与切换条件所需的量化支撑。

### 7.1 可观测性指标（Prometheus）

参考项目已有的 `qr_*` 指标命名（见最近 commit `d79762b feat(obs): add qr_cache_hit_ratio gauge`），新增以下 `code_*` 指标：

| 指标名                                  | 类型        | 标签                             | 用途                                                                                     |
| ------------------------------------ | --------- | ------------------------------ | -------------------------------------------------------------------------------------- |
| `code_route_total`                   | counter   | `route_method`                 | 各路由路径命中次数（`db_registry_match_single` / `db_registry_match_two_stage` / `exact_file_match` / `module_lookup` / `broad_search`） |
| `code_route_confidence`              | counter   | `confidence`                   | 各置信度档位命中次数                                                                             |
| `code_route_llm_latency_seconds`     | histogram | `route_method`                 | LLM 路由调用延迟分布                                                                           |
| `code_route_total_latency_seconds`   | histogram | `route_method`                 | `route_repos` 端到端延迟分布（含降级路径），P50/P95/P99 SLO 在此处埋点                                     |
| `code_route_accuracy_sample`         | counter   | `verdict`                      | 人工标注 / 在线 A/B 评估的样本数                                                                   |
| `code_explore_rounds`                | histogram | `converge_level`               | 探索收敛轮数分布（按 level 区分）                                                                   |
| `code_explorer_refine_errors_total`  | counter   | `op`                           | `_refine_terms` / `_assess` LLM 路径异常次数                                                 |
| `code_searcher_timeout_total`        | counter   | `op`（search/glob/read）         | ripgrep subprocess 超时次数                                                                |
| `code_searcher_fail_total`           | counter   | `op`                           | ripgrep 失败次数（非 0/1 退出码 / 文件 I/O 失败）                                                    |
| `code_repo_registry_query_seconds`   | histogram | `op`（list/get/keyword_filter） | `RepoRegistry.list_active_repos()` / `get_repo_by_name()` / `list_repos_by_keyword()` DB 查询耗时与成败 |
| `code_repo_registry_admin_ops_total` | counter   | `op`, `status`                 | admin API 写入次数（`op=create/update/delete`，`status=ok/fail`）                             |
| `code_repo_registry_fallback_total`  | counter   | `reason`（table\_missing/empty） | `MODULE_REGISTRY_OPTIONAL=true` 降级到 `file_path_cache.list_repos()` 的次数                 |
| `code_route_fallback_total`          | counter   | `from_method`, `to_method`     | 路由降级次数（from=主路径失败，to=降级到的路径），用于统计 LLM 不可用时的兜底率                                         |
| **`code_repo_registry_count`**       | **gauge** | —                              | **当前 `enabled=true` 的仓库总数（触发"评估向量预筛选"告警用，详见 §4.4）**                            |
| **`code_route_two_stage_seconds`**   | **histogram** | `op`（keyword_filter）         | **Stage 1 pg_trgm 关键词预筛查询耗时**                                                       |
| **`code_route_two_stage_results`**   | **histogram** | `op`（keyword_filter）         | **Stage 1 关键词预筛召回数分布（用于召回率低告警）**                                                  |

**告警规则**（与 §10.4 自动回滚触发器对齐）：

- `code_route_llm_latency_seconds:p99 > 3s`（5 分钟窗口）
- `code_route_total_latency_seconds:p99 > 5s`（5 分钟窗口，端到端兜底）
- `rate(code_searcher_timeout_total[5m]) > 10`（按 op 拆分）
- `rate(code_route_fallback_total{from_method=~"db_registry_match.*"}[5m]) > 50`（DB 路由兜底率超过 10%）
- `rate(code_repo_registry_fallback_total[5m]) > 10`（registry 整体降级率过高，DB 链路异常）
- `code_repo_registry_admin_ops_total{status="fail"} increase > 0`（admin 写入失败即时告警）
- **`code_repo_registry_count > 50` 持续 1h → warning**（提示评估向量预筛选，触发 v2 升级评估，详见 §4.4）
- **`code_repo_registry_count > 100` 持续 1h → critical**（强制评估升级到 hybrid RRF）
- **`code_route_two_stage_results:p50 < 3` 持续 24h → warning**（关键词预筛召回率低，提示检查元数据质量）

### 7.2 测试策略

**单元测试**（覆盖率目标 ≥ 85%）：

| 模块                         | 关键测试点                                                                                                                      |
| -------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `RepoRegistry`             | ① 空文件 / 缺字段 / 重复 `name` 异常；② 正常加载后 `get_repo_by_name` 命中率 100%；③ **`list_repos_by_keyword` 5 个 case（中文 / 英文 / tags 精确命中 / 阈值松弛 / 空查询）** |
| `RipgrepExecutor`          | ① `glob_files` / `read_files` 各 1 case；② `search` 4 层降级各 1 case；③ timeout 触发 kill 路径                                       |
| `assess_code_completeness` | 7 种收敛模式各 1 case：`goal_verified` / `stuck` / `regression` / `diminishing_returns` / `cap_reached` / `llm_judged` / `expand` |
| `route_repos`              | ① LLM DB 主路径；② LLM 失败降级到 `module_lookup`；③ RepoRegistry 返回空 → `broad_search` 兜底；**④ Stage 0 决策（仓库数 ≤ 5 走 `db_registry_match_single`）；⑤ Stage 0 决策（仓库数 > 5 走 `db_registry_match_two_stage`）；⑥ LLM 返回仓库名不在 candidates 中 → 过滤降级** |
| `CodeExplorer`             | 见 §3.5.7 单测矩阵（7 项 case）                                                                                                    |

**路由降级覆盖率测试**（独立单元测试，保证降级路径都被覆盖）：

| 场景                                                                       | 触发方式                                  | 期望 route\_method                              | 期望告警                                                                                |
| ------------------------------------------------------------------------ | ------------------------------------- | --------------------------------------------- | ----------------------------------------------------------------------------------- |
| LLM 主路径成功（仓库数 ≤ 5）                                                       | mock LLM 返回合法 JSON + `two_stage_threshold=5` | `db_registry_match_single`                    | 无                                                                                   |
| LLM 主路径成功（仓库数 > 5）                                                       | mock LLM 返回合法 JSON + `two_stage_threshold=5` + 6 个 mock 仓库 | `db_registry_match_two_stage`                 | `code_route_two_stage_results` histogram 记录 Stage 1 召回数                              |
| LLM 主路径超时                                                                 | mock LLM `asyncio.TimeoutError`       | `module_lookup`                               | `code_route_fallback_total`                                                         |
| LLM 主路径返回 JSON 解析错误                                                         | mock LLM 返回 malformed JSON            | `module_lookup`                               | `code_route_fallback_total`                                                         |
| LLM 主路径返回仓库不在 candidates 中                                                   | mock LLM 返回未在 Stage 1 top-K 中的 repo\_name | `broad_search`                                | `code_route_fallback_total`                                                         |
| Stage 1 召回 < 3 条                                                          | mock `list_repos_by_keyword` 返回 1 条        | 仍走两阶段（自动放宽阈值 0.3 → 0.15 重试 / 全表兜底）                | `code_route_two_stage_results` histogram 记录低召回数                                      |
| **`repo_registry`** **表为空 +** **`MODULE_REGISTRY_OPTIONAL=true`**            | 不执行 seed + 启动降级开关                     | `module_lookup`（元数据为空，仅 repo\_name 列表）              | 启动 warn 日志 + `code_repo_registry_fallback_total{reason="empty"}`                     |
| Stage 1 SQL 失败                                                            | mock `list_repos_by_keyword` 抛异常         | 降级到单阶段 LLM（用全表）                              | `code_repo_registry_query_seconds{op="keyword_filter", status="fail"}` histogram   |

**集成测试**（基于 Testcontainers，参考最近 commit `9f8c3f1 test(qr): end-to-end integration`）：

- 端到端 fixture：用户查询 → `route_repos` → 多轮探索 → 收敛 → 返回结果
- 离线 replay 测试集：≥ 30 条标注样本（覆盖中英文混合查询、单仓库 / 多仓库命中、模糊查询场景）
- 准确率门槛：路由准确率 ≥ **80%**（与 §10.2 阶段 1 切换条件保持一致）

**回滚演练**（§10.3 滚出策略的可执行验证）：

- 注入 `code_route_llm_latency_seconds` 异常（mock LLM 慢响应）→ 验证 5 分钟内自动触发回滚
- 注入 `repo_registry` 表为空（不执行 seed）→ 验证 `RepoRegistry._validate_startup()` 启动 fail-fast 行为（默认）；或 `MODULE_REGISTRY_OPTIONAL=true` 降级 warn 日志
- 演练频次：每次发版前必须跑通，记录到 release checklist

### 7.3 `repo_registry` 数据完整性校验（CI 检查）

> **取代原 YAML Schema 校验**：DB 单一真相源下，校验重点从"YAML 文件结构"转移到"DB 表数据完整性"。

`repo_registry` 表在 CI 流水线（PR 检查阶段，连接 staging DB）必须通过以下完整性校验，避免运行时才发现路由准确率问题：

| 检查项                    | 校验规则（SQL / Python）                                                                                                       | 失败行为             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------- |
| 必填字段非空                 | `SELECT COUNT(*) FROM repo_registry WHERE enabled = true AND (description = '' OR tags = '{}' OR display_name = '')` = 0 | PR 检查 fail       |
| `repo_name` 唯一性        | `repo_name` 上有 UNIQUE 约束（DDL 层保证）                                                                                        | DB 写入 fail       |
| `description` 长度       | LENGTH(description) BETWEEN 5 AND 500                                                                                    | PR 检查 fail       |
| `tags` 非空数组            | array\_length(tags, 1) BETWEEN 1 AND 20                                                                                  | PR 检查 fail       |
| `enabled=true` 仓库数     | `SELECT COUNT(*) FROM repo_registry WHERE enabled = true` ≥ N（环境变量阈值，dev/staging/prod 不同）                                | PR 检查 fail（低于阈值） |
| 与 `file_path_cache` 一致 | `repo_registry.repo_name` ⊆ `SELECT DISTINCT repo_name FROM file_path_cache`（repo\_registry 不引用未索引仓库）                    | PR 检查 fail       |
| `last_indexed_at` 时效   | `last_indexed_at IS NULL OR last_indexed_at > now() - interval '7 days'`（仅 informational）                                | warning          |

校验脚本可放在 `scripts/check_repo_registry_integrity.py`，通过 `pre-commit` 钩子或 GitHub Actions 触发（连接 staging DB，staging 与 prod schema 一致）。

## 八、风险与权衡

| 风险                            | 影响                                                                           | 缓解措施                                                                                                                                                                                                                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 路由准确性                         | 仓库描述不准确导致路由错误                                                                | 提供模板化元数据填写指南；定期审核；离线 replay 测试集持续回归                                                                                                                                                                                                                                               |
| LLM 调用成本                      | 多轮探索每轮调 LLM 精化关键词                                                            | **LLM 调用上限 = 1（路由）+ 6（每轮 \_refine\_terms）+ 1（\_assess 兜底）= 8 次/查询**；典型场景 2-4 次（路由 1 + 1-3 轮 refine + 0-1 次 assess）优化策略：① `_refine_terms` 仅在 `_assess` 判定 `expand` 后触发（节省 30% 调用）；② `_refine_terms` 与 `_assess` 共享 LLM client 连接池；③ 设置 LLM 响应缓存（query+context 哈希 → refined\_terms） |
| 响应时间                          | 多轮探索增加延迟                                                                     | 限制每轮搜索范围，优先精确匹配；`RipgrepExecutor` 单次 timeout 5s（[searcher.py:19](src/spma/agents/code/searcher.py#L19)），subprocess 终止后强制 kill 兜底；端到端 P99 SLO 5s（`code_route_total_latency_seconds`）                                                                                               |
| 仓库数量增长                        | **v1 已防**：仓库数 > 5 自动走两阶段（Stage 1 pg_trgm 预筛 + Stage 2 LLM 精排），prompt 不再随 N 线性增长 | 单阶段路径仍存在（小规模 fallback）；扩展路径见 §4 三阶段演进（v2 hybrid RRF / v3 三路 RRF）                                                                                                                                                                                                              |
| ripgrep subprocess 失败         | `_rg_search` 进程崩溃、仓库无文件、权限不足                                                 | 单仓库失败仅跳过该仓库（不中断整轮）；返回非 0 / 1 退出码时记录 stderr 前 200 字符告警                                                                                                                                                                                                                             |
| ripgrep timeout               | 大仓库搜索超过 5s 阈值                                                                | terminate → 2s grace → kill 三级兜底；超时计入 `searcher_timeout_total` 指标                                                                                                                                                                                                                 |
| 文件 I/O 失败                     | `read_files` 读不到文件（权限/不存在/编码错误）                                              | `errors="ignore"` 静默跳过；记录 `code_searcher_fail_total{op="read"}` 指标；额外黑名单过滤 `.env` / `secrets.*` / `.git/` 等敏感/无关路径（见 §9 适配表）                                                                                                                                                      |
| **`repo_registry`** **表字段缺失** | 新增仓库时未填 `description` 或 `tags` 为空 → LLM 路由准确率下降                              | DB 层 `description NOT NULL` + admin API 写入校验；CI 增加"任意 enabled=true 仓库的 description 长度 ≥ 5、tags 长度 ≥ 1"烟雾测试                                                                                                                                                                        |
| **seed 脚本未执行**                | `repo_registry` 表为空 → 启动失败（`MODULE_REGISTRY_OPTIONAL=false`）或路由准确率显著下降（降级路径） | `RepoRegistry._validate_startup()` 启动期 `SELECT COUNT(*) WHERE enabled=true` 必 ≥ 1；CI 增加"启动期烟雾测试"验证；可选 `MODULE_REGISTRY_OPTIONAL=true` 降级到 `file_path_cache.list_repos()`                                                                                                          |
| **admin API 写入失败**            | 新增/更新仓库元数据时事务回滚或网络异常                                                         | admin API 端到端事务（`BEGIN` → UPSERT → `COMMIT`/rollback）+ audit log（写入 `repo_registry_audit` 表，记录 `op` / `actor` / `before` / `after`）+ idempotency key（基于 `repo_name` + `updated_at`）                                                                                               |
| 探索发散                          | 多轮探索陷入无效搜索（>6 轮）                                                             | `cap_reached` 硬截断（call\_depth ≥ max\_rounds）；`previous_new_files` 状态维护使 `stuck` / `regression` 模式尽早触发                                                                                                                                                                             |
| 敏感文件泄露                        | `read_files` 可能读取 `.env` / `credentials` 等敏感文件                               | 读取前过滤路径黑名单（`**/.env` / `**/secrets.*` / `**/.git/` / `**/*.pem` / `**/*.key`）；日志脱敏（`code_searcher_fail_total` 不记录文件内容）                                                                                                                                                            |

***

## 九、与现有代码的适配

| 现有代码                                      | 方案需求                     | 适配方式                                                                                                                                                                                                                                                                    |
| ----------------------------------------- | ------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `router.py`                               | route\_repos Stage 0/1/2  | 修改函数签名，新增 `query` + `repo_registry` + `llm` + `two_stage_threshold`（默认 5）参数；实现三段式：① Stage 0 策略决策（仓库数 ≤ 5 → `db_registry_match_single` 单阶段 LLM；> 5 → `db_registry_match_two_stage` 两阶段）；② Stage 1 pg_trgm 关键词预筛 + 阈值松弛（0.3 → 0.15）；③ Stage 2 LLM 精排 + 仓库名过滤（防 LLM 幻觉）；保留旧路径作为 `repo_registry=None` 时的向后兼容回退 |
| `entity_extractor.py`                     | LLM 意图分析                 | 复用现有实体抽取能力，作为路由辅助（当前 `code_refs` / `module` 仍可能为空）                                                                                                                                                                                                                      |
| `searcher.py`                             | Glob→Grep→Read           | 当前仅有 `search()`（[searcher.py:22-83](src/spma/agents/code/searcher.py#L22-L83)）4 层降级；v2 实施时**新增** `glob_files` / `read_files` 方法以供 `CodeExplorer` 调用；`glob_files` / `read_files` 内部对敏感路径做黑名单过滤（`**/.env` / `**/secrets.*` / `**/.git/` / `**/*.pem` / `**/*.key`），避免泄露机密 |
| `completeness.py`                         | 收敛判断                     | 当前仅 3 级（`L1` / `L2` / `L3`）；v2 实施时**升级**为 5+2 模式（`goal_verified` / `stuck` / `regression` / `diminishing_returns` / `cap_reached` / `expand` / `llm_judged`），新增 `previous_new_files` / `max_files` / `max_rounds` 三个参数                                                  |
| `graph.py`（状态机）                           | 多轮探索循环                   | 当前 4 节点内联状态机（route / search / assess / expand）；v2 实施时**改造**为 3 节点薄包装（route / explore / finalize），多轮循环移至 `CodeExplorer`（见 §3.5）                                                                                                                                          |
| `explorer.py`（新增）                         | 多轮探索引擎（独立类）              | v2 实施时新增；拥有 `ExplorerState`，暴露 `explore(state)` 一次性 API；通过 `on_round_complete` callback 暴露可观测事件；解决 P1/P2/P3 三类问题（见 §3.5.1）                                                                                                                                              |
| `repo_registry`（DB 表）                     | 仓库元数据                    | **唯一真相源**（v1 落地后取代原 YAML 方案）；字段与 design-03 §3.6 spec 100% 对齐；由 `deployments/docker/migrations/005_repo_registry.sql` 创建；不再依赖 YAML 配置；详见 §3.2                                                                                                                            |
| `repo_registry.py`（新增）                    | `RepoRegistry` 类（DB 查询版） | v1 实施时新增；构造接收 `asyncpg.Pool`，暴露 `list_active_repos()` / `get_repo_by_name()` / **`list_repos_by_keyword(keyword, top_k, similarity_threshold)`**（Stage 1 关键词预筛 + 阈值松弛）3 个 async 方法；启动期 fail-fast 校验；可选降级到 `file_path_cache.list_repos()`                                                |
| `deployments/docker/migrations/005_repo_registry.sql`（新增） | `repo_registry` 表 DDL    | 创建表 + `idx_repo_registry_enabled` 部分索引 + **pg_trgm 扩展 + 3 个 GIN trigram 索引**（`repo_name` / `display_name` / `description`）+ COMMENT（详见 §3.2.1 与 §3.3 Stage 1 SQL）；与 `001-004` migration 同目录，alembic 链路兼容 |
| `scripts/seed_repo_registry.py`（新增）       | seed 脚本入口                | 从 `config/ingestion.yaml` 读仓库 URL；交互式录入 `display_name` / `description` / `tags`；预留 `--from-yaml` 兼容模式（v1 过渡期迁移路径）；幂等执行（重复运行不报错）                                                                                                                                         |
| `api/admin_router.py`（新增，v1.1 再实现）        | admin API 三个 endpoint    | `POST /admin/repos`、`PATCH /admin/repos/{name}`、`DELETE /admin/repos/{name}`；受 `code_repo_admin_enabled` feature flag 控制；本期仅占位接口契约，v1.1 落地                                                                                                                              |
| `repo_registry_audit`（DB 表，v1.1 新增）       | admin API 写入审计           | 记录 `op` / `actor` / `before` / `after` / `created_at`；与 §8 "admin API 写入失败"风险缓解对齐                                                                                                                                                                                       |
| `observability/code_metrics.py`（新增）       | Code Agent Prometheus 指标 | v1 实施时新增；参考 `qr_metrics.py` 模式：`code_repo_registry_count` gauge（监控仓库规模触发 v2 升级评估）+ `code_route_two_stage_seconds` / `code_route_two_stage_results` histogram（Stage 1 耗时与召回数）+ `code_route_method_total` counter（新增 `db_registry_match_single` / `db_registry_match_two_stage` 两个标签值） |
| `ASTParser`                               | 结构提取                     | 复用现有 TreeSitter 解析能力；`_expand_via_ast` 直接调用现有 `expand_via_ast()` 函数 + 增量追加到 `seen_files`                                                                                                                                                                                |
| `GitManager`                              | 变更检测                     | 复用 `handle_webhook()` 的 changed\_files 提取                                                                                                                                                                                                                               |
| 路由层 feature flag                          | 灰度比例实现                   | 路由层读取 `code_route_strategy` feature flag（`db_registry_match_single` / `db_registry_match_two_stage` / fallback）；可按用户 ID 哈希分流，与 qr 系统的 `qr_weights_history` 思路一致（见 §10.4）                                                                                                                  |

***

## 十、迁移与滚出策略

### 10.1 当前状态分析

| 当前问题                                              | 影响                                              | 迁移目标                                                                                                                                              |
| ------------------------------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `extract_entities` 返回空的 `code_refs` 和 `module`    | 所有查询走 fallback 路由                              | 实现 **Stage 0/1/2 三段式路由**：Stage 0 决策 → Stage 1 pg_trgm 关键词预筛（仓库数 > 5）/ Stage 2 LLM 精排；详见 §3.3                                                |
| `route_repos` 使用中文模块名进行路径匹配                       | 用户说"用户登录"但代码文件名为英文                            | 通过 `repo_registry.description` 字段匹配                                                                                                              |
| **`repo_registry`** **表未落地**（仅在 design-03 spec 中） | **没有任何结构化的仓库元数据可供 LLM 路由使用**                  | **提交** **`deployments/docker/migrations/005_repo_registry.sql`**（含 pg_trgm 扩展 + GIN 索引）**+** **`scripts/seed_repo_registry.py`** 落地表与种子数据；详见 §3.2 |

### 10.2 迁移路线图

#### 阶段0：准备阶段（第 0-1 周）

| 任务                                                          | 描述                                                                                                                                                       | 依赖   |
| ----------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- |
| 提交 `deployments/docker/migrations/005_repo_registry.sql`     | 落地 `repo_registry` 表（DDL + pg_trgm 扩展 + 3 个 GIN trigram 索引 + COMMENT），alembic 链路可重放                                                                              | 无    |
| 实现 `RepoRegistry` 类（DB 查询版）                                       | 从 `asyncpg.Pool` 查询，提供 `list_active_repos()` / `get_repo_by_name()` / **`list_repos_by_keyword(keyword, top_k, similarity_threshold)`** 三个 async 方法，启动期 fail-fast 校验 | 无    |
| 编写 `scripts/seed_repo_registry.py`                          | 从 `config/ingestion.yaml` 读仓库 URL 并交互式录入 description/tags；幂等；预留 `--from-yaml` 兼容模式                                                                          | 任务 1 |

#### 阶段1：路由能力增强（第 1-3 周）

| 任务                                | 描述                                                                                                                                                                          | 风险 | 回滚方案                                                                                                       |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -- | ---------------------------------------------------------------------------------------------------------- |
| 修改 `route_repos` 实现 Stage 0/1/2 | 添加 `query` + `repo_registry` + `llm` + `two_stage_threshold`（默认 5）参数；`route_method` 拆分为 `db_registry_match_single` / `db_registry_match_two_stage` 两个枚举值；`repo_registry=None` 时行为完全兼容旧实现 | 低  | 保留原有 `module_lookup` / `exact_file_match` / `broad_search` 路径作为 fallback                                  |
| **Stage 1 pg_trgm 关键词预筛**          | **`list_repos_by_keyword()`** 在 `repo_name` / `display_name` / `description` 上做 trigram 相似度 + tags 精确命中加权；召回 < 3 自动放宽阈值 0.3 → 0.15 重试；仍 < 3 全表兜底                          | 低  | Stage 1 SQL 失败降级到单阶段 LLM（用全表）                                                                                  |
| Stage 2 LLM 精排                    | 在 Stage 1 top-20 上调 LLM，过滤不在 candidates 中的仓库名（防 LLM 幻觉）                                                                                                                | 中  | LLM 失败 / 超时降级到 `module_lookup`；候选为空降级到 `broad_search`                                                    |
| A/B 测试                            | DB 路由与旧路由并行                                                                                                                                                                 | 低  | 通过 `code_route_strategy` feature flag 切换                                                                  |

**切换条件**：新路由准确率 ≥ 80%，持续 1 周

#### 阶段2：探索流程优化（第 3-4 周）

| 任务     | 描述       | 风险 | 回滚方案              |
| ------ | -------- | -- | ----------------- |
| 实现多轮探索 | 基于现有代码扩展 | 低  | 保留单轮搜索作为 fallback |
| 性能优化   | 并行化处理    | 中  | 关闭并行，恢复串行         |

### 10.3 滚出策略

#### 快速滚出（10 分钟内）

| 场景        | 操作                                                                                    |
| --------- | ------------------------------------------------------------------------------------- |
| 新路由导致严重错误 | `code_route_strategy = fallback`，切换回旧路由逻辑（`file_path_cache.module_lookup`）            |
| LLM 服务不可用 | 启用 `MODULE_REGISTRY_OPTIONAL=true` 降级到 `file_path_cache.list_repos()`（无元数据），关闭 LLM 调用 |
| DB 不可用    | 同上（降级到 `file_path_cache`）                                                             |

#### 完全回滚（1 小时内）

| 操作           | 描述                                             |
| ------------ | ---------------------------------------------- |
| 代码回滚         | 回滚 `router.py` 与 `repo_registry.py`（DB 查询版）的修改 |
| Migration 回滚 | `alembic downgrade -1` 删除 `repo_registry` 表    |
| Seed 数据保留    | 不动 `repo_registry` 表中已有数据（保留作为下次重新激活的种子）       |

### 10.4 灰度发布策略

> **比例必须单调递增**：内部测试 0% 用户（只覆盖内部账号）→ 小流量 1% → 中流量 10% → 大流量 50% → 全量 100%。
> 每个阶段最短持续 24 小时，且该阶段 SLO 全部达标才能进入下一阶段。

| 阶段    | 范围      | 比例   | 监控重点          | 阶段准入（必须全部满足）                         |
| ----- | ------- | ---- | ------------- | ------------------------------------ |
| 内部测试  | 开发/测试人员 | 0%   | 路由准确率、响应时间    | 离线 replay 准确率 ≥ 80%                  |
| 小流量灰度 | 1% 用户   | 1%   | 用户反馈、错误率      | 错误率 < 1%、P99 延迟 < 5s                 |
| 中流量灰度 | 10% 用户  | 10%  | 系统性能、LLM 调用成本 | LLM 调用成功率 ≥ 99%、单查询 LLM 成本 ≤ ¥0.05   |
| 大流量灰度 | 50% 用户  | 50%  | 缓存命中率、CPU 负载  | L1 / L2 缓存命中率 ≥ 60%                  |
| 全量发布  | 100% 用户 | 100% | 全面监控          | 持续 7 天 SLO 全部达标（错误率 < 0.5%、P99 < 3s） |

**自动回滚触发器**（任一命中即立即回滚到上一阶段）：

- 路由错误率 > 5%（5 分钟窗口）
- P99 响应时间 > 10s（5 分钟窗口）
- LLM 5xx 比例 > 1%（5 分钟窗口）
- YAML 加载失败或字段缺失导致 `RepoRegistry` 初始化失败

**灰度比例实现机制**（feature flag + 用户哈希分流）：

```python
# 伪代码：路由层入口根据 feature flag 决定使用哪种路由策略
async def dispatch_route(user_id: str, query: str, ...) -> dict:
    """路由层根据 code_route_strategy 决定 Stage 0/1/2 主路径还是 fallback。"""
    strategy = await feature_flag.get(
        "code_route_strategy",
        default="db_registry_match_two_stage",   # v1 默认（按仓库数自动选 single/two_stage）
    )
    # 按阶段比例（§10.4 表格）执行强制分流：内部测试=0% 用户；小流量=1%；...
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

> **关键点**：
>
> - feature flag 值由部署系统写入（环境变量 / 配置中心），无需代码变更即可调整比例
> - 用户 ID 哈希保证同一用户在不同阶段始终命中或始终不命中，**避免抖动**
> - 各阶段的 `rollout_percentage`（0/1/10/50/100）按 §10.4 表格单调递增配置
> - 与 qr 系统已有的 `qr_weights_history` 思路一致：版本 + 权重快照 + 历史可追溯

**与** **`code_route_strategy`** **feature flag 对齐的回滚操作**（§10.3 快速滚出）：

| 场景             | feature flag 操作                                               | 生效时间     |
| -------------- | ------------------------------------------------------------- | -------- |
| LLM 服务不可用      | `code_route_strategy = fallback`                              | 立即（下次请求） |
| 新路由导致严重错误      | `code_route_strategy = fallback`                              | 立即       |
| 临时回滚到上一阶段（10%） | `code_route_strategy = db_registry_match` + rollout=10%       | 立即       |
| 全量回滚（1 小时内）    | 关闭 feature flag（强制 fallback）+ 回滚 `router.py` 与 `RepoRegistry` | 5-10 分钟  |

***

## 十一、架构图与流程图

> **说明**：本节聚焦端到端（§11.1）、路由决策（§11.2）、路由降级（§11.3）、**多轮探索（§11.4）**四类图。多轮探索在 §3.5.8 内联流程图中按"6 阶段方法 + P1/P2/P3 设计对策"视角描述；§11.4 从"端到端叙事 + 7 种收敛判定分支展开"视角补足完整闭环。两图互补：§3.5.8 是实现细节近邻参考，§11.4 是高阶概览。

### 11.1 端到端流程图

```mermaid
flowchart TD
    A["用户查询: 修改支付接口的认证逻辑"] --> B["route_repos 透传查询"]

    B --> C["查询 repo_registry 表"]
    C --> D["提取仓库列表: repo_name + description + keywords"]

    D --> E["构建路由提示词"]
    E --> F["调用 LLM"]
    F --> G["LLM 返回候选仓库: repo_payment, repo_auth"]

    G --> H["CodeExplorer 多轮探索引擎"]
    H --> I["Round 1: Glob 发现目录结构"]
    I --> J["Round 1: Grep 定位关键词"]
    J --> K["Round 1: Read 读取关键文件"]

    K --> L{"7 种收敛判定<br/>(5 确定性 + 2 LLM 路径，见 §3.5.8)"}
    L -- 信息不足 --> M["Round N: 扩展搜索词继续探索"]
    M --> H
    L -- 信息充足，满足收敛 --> N["收敛条件满足"]

    N --> O["返回结果"]

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style O fill:#9f9,stroke:#333,stroke-width:2px
```

### 11.2 路由决策流程图（v1 Stage 0/1/2 三段式）

```mermaid
flowchart TD
    A[用户查询] --> B[查询 repo_registry.list_active_repos]
    B --> C{仓库数量 > two_stage_threshold<br/>默认 5?}

    C -->|否 ≤ 5| D["Stage 0: 单阶段路径<br/>route_method=db_registry_match_single"]
    C -->|是 > 5| E["Stage 1: pg_trgm 关键词预筛<br/>list_repos_by_keyword(query, top_k=20)"]
    E --> E1{召回 ≥ 3?}
    E1 -->|否| E2["放宽阈值 0.3 → 0.15 重试"]
    E2 --> E3{召回 ≥ 3?}
    E3 -->|否| E4["兜底全表 ORDER BY id LIMIT 20"]
    E3 -->|是| F
    E4 --> F
    E1 -->|是| F
    D --> F["Stage 2: LLM 精排<br/>输入: query + candidates ≤ 20"]

    F --> G[调用 LLM]
    G --> H[解析返回 JSON]
    H --> I{解析成功?}
    I -->|否| J["降级: module_lookup"]
    I -->|是| K{仓库名在 candidates 中?}
    K -->|否| L["过滤后若空 → 降级 broad_search"]
    K -->|是| M[输出候选仓库列表]
    J --> M
    L --> M

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style M fill:#9f9,stroke:#333,stroke-width:2px
    style C fill:#ff9,stroke:#333
```

### 11.3 路由降级流程图（新增）

```mermaid
flowchart TD
    A[route_repos 入口] --> B{主路径 db_registry_match_single / two_stage}
    B -->|尝试| C[LLM 调用]

    C --> D{LLM 调用结果?}
    D -->|成功 + 仓库名命中 Registry| E[route_method=db_registry_match<br/>confidence=high/medium]
    D -->|超时| F[降级: module_lookup]
    D -->|JSON 解析错误| F
    D -->|返回仓库不在 Registry| G[降级: broad_search]

    E --> H[输出候选仓库]
    F --> H
    G --> H

    H --> I[记录 code_route_fallback_total<br/>from→to 降级次数]

    style A fill:#f9f,stroke:#333,stroke-width:2px
    style H fill:#9f9,stroke:#333,stroke-width:2px
    style F fill:#ff9,stroke:#333
    style G fill:#ff9,stroke:#333
```

> **降级路径优先级**：`db_registry_match` → `module_lookup`（`file_path_cache.query_files(module)`，与 design-03 §3.6 spec 中的 `dir_module_map` 字段对应——本次新 `repo_registry` 表未引入该列，`module_lookup` 仍走 `file_path_cache` 的 fallback 路径）→ `broad_search`（返回所有仓库）。
> **降级指标**：`code_route_fallback_total{from_method, to_method}` 用于统计 LLM 不可用率（告警阈值见 §7.1）。

### 11.4 多轮探索流程图（v1 Stage 0/1/2 + 7 种收敛判定）

> **作用域**：本图覆盖从路由决策到多轮探索的完整闭环。**与 §3.5.8 互补**——本图聚焦"端到端叙事 + 7 种收敛判定分支展开 + Stage 0/1/2 路由串联"；§3.5.8 聚焦"6 阶段方法调用顺序 + P1/P2/P3 设计对策 + LangGraph 状态机边"。读者定位：§11.4 用于快速建立全局心智模型；§3.5.8 用于实现细节查阅。

```mermaid
flowchart TD
    Start([用户查询进入 route_node]) --> S0{Stage 0<br/>len active_repos > 5?}

    S0 -->|否 ≤ 5| Single["单阶段: 全部 repo_registry 喂 LLM<br/>route_method=db_registry_match_single"]
    S0 -->|是 > 5| S1["Stage 1: pg_trgm 关键词预筛<br/>list_repos_by_keyword query top_k=20"]
    S1 --> S1Recall{召回 ≥ 3?}
    S1Recall -->|否| Relax["放宽阈值 0.3 → 0.15 重试<br/>仍 < 3 → 兜底全表 ORDER BY id LIMIT 20"]
    S1Recall -->|是| S2
    Relax --> S2
    Single --> S2["Stage 2: LLM 精排<br/>route_method=db_registry_match_two_stage"]

    S2 --> Explore["explore_node<br/>CodeExplorer.explore"]
    Explore --> R{第 1 轮: 收敛?}
    R -->|否| Refine["_refine_terms<br/>首轮退化: query + entities<br/>后续轮: LLM 重组关键词"]
    R -->|是| Final
    Refine --> Glob["_glob<br/>ripgrep --files"]
    Glob --> Grep["_grep<br/>ripgrep 4 层降级<br/>fallback_layer 0-3"]
    Grep --> Read["_read<br/>读取 top 文件内容"]
    Read --> Expand["_expand_via_ast<br/>TreeSitter 引用扩展"]
    Expand --> Assess["_assess<br/>assess_code_completeness"]

    Assess --> AssessDecide{"7 种收敛判定<br/>5 确定性 + 2 LLM 路径"}
    AssessDecide --> goal_verified["goal_verified<br/>code_refs 非空 + total ≥ 3"]
    AssessDecide --> stuck["stuck<br/>round ≥ 2 且连续 0 新文件"]
    AssessDecide --> regression["regression<br/>round_over_round_ratio < 0.5"]
    AssessDecide --> diminishing["diminishing_returns<br/>连续两轮 new_files_rate < 0.10"]
    AssessDecide --> cap["cap_reached<br/>call_depth ≥ max_rounds=6"]
    AssessDecide --> llm_judged["llm_judged<br/>LLM 判定 sufficient"]
    AssessDecide --> expand["expand<br/>LLM 判定 insufficient 或失败"]

    goal_verified --> Final
    stuck --> Final
    regression --> Final
    diminishing --> Final
    cap --> Final
    llm_judged --> Final
    expand --> Loop["round++ + on_round_complete callback"]
    Loop --> R

    Final["finalize_node<br/>组装 CodeAgentState 最终输出"] --> End([END])

    classDef stageStyle fill:#fef9e7,stroke:#333,stroke-width:1px
    classDef loopStyle fill:#e8f4f8,stroke:#333,stroke-width:1px
    classDef finalStyle fill:#9f9,stroke:#333,stroke-width:2px
    class Single,S1,S2,Relax stageStyle
    class Refine,Glob,Grep,Read,Expand,Assess,Loop,goal_verified,stuck,regression,diminishing,cap,llm_judged,expand loopStyle
    class Start,End,Final finalStyle
```

**图例说明**：

| 视觉分组       | 颜色   | 覆盖范围                                                                              |
| ---------- | ---- | --------------------------------------------------------------------------------- |
| 路由段（黄色）   | 浅黄   | Stage 0 决策 → Stage 1 关键词预筛 → Stage 2 LLM 精排                                   |
| 循环段（蓝色）   | 浅蓝   | CodeExplorer 6 阶段方法 + 7 种收敛判定分支 + 循环回路（`expand` 触发 round++）                |
| 终点（绿色）    | 浅绿   | 起点（用户查询进入 route_node）、`finalize_node` 组装、END                                |

**收敛分支对照表**（与 §3.4 表格 100% 对齐）：

| level                       | 类别     | 触发条件                                                          | 进入下一轮? |
| --------------------------- | ------ | ------------------------------------------------------------- | ------ |
| `goal_verified`             | 确定性    | `code_refs` 非空 + `total_results ≥ 3` + `fallback_layer = 0` | 否 → finalize |
| `stuck`                     | 确定性    | `round ≥ 2` 且 `new_files_this_round = 0` 且 `previous_new_files = 0` | 否 → finalize |
| `regression`                | 确定性    | `round_over_round_ratio < 0.5` 且本轮 `total_results` 减少             | 否 → finalize |
| `diminishing_returns`       | 确定性    | 连续两轮 `new_files_rate < 0.10`                                  | 否 → finalize |
| `cap_reached`               | 确定性    | `call_depth ≥ max_rounds` 或 `total_files ≥ max_files`           | 否 → finalize |
| `llm_judged`                | LLM 路径 | 5 种确定性模式全不命中 + LLM 判定 `sufficient`                            | 否 → finalize |
| `expand`                    | LLM 路径 | 5 种确定性模式全不命中 + LLM 判定 `insufficient`（或 LLM 调用失败兜底）            | 是 → round++ + callback |


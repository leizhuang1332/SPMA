# Design: 数据摄入管道设计（离线/异步）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 模块职责：将三种异构数据源（PRD 文档、代码仓库、SQL 数据库）持续同步到 PGVector 向量数据库和元数据表，保持知识新鲜度

---

## 数据摄入全景

```
PRD 文档 (Confluence/Wiki)
  → Docling/Unstructured 解析
  → 递归语义分块（按段落+标题自然边界切割，目标 ~500 tokens/块，50-token overlap）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：Webhook（Confluence 页面更新事件）或定时全量同步（每日凌晨）

代码仓库 (Git)
  → 两路极简输出:
      ├─ file_path_cache 表 — 用于 Code Agent Phase 0 文件路径路由（500→5 仓库）
      └─ code_metadata 表 — 只存调用图（calls/called_by/imports），不存源代码
  → 表结构详见 [Code Agent 设计](SPMA-design-03-code-worker.md)
  → 触发: Git webhook push → git pull → git ls-files / TreeSitter 解析

SQL 数据库 (PostgreSQL/MySQL)
  → Schema 自省 → DDL + 列注释 + 业务元数据 → BGE-M3 嵌入 → PGVector
  → 触发方式：定时轮询 information_schema（10min 间隔）+ 手动触发刷新
```

---

## 一、PRD 文档摄入管道

### 1.1 解析
使用 Docling 或 Unstructured 库解析 Confluence/Wiki 页面。支持 HTML 富文本（保留标题层级、表格、列表结构）和 Markdown 纯文本。

### 1.2 分块策略
**递归语义分块：** 先按一级标题切 → 二级标题切 → 段落切，优先在自然边界切割。

| 参数 | 值 |
|------|-----|
| Chunk size | ~500 tokens（tiktoken cl100k_base） |
| Overlap | 50 tokens |
| 分隔符优先级 | `\n## ` > `\n### ` > `\n\n` > `\n` > `。` |

### 1.3 元数据提取
每个 chunk 附带：`req_id`（需求ID）、`doc_type`（PRD/技术方案/接口文档/会议纪要）、`version`、`updated_at`、`source_url`。

### 1.4 触发方式
- **增量更新：** Confluence Webhook（页面创建/更新/删除事件）→ 实时或准实时同步
- **全量同步：** 每日凌晨定时任务，兜底增量遗漏

---

## 二、代码摄入管道

> **架构对齐：** Code Agent 使用 ripgrep 实时搜索（不建内容索引），摄入管道仅输出两路数据：文件路径缓存（用于仓库路由）+ 调用图元数据（用于上下文扩展）。不存储源代码——检索时通过 read_file 实时读取。表结构和维护细节见 [Code Agent 设计](SPMA-design-03-code-worker.md)。

### 2.1 输出清单

| 输出 | 用途 | 数据量 | 触发 |
|------|------|--------|------|
| `file_path_cache` 表 | Code Agent Phase 0 文件路径路由 | ~40MB（含索引） | Git webhook push → `git ls-files` → upsert |
| `code_metadata` 表 | Code Agent Phase 2 AST 调用图扩展 | — | Git webhook push → TreeSitter 解析变更文件 |
| 文件系统工作副本 | Phase 1 ripgrep + Phase 2 read_file | — | 服务启动时 clone；运行时 webhook → pull |

---

## 三、SQL Schema 摄入管道

### 3.1 Schema 自省
从 `information_schema` 提取表名、列名、数据类型、列注释、外键关系。

### 3.2 嵌入内容
每条表的 DDL + 列注释 + 外键关系 + 业务元数据作为 embedding chunk。业务元数据的额外来源：
- 数据库列注释（已有，直接提取）
- 代码中的 enum 定义（AST 提取，见 [Code Agent](SPMA-design-03-code-worker.md)）
- 数据字典/PRD 文档（跨源关联，见 [Doc Agent](SPMA-design-02-doc-worker.md)）

### 3.3 Few-Shot 示例采集
从 `pg_stat_statements` 采样 Top 100 高频查询作为 few-shot 示例，需人工 curator 审核业务过滤条件和聚合逻辑。详见 [SQL Agent 设计](SPMA-design-04-sql-worker.md)。

### 3.4 触发方式
- **定时轮询：** 每 10 分钟检查 `information_schema` 变更
- **手动触发：** DDL 变更后手动刷新（提供 API 和 Web UI）
- **知识新鲜度目标：** Schema 变更 < 10 分钟内可检索

---

## 四、摄入调度

使用 APScheduler + PG 队列（替代 Kafka——2-3 人团队无需维护消息队列）。

| 数据源 | 触发方式 | 延迟目标 |
|--------|---------|---------|
| PRD 文档 | Webhook + 每日全量 | 增量 < 5min |
| 代码仓库 | Git Webhook（push 事件）| < 5min |
| SQL Schema | 定时轮询 10min + 手动触发 | < 10min |

---

## 五、同义词映射表的冷启动

同义词映射表是查询标准化的基础数据，冷启动阶段的数据来源：

| 数据来源 | 提取的映射项 | 示例 |
|---------|-------------|------|
| 数据库 `information_schema` | 表名 ←→ 表注释、列名 ←→ 列注释 | `users ←→ 用户信息表` |
| PRD 文档标题/目录 | 模块名 ←→ 需求ID | `用户登录 ←→ REQ-2024-0187` |
| Git 仓库目录结构 | 目录名 ←→ 模块名 | `src/auth/ ←→ 认证模块` |
| 人工补充 | 常见口语/缩写 | `挂了 ←→ 服务不可用` |

预计冷启动可自动生成 60-80 条映射，人工补充 20-30 条，总量 80-110 条。

> 同义词映射表的持续维护机制（自动发现 + 人工审核 + 衰变检查）详见 [Supervisor Agent 设计 - 查询改写](SPMA-design-01-supervisor-agent.md#映射表维护人工种子--自动发现--人工审核闭环)。

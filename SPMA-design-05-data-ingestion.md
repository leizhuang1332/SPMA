# Design: 数据摄入管道设计（离线/异步）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 模块职责：将三种异构数据源（PRD 文档、代码仓库、SQL 数据库）持续同步到 PGVector 向量数据库和元数据表，保持知识新鲜度

---

## 数据摄入全景

```
PRD 文档 (Confluence/Wiki)
  → Docling/Unstructured 解析
  → 递归语义分块（按段落+标题自然边界切割，目标 ~500 tokens/块，50-token overlap；使用 tiktoken cl100k_base tokenizer）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：Webhook（Confluence 页面更新事件）或定时全量同步（每日凌晨）

代码仓库 (Git)
  → 两路极简输出:
      ├─ file_path_cache 表（git ls-files → 文件路径列表，~25MB）
      │    用于 Code Agent Phase 0 文件路径路由（500→5 仓库）
      │    触发: Git webhook push → git pull → git ls-files → upsert（~100ms/仓库）
      │
      └─ code_metadata 表（TreeSitter AST → 调用图元数据）
           只存调用图（calls/called_by/imports），不存源代码
           用于 Code Agent Phase 2 AST 调用图扩展
           触发: Git webhook push → TreeSitter 解析变更文件 → upsert 调用图
  
  此外: 文件系统工作副本（git clone）
        用于 Code Agent Phase 1 ripgrep 实时搜索 + Phase 2 read_file
        触发: 服务启动时 git clone；运行时 git webhook → git pull

SQL 数据库 (PostgreSQL/MySQL)
  → Schema 自省（表结构、列类型、外键关系、列注释作为业务语义描述）
  → DDL + pg_stat_statements 采样（Top 100 高频查询作为 few-shot 示例；需 DBA 配合开通 pg_stat_statements 扩展）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：定时轮询 information_schema（10min 间隔）+ 手动触发刷新（DDL 变更后）
```

---

## 一、PRD 文档摄入管道

### 1.1 解析

使用 Docling 或 Unstructured 库解析 Confluence/Wiki 页面。支持 HTML 富文本（保留标题层级、表格、列表结构）和 Markdown 纯文本。

### 1.2 分块策略

**递归语义分块：** 先按一级标题切 → 二级标题切 → 段落切，优先在自然边界（段落边界、标题边界）切割，目标 ~500 tokens/块。

关键参数：
- Chunk size: ~500 tokens（tiktoken cl100k_base tokenizer）
- Overlap: 50 tokens
- 分隔符优先级: `\n## ` > `\n### ` > `\n\n` > `\n` > `。`

### 1.3 元数据提取

每个 chunk 附带：
- `req_id`: 从文档标签或标题中提取的需求 ID（如 REQ-2024-0187）
- `doc_type`: PRD / 技术方案 / 接口文档 / 会议纪要
- `version`: 文档版本号
- `updated_at`: 文档最后修改时间
- `source_url`: 原始文档链接

### 1.4 触发方式

- **增量更新：** Confluence Webhook（页面创建/更新/删除事件）→ 实时或准实时同步变更
- **全量同步：** 每日凌晨定时任务，兜底增量遗漏

---

## 二、代码摄入管道

> **架构对齐：** Code Agent 使用 ripgrep 实时搜索（不建内容索引），摄入管道仅输出两路数据：文件路径缓存（用于仓库路由）+ 调用图元数据（用于上下文扩展）。不存储源代码——检索时通过 read_file 实时读取。

### 2.1 文件路径缓存

一张极简的表，只存文件路径，不存内容：

```sql
CREATE TABLE file_path_cache (
    id BIGSERIAL PRIMARY KEY,
    repo_name TEXT NOT NULL,           -- auth-service
    file_path TEXT NOT NULL,           -- src/auth/oauth.py
    file_type TEXT,                    -- py, java, ts, yaml, sql, ...
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(repo_name, file_path)
);

-- 索引
CREATE INDEX idx_fpc_repo ON file_path_cache (repo_name);
CREATE INDEX idx_fpc_path_trgm ON file_path_cache 
    USING GIN (file_path gin_trgm_ops);  -- 支持 LIKE '%auth%'
CREATE INDEX idx_fpc_type ON file_path_cache (file_type);
```

**规模估算：** 500 仓库 × 平均 1000 文件/仓库 = 50 万行 × ~50 字节 = ~40MB（含索引）。

**维护方式：** Git webhook push → `git pull` → `git ls-files` → DELETE + INSERT 该仓库记录。单个 1000 文件仓库刷新 < 100ms。

### 2.2 调用图元数据

TreeSitter AST 解析，只提取调用图，不存源代码：

```sql
CREATE TABLE code_metadata (
    id UUID PRIMARY KEY,
    
    -- 位置（用于关联 ripgrep 结果和调用图）
    repo TEXT NOT NULL,
    file_path TEXT NOT NULL,
    function_name TEXT,
    class_name TEXT,
    line_start INTEGER,
    line_end INTEGER,
    
    -- AST 调用图（核心保留部分）
    calls TEXT[],          -- 该函数调用了谁
    called_by TEXT[],      -- 谁调用了该函数
    imports TEXT[],        -- 该文件的 import 列表
    
    -- 跨源关联
    req_ids TEXT[],        -- git log 中关联的需求 ID
    commit_hash TEXT,
    updated_at TIMESTAMP
);

-- 索引：只用于按文件路径快速查找调用图
CREATE INDEX idx_cm_file ON code_metadata (repo, file_path);
CREATE INDEX idx_cm_req_ids ON code_metadata USING GIN (req_ids);
```

**为什么不存源代码了：**
- Code Agent 的 ripgrep 能找到代码位置（文件路径 + 行号）
- read_file 能读取完整源代码（包括 import、装饰器、模块常量——比 chunk 更完整）
- 存源代码的维护成本（每次 commit 需重新提取和存储函数体）远高于"用时再读"

### 2.3 触发方式

- **文件路径缓存：** Git Webhook（push 事件）→ `git pull` → `git ls-files` → upsert（~100ms/仓库）
- **调用图元数据：** Git Webhook（push 事件）→ TreeSitter 解析变更文件 → upsert 调用图
- **文件系统工作副本：** 服务启动时 `git clone`；运行时 Git Webhook → `git pull`。用于 Code Agent Phase 1 ripgrep 实时搜索

---

## 三、SQL Schema 摄入管道

### 3.1 Schema 自省

从 `information_schema` 提取：

```sql
SELECT 
    t.table_name,
    t.table_comment,
    c.column_name,
    c.data_type,
    c.column_comment,
    c.is_nullable,
    c.column_default
FROM information_schema.tables t
JOIN information_schema.columns c ON t.table_name = c.table_name
WHERE t.table_schema = 'public'
ORDER BY t.table_name, c.ordinal_position;
```

### 3.2 嵌入内容

每条表的 DDL 语句 + 列注释 + 外键关系 + 业务元数据作为 embedding chunk。

业务元数据的额外来源：
- 数据库列注释（已有，直接提取）
- 代码中的 enum 定义（AST 提取）
- 数据字典/PRD 文档（跨源关联）

### 3.3 Few-Shot 示例采集

从 `pg_stat_statements` 采样 Top 100 高频查询作为 few-shot 示例，需要人工 curator 审核：
1. 查询是否加了必要的业务过滤条件（软删除、租户隔离、时间范围）
2. 聚合逻辑是否正确
3. 标注"这条查询体现了什么业务规则"

### 3.4 触发方式

- **定时轮询：** 每 10 分钟检查 `information_schema` 变更
- **手动触发：** DDL 变更后手动刷新（提供 API 和 Web UI）
- **知识新鲜度目标：** Schema 变更 < 10 分钟内可检索

---

## 四、摄入调度

使用 APScheduler + PG 队列，替代 Kafka——2-3 人团队无需维护消息队列，摄入任务量级不需要流处理。

| 数据源 | 触发方式 | 延迟目标 |
|--------|---------|---------|
| PRD 文档 | Webhook + 每日全量 | 增量 < 5min，全量兜底 |
| 代码仓库 | Git Webhook（push 事件）| < 5min |
| SQL Schema | 定时轮询 10min + 手动触发 | < 10min |

---

## 五、同义词映射表的冷启动

同义词映射表是查询标准化的基础数据，其来源横跨三种数据源：

```
数据来源                          提取的映射项
──────────────────────────────────────────────────
数据库 information_schema     →  表名 ←→ 表注释（"users ←→ 用户信息表"）
  - table_name                 →  列名 ←→ 列注释（"usr_auth_st ←→ 用户认证状态"）
  - table_comment / column_comment
  
PRD 文档标题/目录              →  模块名 ←→ 需求ID（"用户登录 ←→ REQ-2024-0187"）
  - 文档标题层级
  - 标签/分类
  
Git 仓库目录结构               →  目录名 ←→ 模块名（"src/auth/ ←→ 认证模块"）
  - 一级/二级目录名
  
人工补充                       →  常见口语/缩写（"挂了 ←→ 服务不可用"）
  - 从 Shadowing 观察中收集
  - 团队内部黑话
```

预计冷启动可自动生成 60-80 条映射，人工补充 20-30 条。总量 80-110 条，覆盖最常见的查询。

> 同义词映射表的持续维护机制（自动发现 + 人工审核 + 衰变检查）详见 [Supervisor Agent 设计 - 查询改写](SPMA-design-01-supervisor-agent.md#映射表维护人工种子--自动发现--人工审核闭环)。

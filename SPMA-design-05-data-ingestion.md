# Design: 数据摄入管道设计（离线/异步）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 模块职责：将三种异构数据源（PRD 文档、代码仓库、SQL 数据库）持续同步到 PGVector 向量数据库，保持知识新鲜度

---

## 数据摄入全景

```
PRD 文档 (Confluence/Wiki)
  → Docling/Unstructured 解析
  → 递归语义分块（按段落+标题自然边界切割，目标 ~500 tokens/块，50-token overlap；使用 tiktoken cl100k_base tokenizer）
  → BGE-M3 嵌入 → PGVector
  → 触发方式：Webhook（Confluence 页面更新事件）或定时全量同步（每日凌晨）

代码仓库 (Git)
  → TreeSitter AST 解析（保留函数/类边界，按语法单元分块）
  → 元数据提取（文件路径、函数签名、imports、调用图、commit message 中的需求 ID 如 [REQ-1234]）
  → 存储到 code_chunks 表（不生成 embedding）
  → 触发方式：Git Webhook（push 事件）→ 增量索引变更文件；存量代码全量索引

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

### 2.1 解析

使用 TreeSitter 做 AST 解析。支持的语言由 TreeSitter grammar 决定（Python、Java、Go、TypeScript、JavaScript 等）。

### 2.2 分块策略

**按语法单元分块——函数/类边界，不是 token 数：**

```
文档分块:  ┌──── 段落1 ────┐┌──── 段落2 ────┐┌──── 段落3 ────┐
代码分块:  ┌── def login() ──┐┌── class TokenService ──────────┐
                        按函数/类边界，长度不固定
```

TreeSitter 做**语法感知的边界识别**——"这个函数从第 42 行开始，第 87 行结束，包括它的 docstring、函数体、内部的所有逻辑"。每个函数/类作为一个独立的 embedding chunk。

### 2.3 元数据提取

每个代码 chunk 提取：
- `file_path`: 文件路径
- `function_name` / `class_name`: 函数名/类名
- `language`: 编程语言
- `imports`: import 列表
- `calls`: 被调用的函数列表（来自 AST 调用图分析）
- `called_by`: 调用者列表
- `req_ids`: 从 git log 中提取的关联需求 ID（正则匹配 `[REQ-XXXXX]`）
- `commit_hash` / `author`: 最后修改的 commit 和作者

### 2.4 数据库 Schema

```sql
CREATE TABLE code_chunks (
    id UUID PRIMARY KEY,
    content TEXT,                -- 函数/类的完整源代码（含注释和docstring）
    
    file_path TEXT,              -- src/auth/oauth.py
    function_name TEXT,          -- token_refresh
    class_name TEXT,             -- TokenService
    language TEXT,               -- python
    imports TEXT[],              -- ["from jose import jwt", "import redis"]
    calls TEXT[],                -- 被调用的函数
    called_by TEXT[],            -- 调用者
    
    req_ids TEXT[],              -- 关联的需求ID
    commit_hash TEXT,
    author TEXT,
    
    repo TEXT,
    branch TEXT,
    updated_at TIMESTAMP
);

-- B-tree 索引（精确搜索，不需要向量索引）
CREATE INDEX idx_code_file ON code_chunks (file_path);
CREATE INDEX idx_code_function ON code_chunks (function_name);
CREATE INDEX idx_code_class ON code_chunks (class_name);
CREATE INDEX idx_code_repo ON code_chunks (repo);
```

### 2.5 为什么存源代码而不是存摘要

| | 存源代码（本方案） | 存 LLM 生成的摘要 |
|---|---|---|
| **怎么生成** | 直接取函数体原文 | 每个函数调一次 LLM 生成描述 |
| **成本** | 零额外成本 | 10 万个函数 × 一次 LLM = 大量 token |
| **中文召回** | 不适用（Code Worker 不做 embedding） | 摘要是中文的，跟中文 query embedding 天然匹配 |
| **返回给用户** | 直接展示源代码 + 文件路径 | 展示摘要，用户看源码需要再跳一次 |
| **维护** | Git webhook → 增量更新变更文件 | 每次代码变更都需要重新生成摘要 |
| **幻觉风险** | 零 | LLM 可能错误理解函数行为 |

选择存源代码的原因：零成本、零幻觉。

**Phase 3 可选增强：** 如果中文→英文标识符映射表覆盖不够好，用 LLM 为高频检索的函数生成中文描述标签，存入 `code_chunks` 的元数据字段（如 `description_cn`），用于增强关键词匹配。不涉及 embedding。

### 2.6 触发方式

- **增量更新：** Git Webhook（push 事件）→ 只索引变更的文件（通过 git diff 确定）
- **全量索引：** 首次部署时全量索引所有仓库的存量代码

### 2.7 元数据索引的过期处理

代码一 commit，元数据索引就跟不上。但这对 Code Worker 的影响远小于 embedding 方案——因为 grep 可以直接搜实时文件系统，不依赖索引的新鲜度。

**处理策略：**

1. **元数据索引（code_chunks 表）** 用于快速定位（~10ms），是主路径
2. **实时文件系统 ripgrep** 用于验证和兜底——当元数据索引的 `commit_hash` 落后于 HEAD 时，自动触发实时 grep
3. 代码 chunk 存储 `commit_hash`——检索结果可以跟当前 HEAD 对比，标注"此 chunk 可能已过期"
4. Git Webhook 触发增量索引（变更文件在 push 后 5 分钟内更新元数据）

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

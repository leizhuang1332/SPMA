# Design: Code Worker 设计（代码检索）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责下发检索参数给本 Worker
> 模块职责：对代码仓库执行 AST 感知的结构化搜索，通过 grep 精确匹配 + AST 调用图扩展上下文，支持多仓库路由

---

## 模块在架构中的位置

```
Supervisor Agent
    │
    ├── Doc Worker
    ├── Code Worker  ← 本文档范围
    │   ├─ grep 关键词+结构搜索（主引擎，唯一引擎）
    │   ├─ AST 调用图扩展（上下文补全）
    │   └─ 多仓库路由（module → repo 映射）
    │
    └── SQL Worker
```

---

## 一、设计决策：为什么 Code Worker 只用 grep/AST，不做 embedding

### 1.1 研究证据

**GrepRAG（ISSTA 2026）** 对 Python 代码补全任务的对比实验：

| 方法 | 准确率 | 延迟 |
|------|--------|------|
| **GrepRAG（ripgrep）** | **42.29%** | **0.018s** |
| RLCoder（强化学习） | 39.46% | ~3.0s |
| VanillaRAG（BM25） | 24.99% | ~3.0s |
| GraphCoder（图结构） | 19.44% | 6.9s |

轻量级 grep 不仅比嵌入检索准确率高 17 个百分点，而且快 170 倍。

**Chroma 和 Google DeepMind 的研究发现：**

1. **语义相似 ≠ 功能正确。** 把 `<` 改成 `<=`，嵌入向量几乎不动，但程序行为完全变了。
2. **规模天花板。** 512 维嵌入在 ~50 万条文档时开始崩溃。BM25 关键词搜索反超神经嵌入。
3. **干扰项问题。** 语义搜索返回的"看起来相关但事实无关"的结果对 LLM 造成的幻觉比随机无关文本还严重。

### 1.2 Claude Code 的做法

Claude Code 完全不做代码索引和 embedding：

```
1. 用 Grep 搜相关的符号/字符串 → 确定文件范围
2. 用 Read 直接读文件（不是读取 embedding chunk，是完整文件）
3. 在上下文中理解代码结构（调用关系、继承链、数据流）
4. 根据需要再 Grep → 再 Read → 再理解
```

Boris Cherny（Claude Code 技术负责人）公开说过放弃本地向量数据库的原因——**嵌入索引的过期问题**。代码一 commit，索引就跟不上。agentic grep 永远搜索的是代码的当前状态。

### 1.3 我们场景的适配

Claude Code 的方案有两个前提：
- 200K token 上下文窗口（一次性装下大量代码）
- 单用户、单任务的交互模式（agent 可以慢慢搜）

这两个前提在我们的 RAG 系统里不完全成立：
- 我们的系统是**多用户共享的知识库**，预建元数据索引的价值在于"回答一次，后续所有人受益"
- 用户期望**秒级响应**，纯 agentic grep 在复杂查询上的延迟不可接受

**但我们接受的结论是：** grep 比 embedding 更准确这个事实不因为场景不同而改变。因此 Code Worker 的路线是：

> **预建 AST 元数据索引（函数名、类名、文件路径、调用图）+ 实时 grep 搜索，不做代码 embedding。**

### 1.4 与 Doc Worker 的路线差异

Doc Worker 用 embedding（BGE-M3），Code Worker 不用。这不是不一致——是两种数据源的本质差异决定的：

| | PRD 文档 | 代码 |
|---|---|---|
| **用户怎么搜** | "用户登录的需求是怎么定的？"——自然语言描述行为 | "oauth.py 的 token_refresh"——有精确的符号名 |
| **内容特征** | 中文长文本，词汇多样化 | 英文标识符，高度结构化 |
| **embedding 的价值** | 中文语义匹配，跨表述召回 | 语义相似≠功能正确，嵌入向量对代码变更不敏感 |
| **grep 的价值** | 低——用户很少记得文档里的精确措辞 | 高——函数名/类名/文件名是精确的检索锚点 |
| **过期风险** | 低——文档更新频率低 | 高——代码随时在 commit，embedding 跟不上 |
| **适合的检索方式** | embedding 为主 + BM25 补充 | **grep/AST 为主，不用 embedding** |

---

## 二、检索策略：grep 先行 + AST 调用图扩展

### 2.1 唯一检索路径

```
所有查询 → grep/AST 搜索
            ├─ 有精确实体（code_refs 非空）→ 精确匹配（~40%查询）
            │   └─ AST 调用图扩展上下文（调用链、被调用链）
            │
            ├─ 有语义锚点（module 非空，code_refs 为空）→ 关键词搜索（~50%查询）
            │   └─ 查询改写（中文术语→英文标识符）→ grep
            │   └─ AST 调用图扩展上下文
            │
            └─ 无锚点（bare）→ 全文关键词搜索 + 必要时反问用户（~10%查询）
```

不再有 embedding 兜底路径。grep 零结果时，策略是**更好的查询改写**而非**降级到不准确的语义搜索**。

### 2.2 检索函数

```python
def code_search(query: str, entities: ExtractedEntities, target_repos: list[str] | None) -> SearchResult:
    """
    Code Worker 的唯一定位策略：grep + AST 调用图扩展。
    不依赖代码 embedding。
    """
    # Step 1: 确定搜索范围
    repo_filter = target_repos or []  # 来自 Repo Router（见第三章）
    
    # Step 2: 提取搜索词
    search_terms = build_search_terms(query, entities)
    # code_refs 非空 → 精确符号名 + 文件名
    # code_refs 为空 → 从 query 中提取关键词 + 同义词映射
    
    # Step 3: grep 搜索（同时搜元数据索引和实时文件系统）
    grep_results = grep_codebase(
        terms=search_terms,
        repo_filter=repo_filter,
        file_patterns=infer_file_patterns(entities.module),
        search_targets=["function_name", "class_name", "file_path", "content"]
    )
    
    if grep_results:
        # Step 4: AST 调用图扩展——从 grep 命中出发，沿调用链扩展上下文
        expanded = expand_via_call_graph(
            seeds=grep_results,
            direction="both",  # callers + callees
            max_depth=2
        )
        return SearchResult(
            primary=grep_results,
            expanded_context=expanded,
            method="grep+ast"
        )
    
    # Step 5: grep 零结果 → 不降级到 embedding，而是反问或优化查询
    return SearchResult(
        primary=[],
        method="grep",
        note="grep 未匹配到关键词。建议用户提供文件名、函数名或需求ID以精确定位。",
        suggestions=generate_query_suggestions(query, entities)
    )
```

### 2.3 搜索词构造策略

当用户没有提供精确的 `code_refs` 时，如何从自然语言 query 构造有效的 grep 关键词是关键：

```python
def build_search_terms(query: str, entities: ExtractedEntities) -> list[str]:
    """
    从用户 query 构造 grep 搜索词。核心挑战：中文 query → 英文代码标识符。
    """
    terms = []
    
    # 优先级1: 用户明确给出的符号名（最可靠）
    if entities.code_refs:
        terms.extend(entities.code_refs)
        return terms  # 有精确引用，不需要额外构造
    
    # 优先级2: 同义词映射表（中文术语 → 可能的英文标识符）
    if entities.module:
        terms.extend(lookup_code_terms(entities.module))
        # 例: "用户登录" → ["login", "auth", "authenticate", "oauth", "sso", "session"]
    
    # 优先级3: 从 query 中提取英文词（用户可能中英混合输入）
    english_tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', query)
    terms.extend(english_tokens)
    
    # 优先级4: LLM 辅助翻译（条件触发，仅当 terms 仍为空时）
    # 让 LLM 把中文业务描述翻译为可能的代码标识符
    if not terms and len(query) > 10:
        terms = llm_translate_to_identifiers(query)
        # "用户登录的OAuth token刷新逻辑在哪"
        # → ["oauth", "token_refresh", "refresh_token", "login", "authenticate"]
    
    return terms[:10]  # 限制搜索词数量，避免噪声
```

### 2.4 同义词映射表：中文业务术语 → 英文代码标识符

这是 grep-only 策略的**核心基础设施**。不同于 Supervisor Agent 的通用同义词映射（"用户表"→"users"），Code Worker 需要的是**中文术语到英文代码标识符**的映射：

```python
# 业务域 → 可能的代码标识符
CODE_TERM_MAP = {
    # 用户认证域
    "登录":       ["login", "signin", "authenticate", "auth", "oauth", "sso"],
    "注册":       ["register", "signup", "create_user", "enroll"],
    "权限":       ["permission", "acl", "rbac", "authorize", "role", "access_control"],
    "会话":       ["session", "token", "jwt", "cookie"],
    "密码":       ["password", "passwd", "credential", "secret"],
    "验证码":     ["captcha", "verification_code", "otp", "sms_code"],
    
    # 订单域
    "下单":       ["create_order", "place_order", "checkout", "purchase"],
    "退款":       ["refund", "reverse", "chargeback", "return"],
    "购物车":     ["cart", "basket", "shopping_cart", "line_item"],
    "优惠券":     ["coupon", "voucher", "promo_code", "discount"],
    
    # 支付域
    "支付":       ["payment", "pay", "charge", "transaction", "billing"],
    "对账":       ["reconciliation", "settlement", "clearing", "balance"],
    "分账":       ["split", "commission", "profit_sharing", "fee"],
    
    # 通知域
    "推送":       ["push", "notify", "notification", "fcm", "apns", "firebase"],
    "短信":       ["sms", "message", "text_message", "send_sms"],
    "邮件":       ["email", "mail", "send_mail", "smtp"],
    
    # 通用技术术语
    "超时":       ["timeout", "ttl", "expire", "expiry", "deadline", "ttl"],
    "重试":       ["retry", "backoff", "circuit_breaker", "resilience"],
    "缓存":       ["cache", "redis", "memcache", "cached", "invalidate"],
    "队列":       ["queue", "kafka", "rabbitmq", "pulsar", "message", "event"],
    "定时任务":   ["cron", "scheduler", "job", "task", "scheduled", "interval"],
    "限流":       ["rate_limit", "throttle", "quota", "limiter"],
    "熔断":       ["circuit_breaker", "fallback", "degradation", "hystrix"],
    "幂等":       ["idempotent", "dedup", "duplicate", "exactly_once"],
    "分布式锁":   ["lock", "distributed_lock", "mutex", "redis_lock"],
}
```

**维护方式：** 与 Supervisor Agent 的同义词映射表共享同一个维护管道——人工种子（冷启动）→ 自动发现（用户交互日志）→ 人工审核闭环（见 [Supervisor Agent 设计 - 映射表维护](SPMA-design-01-supervisor-agent.md#映射表维护人工种子--自动发现--人工审核闭环)）。

### 2.5 AST 调用图扩展

grep 找到目标函数后，沿 AST 调用图扩展上下文——这是"不用 embedding 但仍能补全相关代码"的关键机制：

```python
def expand_via_call_graph(
    seeds: list[CodeChunk],
    direction: str,  # "upstream" | "downstream" | "both"
    max_depth: int = 2
) -> list[CodeChunk]:
    """
    从 grep 命中的种子函数出发，沿调用图扩展。
    
    seeds = grep 找到的 token_refresh()
    direction="upstream"  → 谁调用了 token_refresh()？
    direction="downstream" → token_refresh() 调用了谁？
    direction="both"      → 双向扩展
    max_depth=2           → 最多扩展 2 层调用链
    """
    expanded = set()
    frontier = list(seeds)
    
    for depth in range(max_depth):
        next_frontier = []
        for chunk in frontier:
            if direction in ("upstream", "both"):
                for caller in chunk.called_by:
                    if caller not in expanded:
                        expanded.add(caller)
                        next_frontier.append(lookup_chunk(caller))
            if direction in ("downstream", "both"):
                for callee in chunk.calls:
                    if callee not in expanded:
                        expanded.add(callee)
                        next_frontier.append(lookup_chunk(callee))
        frontier = next_frontier
    
    return list(expanded)
```

**典型场景：**

```
用户: "token_refresh 这个函数的逻辑是什么？"
grep: 精确定位到 auth-service/src/auth/oauth.py::token_refresh()
AST扩展:
  upstream(谁调用了它):   login_oauth() → validate_session() → api/middleware.py::auth_middleware()
  downstream(它调用了谁): jwt.decode() → redis.get() → rotate_credentials()
  
返回: token_refresh 本体 + 2 层调用链上下文
用户看到的不仅是 token_refresh 的代码，还有它在整个认证链路中的位置
```

---

## 三、多仓库路由

### 3.1 问题

公司后端微服务有几百个源码仓库，每个承载不同业务。用户问"用户登录的 OAuth 逻辑在哪"时，不可能让用户手动选择仓库。需要在检索前自动确定目标仓库范围。

### 3.2 方案：Module → Repo 映射

这是 Supervisor Agent 的 `module` 实体和 Code Worker 之间的桥梁：

```
Supervisor Agent
  └─ 实体抽取: module="用户登录"
         │
         ▼
Repo Router（本文档定义）
  ├─ 静态映射: "用户登录" → ["auth-service", "auth-gateway", "sso-service"]
  └─ 下发给 Code Worker: repo_filter=["auth-service", "auth-gateway", "sso-service"]
         │
         ▼
Code Worker
  └─ grep ... WHERE repo IN ('auth-service', 'auth-gateway', 'sso-service')
```

### 3.3 静态映射表

```python
MODULE_REPO_MAP = {
    # 用户认证
    "用户登录":       ["auth-service", "auth-gateway", "sso-service"],
    "权限管理":       ["auth-service", "rbac-service"],
    "用户管理":       ["user-service", "account-service"],
    
    # 交易链路
    "订单":           ["order-service", "order-query-service"],
    "支付":           ["payment-service", "billing-service", "payment-gateway"],
    "退款":           ["refund-service", "payment-service"],
    "购物车":         ["cart-service"],
    
    # 商品
    "商品":           ["product-service", "inventory-service", "catalog-service"],
    "库存":           ["inventory-service", "warehouse-service"],
    
    # 工作流
    "审批":           ["workflow-engine", "approval-service"],
    "工单":           ["ticket-service", "workflow-engine"],
    
    # 通知
    "通知":           ["notification-service", "message-center"],
    "推送":           ["push-service", "notification-service"],
    "短信":           ["sms-service", "notification-service"],
    "邮件":           ["email-service"],
    
    # 基础设施
    "网关":           ["api-gateway", "gateway-service"],
    "配置":           ["config-center", "nacos-service"],
    "日志":           ["log-service", "log-collector"],
    "监控":           ["monitor-service", "metrics-service", "alert-service"],
    "定时任务":       ["scheduler-service", "job-service"],
    "文件":           ["file-service", "oss-service"],
    "搜索":           ["search-service", "elasticsearch-service"],
}
```

### 3.4 路由逻辑

```python
def resolve_repos(
    module: str | None,
    code_refs: list[str] | None,
    user_query: str
) -> list[str] | None:
    """
    确定应该检索哪些仓库。三层递进。
    返回 None 表示不限仓库（全量搜索）。
    """
    # 第一层：静态映射（~1ms）
    if module and module in MODULE_REPO_MAP:
        return MODULE_REPO_MAP[module]
    
    # 第二层：从 code_refs 推断
    # 如果用户给了具体文件路径如 "src/auth/oauth.py"，
    # 可以用 repo 的文件索引反向查找
    if code_refs:
        repos = reverse_lookup_repos_by_file_patterns(code_refs)
        if repos:
            return repos
    
    # 第三层：repo 注册表的语义匹配（~50ms）
    # repo_registry 表存了每个仓库的描述和模块标签
    if module:
        repos = search_repo_registry(module=module, top_k=3)
        if repos and repos[0].match_score > 0.7:
            return [r.repo_name for r in repos]
    
    # 无锚点 → 不限仓库，全量 grep
    # 注意：全量 grep 在 B-tree 索引上是 O(log n)，几百个仓库不影响性能
    return None
```

### 3.5 仓库注册表

```sql
-- 仓库注册表：记录每个仓库的业务归属和描述
CREATE TABLE repo_registry (
    repo_name TEXT PRIMARY KEY,
    display_name TEXT,            -- 中文名称："用户认证服务"
    description TEXT,             -- 功能描述
    modules TEXT[],               -- 归属的业务模块：["用户登录", "权限管理"]
    tech_stack TEXT[],            -- 技术栈：["Python", "FastAPI", "PostgreSQL"]
    owner_team TEXT,              -- 所属团队
    repo_url TEXT,                -- Git 地址
    updated_at TIMESTAMP
);
```

数据来源：
- **自动提取：** 从代码仓库的 README、目录结构、build.gradle/pyproject.toml 等项目文件中提取
- **人工补充：** 团队在系统上线时一次性维护，之后随新仓库创建而更新

### 3.6 静态映射表的维护

与同义词映射表同理：
- **冷启动（上线前）：** 从 `repo_registry` 表的 `modules` 字段反向生成，预计 30-50 条
- **自动发现（上线后）：** 分析检索成功的案例——用户问"XX"时最终命中了哪些仓库 → 加入映射
- **人工审核：** 新的仓库上线时，负责人补充模块标签

---

## 四、AST 元数据存储设计

Code Worker 不做 embedding，但仍然需要**元数据索引**来支持快速的结构化搜索。存储的是代码的**结构化元数据**（函数名、文件路径、调用关系等），不是向量。

### 4.1 分块单元：函数/类

文档分块按段落边界，代码分块按函数/类边界。TreeSitter 的作用是**语法感知的边界识别**——"这个函数从第 42 行开始，第 87 行结束，包括它的 docstring、函数体、内部的所有逻辑"。

```
文档分块:  ┌──── 段落1 ────┐┌──── 段落2 ────┐┌──── 段落3 ────┐
代码分块:  ┌── def login() ──┐┌── class TokenService ──────────┐
                        按函数/类边界，长度不固定
```

### 4.2 数据库 Schema

```sql
CREATE TABLE code_chunks (
    id UUID PRIMARY KEY,
    
    -- 源代码（完整函数/类，含注释和 docstring）
    content TEXT,
    
    -- 结构化元数据（用于精确检索，B-tree 索引）
    file_path TEXT,              -- src/auth/oauth.py
    function_name TEXT,          -- token_refresh
    class_name TEXT,             -- TokenService
    language TEXT,               -- python
    line_start INTEGER,          -- 42（函数起始行）
    line_end INTEGER,            -- 87（函数结束行）
    
    -- AST 调用图
    imports TEXT[],              -- ["from jose import jwt", "import redis"]
    calls TEXT[],                -- 被调用的函数: ["jwt.decode", "redis.get"]
    called_by TEXT[],            -- 调用者: ["login_oauth", "validate_session"]
    
    -- 跨源关联
    req_ids TEXT[],              -- 在 git log 中关联的需求 ID: ["REQ-187"]
    commit_hash TEXT,            -- 最后修改的 commit
    author TEXT,                 -- 最后修改人
    
    -- 多仓库路由
    repo TEXT,                   -- 所属仓库
    branch TEXT,                 -- 分支
    updated_at TIMESTAMP
);

-- B-tree 索引（精确搜索用——不需要向量索引）
CREATE INDEX idx_code_file ON code_chunks (file_path);
CREATE INDEX idx_code_function ON code_chunks (function_name);
CREATE INDEX idx_code_class ON code_chunks (class_name);
CREATE INDEX idx_code_repo ON code_chunks (repo);
CREATE INDEX idx_code_req_ids ON code_chunks USING GIN (req_ids);

-- PostgreSQL 全文搜索索引（用于 grep 零结果时的内容搜索兜底）
CREATE INDEX idx_code_content_fts ON code_chunks 
    USING GIN (to_tsvector('english', content));
```

### 4.3 为什么存源代码而不是存摘要

| | 存源代码（本方案） | 存 LLM 生成的摘要 |
|---|---|---|
| **怎么生成** | 直接取函数体原文 | 每个函数调一次 LLM 生成描述 |
| **成本** | 零额外成本 | 10 万个函数 × 一次 LLM = 大量 token |
| **返回给用户** | 直接展示源代码 + 文件路径 | 展示摘要，用户看源码需要再跳一次 |
| **维护** | Git webhook → 增量更新变更文件 | 每次代码变更都需要重新生成摘要 |
| **幻觉风险** | 零 | LLM 可能错误理解函数行为，摘要和实际代码不一致 |

选择存源代码：零成本、零幻觉。

### 4.4 关于调用图（calls / called_by）

`calls` 和 `called_by` 来自 AST 分析 build 出来的调用图。这两个字段不参与关键词检索，但用于**上下文扩展**——用户搜到 `token_refresh` 后，顺藤摸瓜找到它的调用链，回答"谁调用了它"和"它影响了谁"。见 [2.5 节](#25-ast-调用图扩展)。

### 4.5 实时 grep vs 元数据索引

Code Worker 同时使用两种 grep 路径：

| 路径 | 搜什么 | 延迟 | 适用场景 |
|------|--------|------|---------|
| **元数据索引**（`code_chunks` 表） | function_name, class_name, file_path, content | ~10ms（B-tree） | 已知符号名、模糊关键词 |
| **实时文件系统**（`ripgrep`） | 仓库工作目录中的最新代码 | ~50-100ms | 最新 commit 尚未索引、全文搜索 |

**两者的关系：** 元数据索引覆盖 95% 的查询（有 B-tree 索引，毫秒级）。实时 ripgrep 作为兜底——当索引可能过期时（`updated_at < HEAD`），用实时 grep 验证结果的准确性，并标注"此结果来自实时搜索，索引可能已过时"。

---

## 五、实体驱动的检索分发

```python
def route_code_search(entities: ExtractedEntities, user_query: str) -> SearchStrategy:
    """
    按实体类型决定搜索策略。
    不再有 embedding 路径——只有 grep 的两种模式。
    """
    has_exact_refs = bool(entities.code_refs)
    
    if has_exact_refs:
        # 路径 A: 精确匹配
        # 文件名精确匹配 + 函数名/类名 grep → 毫秒级定位
        # AST 调用图扩展补充上下文
        return SearchStrategy(
            mode="EXACT",
            search_terms=entities.code_refs,
            expand_context=True  # 扩展调用链
        )
    elif entities.module:
        # 路径 B: 模块锚定的关键词搜索
        # 从 module 构造英文标识符关键词 → grep
        # 限定目标仓库（Repo Router）
        return SearchStrategy(
            mode="MODULE_GREP",
            search_terms=build_search_terms(user_query, entities),
            repo_filter=resolve_repos(entities.module, entities.code_refs, user_query)
        )
    else:
        # 路径 C: 全量关键词搜索
        # 从 query 中提取所有可能的搜索词 → 全仓库 grep
        # grep 零结果时 → 反问用户，不降级到不可靠的语义搜索
        return SearchStrategy(
            mode="FULL_GREP",
            search_terms=build_search_terms(user_query, entities),
            on_zero_results="ask_clarification"  # 不再有 embedding 兜底
        )
```

### 实体用法详表

| 实体 | 用法 | 优先级 | 示例 |
|------|------|-------|------|
| `code_refs` | 精确搜索——文件名精确匹配 + 函数名/类名 grep | **最高** | 搜 `oauth.py` 文件 + grep `TokenService` 定义 |
| `req_ids` | 关联搜索——grep commit log 和代码注释中引用该需求 ID 的代码 | 高 | `git log --grep="REQ-2024-0187"` 找变更文件 |
| `module` | 仓库路由 + 关键词构造——限定仓库范围 + 映射中文术语→英文标识符 | 高 | "用户登录"→`["auth-service"]` + grep `["login","auth","oauth"]` |
| `person` | 作者过滤——`git log --author="张三"` 限定变更范围 | 中 | 结合 `time_range` 精确定位 |
| `time_range` | 时间过滤——限制 git log 的 `--since` / `--until` | 中 | `git log --since="2026-05-29"` |
| `version` | 分支/tag 过滤 | 中 | `git log release/2026Q1` |

---

## 六、对中文查询的处理

### 6.1 核心思路：不依赖跨语言嵌入映射

BGE-M3 的中→英跨语言嵌入在代码检索场景下未经验证。Code Worker 的选择是**不依赖它**。中文查询的处理走三条确定性的路：

```
中文查询: "用户登录的OAuth token刷新逻辑"
         │
         ├─ 路径1: 同义词映射表（~1ms）
         │   "用户登录" → ["login", "auth", "authenticate", "oauth"]
         │   "刷新"    → ["refresh", "renew", "rotate"]
         │   → grep "oauth OR refresh OR login" 
         │
         ├─ 路径2: LLM 翻译（~300ms，条件触发）
         │   当映射表覆盖不到时，让 LLM 把中文翻译为可能的代码标识符
         │   → grep "token_refresh OR refresh_token OR oauth_callback"
         │
         └─ 路径3: req_ids 桥接（不需要翻译）
             如果 module="用户登录" 映射到了 req_ids=["REQ-187"]
             → git log --grep="REQ-187" → 直接找变更文件
```

### 6.2 LLM 辅助翻译的触发条件

```python
def llm_translate_to_identifiers(chinese_query: str) -> list[str]:
    """
    仅在以下情况触发：
    1. 同义词映射表未覆盖
    2. query 长度 > 10 字（有足够语义信息）
    3. 不含任何英文词（说明用户纯中文输入）
    
    不会对每个查询都调 LLM——只在需要时触发。
    """
    prompt = f"""将以下中文业务描述翻译为可能的代码标识符（函数名、变量名）。
只输出英文标识符列表，用逗号分隔，最多 8 个。

中文: "{chinese_query}"

英文标识符:"""
    
    # 调用轻量模型（Haiku），~300ms
    # 结果缓存 24h（相同 query 复用）
    response = llm.invoke(prompt)
    return [t.strip() for t in response.split(",") if t.strip()]
```

### 6.3 不受中文查询影响的检索路径

以下路径对中英文不敏感——不依赖任何跨语言映射：

| 路径 | 原理 | 中文查询示例 |
|------|------|------------|
| `code_refs` 精确匹配 | 用户给了文件名/函数名，直接 grep | "oauth.py 的 token_refresh 函数" → grep `token_refresh` |
| `req_ids` 关联搜索 | git log 中搜需求 ID，不需要翻译 | "REQ-187 改了哪些代码" → git log --grep="REQ-187" |
| `person` + `time_range` | git log 过滤，不需要翻译 | "张三上周改了什么" → git log --author="张三" --since |
| 中英混合输入 | 用户输入中带英文标识符，直接提取 | "oauth 的刷新逻辑在哪" → 提取 "oauth" → grep |

这四类路径覆盖了估计 60%+ 的中文查询场景，不需要任何翻译。

---

## 七、反事实分析：去掉实体对 Code Worker 的影响

> **量化估计：** 去掉实体后，Code Worker 的 Recall@10 下降约 20-30 个百分点。损失最大的是"知道文件名/函数名"这类精确查询。

代码检索有一个特殊问题：用户要的不是"语义上相似的代码"，而是"那一份确切的代码"。"oauth.py 里的 TokenService"——用户知道文件在哪，只是不想手动翻。有 `code_refs`，直接 `grep`；没有，就只能靠关键词猜测。

去掉实体后，Code Worker 退化为纯关键词搜索，失去的能力：

| 失去的实体 | 失去的检索能力 | 影响 |
|-----------|--------------|------|
| `code_refs` | 精确文件/函数名匹配 | 最重要——这是 Code Worker 最高精度路径 |
| `module` | 仓库路由 + 中文→英文标识符映射 | 无法确定搜哪个仓库，全量搜索噪声大 |
| `req_ids` | git log 关联搜索 | 跨源溯源（需求→代码）链路断裂 |
| `person` + `time_range` | 作者/时间过滤 | 无法缩小历史变更范围 |

---

## 八、数据摄入（Code Worker 视角）

```
代码仓库 (Git)
  → TreeSitter AST 解析（保留函数/类边界，按语法单元分块）
  → 元数据提取（文件路径、函数签名、imports、调用图、commit message 中的需求 ID 如 [REQ-1234]）
  → 存储到 code_chunks 表（不生成 embedding）
  → 触发方式：Git Webhook（push 事件）→ 增量更新变更文件的元数据；存量代码全量导入
```

与之前设计的关键差异：**去掉了 BGE-M3 嵌入环节**。代码 chunk 不再生成 embedding，只存储结构化元数据和源代码原文。

> 完整的数据摄入管道设计见 [数据摄入管道设计](SPMA-design-05-data-ingestion.md)。注意：该文档中与代码 embedding 相关的内容需同步更新。

# Design: Code Worker 设计（代码检索）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责下发检索参数给本 Worker
> 模块职责：对代码仓库执行 AST 感知的结构化搜索，通过 grep 精确匹配 + AST 调用图扩展上下文，支持多仓库路由

---

## 模块在架构中的位置

```
Supervisor Agent（实体抽取 + 意图分类）
    │
    ├── Doc Worker
    ├── Code Worker  ← 本文档范围
    │   ├─ 搜索词构造管线（用户问题 → 代码标识符）
    │   ├─ Phase 1: 全局符号索引扫描（跨仓库 Top-K 候选）
    │   ├─ Phase 2: 候选仓库深度搜索（grep + AST）
    │   │   ├─ 精确符号匹配（B-tree 索引）
    │   │   ├─ 实时 ripgrep 兜底（最新未索引代码）
    │   │   └─ AST 调用图扩展（上下文补全）
    │   └─ 跨仓库结果去重排序
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

### 2.1 两阶段检索路径

Code Worker 采用**搜后聚合（search-then-aggregate）**策略——先全局扫描确定候选仓库，再在候选仓库内深度搜索：

```
所有查询 → 搜索词构造管线（见第六章）
              │
              ▼
          Phase 1: 全局符号索引扫描（~10ms）
              │  跨所有仓库搜符号名（B-tree 索引）
              │  → Top-K 候选仓库（K=3~5）
              │
              ├─ 命中 1+ 仓库：
              │     ▼
              │   Phase 2: 候选仓库深度搜索（~30ms）
              │     ├─ 精确符号匹配（B-tree on code_chunks）
              │     ├─ 实时 ripgrep 兜底（最新未索引代码）
              │     └─ AST 调用图扩展上下文
              │     → 跨仓库去重排序 → 返回结果
              │
              └─ 零命中：
                    → 不降级到语义搜索
                    → 反问用户提供更精确信息
                    → 或走 git log 路径（req_ids/person/time_range）
```

与 Claude Code 的"逐仓库 grep"不同，本方案用**全局符号索引**一次扫描所有仓库，只对 Top-K 候选仓库做深度搜索。详细设计见第三章。

### 2.2 检索函数（两阶段）

```python
async def code_search(
    query: str,
    entities: ExtractedEntities,
    search_terms: SearchTermSet | None = None
) -> SearchResult:
    """两阶段检索：全局符号扫描 → 候选仓库深度搜索。"""
    # Step 0: 构造搜索词（如果未预构造）
    if search_terms is None:
        search_terms = await build_search_terms(query, entities)  # 见第六章
    
    if not search_terms.has_any:
        # 完全没有提取到搜索词 → 无法做符号搜索
        # 尝试 git log 路径（req_ids / person / time_range）
        return await git_log_search(entities)
    
    # ========== Phase 1: 全局符号索引扫描 ==========
    # 在所有仓库的符号表中搜索，按仓库聚合排序
    candidate_repos = await global_symbol_scan(
        terms=search_terms,
        top_k=5,
        min_match_per_repo=2  # 至少匹配 2 个符号才纳入候选
    )
    
    if not candidate_repos:
        # 符号索引没命中 → 反问用户，不降级到语义搜索
        return SearchResult(
            primary=[],
            method="grep+symbol_index",
            note="全局符号索引未匹配到关键词。建议提供文件名、函数名或需求ID。",
            suggestions=generate_query_suggestions(query, entities)
        )
    
    # ========== Phase 2: 候选仓库深度搜索 ==========
    all_results = []
    for repo in candidate_repos:
        repo_results = await deep_search_repo(
            repo_name=repo.name,
            search_terms=search_terms,
            seeds=repo.matched_symbols,  # Phase 1 已知的种子符号
            expand_context=True,          # AST 调用图扩展
            max_depth=2
        )
        all_results.extend(repo_results)
    
    # 跨仓库去重排序
    ranked = rank_and_deduplicate(all_results, query, search_terms)
    
    return SearchResult(
        primary=ranked[:20],
        candidate_repos=candidate_repos,
        method="symbol_index+grep+ast"
    )
```

**Phase 2 深度搜索**——在单个候选仓库内的搜索逻辑：

```python
async def deep_search_repo(
    repo_name: str,
    search_terms: SearchTermSet,
    seeds: list[str],  # Phase 1 在该仓库匹配到的符号
    expand_context: bool = True,
    max_depth: int = 2
) -> list[CodeChunk]:
    """
    在单个仓库内执行深度搜索。
    同时走两条路：元数据索引（code_chunks 表）+ 实时文件系统（ripgrep）。
    """
    # 路径 1: 元数据索引搜索（B-tree，~5ms）
    # 用 Phase 1 的种子符号做精确匹配，获取完整源代码
    index_results = await search_code_chunks(
        repo=repo_name,
        function_names=[s for s in seeds if is_symbol(s)],
        file_paths=[s for s in seeds if is_filepath(s)],
        content_keywords=search_terms.fuzzy_terms[:5]
    )
    
    # 路径 2: 实时 ripgrep（兜底，~50ms）
    # 当索引可能过期时（updated_at < repo HEAD），验证最新代码
    live_results = []
    if should_live_grep(repo_name):
        live_results = await ripgrep_repo(
            repo=repo_name,
            patterns=search_terms.all_terms()[:10],
            file_patterns=infer_file_patterns(seeds)
        )
    
    # 合并 + AST 扩展
    merged = merge_index_and_live(index_results, live_results)
    
    if expand_context and merged:
        merged = expand_via_call_graph(
            seeds=merged,
            direction="both",
            max_depth=max_depth
        )
    
    return merged
```

### 2.3 搜索词构造策略（入口）

从用户自然语言 query 构造有效的 grep 关键词，是整个检索链条的**第一公里**。核心挑战：中文业务描述 → 英文代码标识符。

Code Worker 不依赖跨语言嵌入映射（BGE-M3 中→英在代码检索场景未经验证），而是走三条确定性的路：

1. **同义词映射表**（~1ms）：预建的 中文术语 → 英文代码标识符 映射，覆盖 60-70% 常见查询
2. **形态扩展**（~1ms）：已知符号名的命名约定变换（snake_case ↔ camelCase ↔ 词序变换）
3. **LLM 翻译**（~300ms，缓存命中 ~5ms）：仅当映射表未覆盖时触发，结果缓存 24h

> **完整设计见 [第六章：搜索词构造管线](#六搜索词构造管线从用户问题到代码标识符)。** 本节只保留与检索流程直接相关的内容。

### 2.4 同义词映射表数据

中文业务术语 → 英文代码标识符的底表数据。搜索词构造管线（第六章）在此基础上叠加权重和上下文：

```python
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

> 这是底表数据。带权重和上下文的增强版结构见 [6.4 节](#64-type-c纯中文三层递进翻译)。维护方式见 [Supervisor Agent 设计 - 映射表维护](SPMA-design-01-supervisor-agent.md#映射表维护人工种子--自动发现--人工审核闭环)。

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

## 三、多仓库路由：全局符号索引 + 两阶段检索

### 3.1 问题与设计决策

公司后端微服务有几百个源码仓库，每个承载不同业务。用户问"用户登录的 OAuth 逻辑在哪"时，面临两难：

| 方案 | 问题 |
|------|------|
| **全量 grep**（每个仓库搜一遍） | 500 仓库 × 50ms/仓库 = 不可接受的延迟；结果排序困难 |
| **搜前路由**（先确定仓库再搜） | "用户登录"可能涉及 50+ 仓库（auth、gateway、session、captcha…），限定范围失去意义 |

**设计决策：搜后聚合（search-then-aggregate）。**

核心思路：不是先路由再搜索，而是**全局搜 → 按仓库聚合 → Top-K 深度搜索**。类比 Google——不是先让你选网站再搜，而是全局索引搜索，结果中标明来源。

### 3.2 全局符号索引

这是整个多仓库检索的核心基础设施——一张跨所有仓库的符号级倒排索引表：

```sql
CREATE TABLE global_symbol_index (
    id BIGSERIAL PRIMARY KEY,
    
    -- 符号名（函数名/类名/常量名/API 端点）
    symbol_name TEXT NOT NULL,          -- token_refresh, OAuthService, REFRESH_TOKEN_TTL
    
    -- 符号类型
    symbol_type TEXT NOT NULL,          -- function | class | constant | api_endpoint | method
    
    -- 位置信息
    repo_name TEXT NOT NULL,            -- auth-service
    file_path TEXT NOT NULL,            -- src/auth/oauth.py
    line_number INTEGER NOT NULL,       -- 42
    
    -- 业务标签（从 repo_registry 继承，用于辅助匹配）
    business_tags TEXT[],               -- ["认证", "OAuth", "Token"]
    
    -- 轻量去重签名
    signature_hash TEXT,                -- 函数签名的 MD5，跨仓库去重用
    
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 核心索引
CREATE INDEX idx_global_symbol_name ON global_symbol_index (symbol_name);
CREATE INDEX idx_global_symbol_repo ON global_symbol_index (repo_name);
CREATE INDEX idx_global_symbol_tags ON global_symbol_index USING GIN (business_tags);

-- pg_trgm 扩展：支持 LIKE '%token%' 不走全表扫描
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE INDEX idx_global_symbol_trgm ON global_symbol_index 
    USING GIN (symbol_name gin_trgm_ops);

-- 部分索引：排除高频噪声符号，缩小索引体积并加速扫描
CREATE INDEX idx_global_symbol_filtered ON global_symbol_index (symbol_name)
    WHERE symbol_name NOT IN (
        'init', 'main', 'run', 'get', 'set', 'handle', 
        'process', 'execute', 'start', 'stop', 'update',
        'create', 'delete', 'list', 'find', 'query'
    );
```

**规模估算：**
- 500 仓库 × 平均 3000 符号/仓库 = 150 万行
- 每行 ~200 字节 = ~300MB 数据 + ~150MB 索引
- PostgreSQL `shared_buffers` 设为 1GB，**全表常驻内存，零磁盘 IO**
- B-tree 深度 3-4 层，单次等值查找 < 0.1ms

### 3.3 两阶段检索流程

```
Phase 1: 全局符号索引扫描（目标 < 20ms）
─────────────────────────────────────────
搜索词 → B-tree 索引扫描（1.5M 行）
       → Bitmap OR 合并（多个搜索词）
       → GROUP BY repo_name（按仓库聚合命中数）
       → Top-K 排序（K=3~5）
       → 返回: [
            {repo: "auth-service", score: 0.95, matched: ["token_refresh", "oauth_login"]},
            {repo: "auth-gateway", score: 0.72, matched: ["oauth_proxy"]},
            {repo: "sso-service", score: 0.58, matched: ["sso_token"]},
          ]

Phase 2: 候选仓库深度搜索（目标 < 30ms）
─────────────────────────────────────────
每个候选仓库:
  ├─ 元数据索引搜索（code_chunks 表，~5ms）
  │   用 Phase 1 的种子符号做精确匹配，获取完整源代码
  │
  ├─ 实时 ripgrep 兜底（~50ms，仅当索引可能过期时触发）
  │   验证最新 commit 的代码是否已索引
  │
  ├─ AST 调用图扩展（~10ms）
  │   从种子符号出发，沿调用链扩展上下文
  │
  └─ 跨仓库去重排序（~5ms）
      基于签名 hash 去重 + 相关性排序
```

### 3.4 Phase 1 实现：全局符号扫描

```python
async def global_symbol_scan(
    search_terms: SearchTermSet,
    top_k: int = 5,
    min_match_per_repo: int = 2
) -> list[CandidateRepo]:
    """
    在所有仓库的符号索引中快速扫描。
    延迟目标: < 20ms（单次 SQL + 简单聚合）。
    """
    # 如果有精确匹配项，优先走 B-tree（最快路径）
    exact_terms = search_terms.exact_terms  # ["token_refresh", "oauth_login"]
    fuzzy_terms = search_terms.fuzzy_terms  # ["refresh", "oauth"]
    tag_terms = search_terms.tag_terms      # ["认证", "Token"]
    
    query = """
    WITH matched AS (
        -- 精确匹配：最高权重
        SELECT repo_name, symbol_name, symbol_type,
               1.0 AS match_score,
               'exact' AS match_type
        FROM global_symbol_index
        WHERE symbol_name = ANY($1)
        
        UNION ALL
        
        -- 模糊匹配：中等权重
        SELECT repo_name, symbol_name, symbol_type,
               0.7 AS match_score,
               'fuzzy' AS match_type
        FROM global_symbol_index
        WHERE symbol_name ILIKE ANY($2)
        
        UNION ALL
        
        -- 标签匹配：辅助权重
        SELECT repo_name, symbol_name, symbol_type,
               0.5 AS match_score,
               'tag' AS match_type
        FROM global_symbol_index
        WHERE business_tags && $3
    )
    SELECT 
        repo_name,
        AVG(match_score) AS avg_score,
        COUNT(*) AS match_count,
        ARRAY_AGG(DISTINCT symbol_name ORDER BY match_score DESC) AS matched_symbols,
        ARRAY_AGG(DISTINCT match_type) AS match_types
    FROM matched
    GROUP BY repo_name
    HAVING COUNT(*) >= $4
    ORDER BY avg_score DESC, match_count DESC
    LIMIT $5
    """
    
    like_patterns = [f"%{t}%" for t in fuzzy_terms]
    
    rows = await db.fetch(
        query,
        exact_terms,           -- $1: 精确匹配
        like_patterns,         -- $2: 模糊匹配
        tag_terms,             -- $3: 标签匹配
        min_match_per_repo,    -- $4: 最少匹配数
        top_k                  -- $5: 返回 Top-K
    )
    
    return [CandidateRepo(
        name=row['repo_name'],
        score=row['avg_score'],
        match_count=row['match_count'],
        matched_symbols=row['matched_symbols'],
        match_types=row['match_types']
    ) for row in rows]
```

**性能关键点：**
- `symbol_name = ANY($1)` 走 B-tree 索引扫描（Bitmap Index Scan），1.5M 行中只读取匹配的几十行
- `ILIKE ANY` 走 pg_trgm GIN 索引，支持模糊匹配而不走全表扫描
- `UNION ALL` 而非 `OR`：PostgreSQL 对 UNION ALL 的 Bitmap OR 合并比 WHERE 中的 OR 更高效
- 排除高频噪声符号的部分索引：`get`/`handle`/`process` 这类出现在 95% 仓库中的符号几乎无区分度，排除它们让结果集从数千行缩到几十行

### 3.5 代码域 IDF：自动过滤低区分度符号

```python
class CodeSymbolIDF:
    """
    代码符号的逆向仓库频率（Inverse Repo Frequency）。
    类比搜索引擎的 IDF，但计算粒度是"符号在几个仓库中出现"而非"词在几个文档中出现"。
    """
    
    def __init__(self):
        self.idf: dict[str, float] = {}
        self.noise_symbols: set[str] = set()
    
    async def refresh(self):
        """每小时从 global_symbol_index 重算 IDF（定时任务）"""
        rows = await db.fetch("""
            SELECT 
                symbol_name,
                COUNT(DISTINCT repo_name) AS repo_count
            FROM global_symbol_index
            GROUP BY symbol_name
        """)
        
        total_repos = await self.get_total_repo_count()  # 500
        
        for row in rows:
            if total_repos > 0 and row['repo_count'] > 0:
                idf = math.log(total_repos / row['repo_count'])
                self.idf[row['symbol_name']] = idf
                
                # 出现在 >30% 仓库中的符号 → 标记为噪声
                if row['repo_count'] > total_repos * 0.3:
                    self.noise_symbols.add(row['symbol_name'])
    
    def is_high_discrimination(self, symbol: str) -> bool:
        """该符号是否足够稀有，能有效缩小搜索范围？"""
        return self.idf.get(symbol, 1.0) > 1.2  # 出现在 <30% 仓库
    
    def apply_filter(self, terms: list[str]) -> list[str]:
        """过滤掉低区分度的搜索词"""
        return [t for t in terms if t not in self.noise_symbols]
```

**使用方式：** 搜索词构造完成后，过一遍 IDF 过滤器再进入 Phase 1。一个叫 `handle` 的函数名在 500 个仓库中出现 480 次——它不提供任何定位信息，直接排除。

### 3.6 Bloom Filter 预检：极端情况兜底

当搜索词全是低频词（IDF 过滤不了）但数量多时，用 Bloom Filter 做快速预排除：

```python
class RepoBloomPreFilter:
    """
    每个仓库一个 Bloom Filter，存该仓库所有符号名。
    
    空间开销: 500 仓库 × 10KB = 5MB（进程内存常驻）
    查询速度: 500 次 Bloom 查询 ≈ 0.1ms
    
    用途: Phase 1 SQL 查询前，先用 Bloom 从 500 缩到 50 个仓库，
          再对 50 个仓库做精确索引扫描，进一步加速。
    """
    
    def __init__(self):
        self.filters: dict[str, BloomFilter] = {}
    
    def quick_filter(self, search_terms: SearchTermSet) -> list[str]:
        """返回可能包含任一搜索词的仓库名列表"""
        all_terms = search_terms.all_terms()
        candidates = []
        for repo_name, bloom in self.filters.items():
            if any(bloom.check(term) for term in all_terms):
                candidates.append(repo_name)
        return candidates
    
    async def refresh(self):
        """Git webhook 触发 → 增量更新对应仓库的 Bloom Filter"""
        # ...
```

Bloom Filter 的特性：说"没有"是 100% 确定，说"可能有"有误报率（可配置，默认 1%）。对于 500 仓库的场景，Bloom Filter 通常能排除 80-90% 的仓库，极端情况下退化为全量扫描（但这是极端情况）。

### 3.7 分级缓存体系

```python
"""
L1: 进程内存 LRU Cache（命中率预计 40-60%）
    ├─ 热查询缓存: query_hash → [repo_names]
    ├─ Bloom Filter 数组: 500 个仓库 × 10KB = 5MB
    ├─ IDF 表: 150 万个符号的频率数据（~12MB）
    └─ 延迟: < 0.1ms

L2: Redis（命中率预计 20-30%）
    ├─ 查询结果缓存: TTL 1min
    ├─ 符号→仓库倒排索引: 热点符号的快速路由
    └─ 延迟: < 1ms

L3: PostgreSQL 全局符号索引（兜底）
    延迟: 5-20ms（常规情况 < 5ms）
"""
```

**预期：** L1 + L2 覆盖 70-80% 的查询，剩余走 L3 仍能 < 20ms。

### 3.8 延迟预算分解

以典型的 Phase 1 查询为例（搜 5 个符号，1.5M 行表）：

```
┌──────────────────────────────────────────────┐
│ 阶段                        │ 预算   │ 累计  │
├──────────────────────────────────────────────┤
│ 1. 搜索词预处理 + IDF 过滤   │ 1ms   │ 1ms   │
│ 2. Bloom Filter 预检（可选） │ 0.1ms │ 1ms   │
│ 3. 缓存查找（L1→L2）        │ 1ms   │ 2ms   │
│ 4. B-tree 索引扫描           │ 3ms   │ 5ms   │
│ 5. Bitmap OR 合并            │ 2ms   │ 7ms   │
│ 6. GROUP BY + Top-N排序      │ 5ms   │ 12ms  │
│ 7. 结果序列化 + 网络传输     │ 3ms   │ 15ms  │
│ 余量                        │ 35ms  │ 50ms  │
└──────────────────────────────────────────────┘
```

在 1.5M 行表上，这类查询的 PostgreSQL 执行时间通常是 **1-5ms**（实测值），远低于 50ms 预算。

### 3.9 静态映射表的新角色

原设计中的 `MODULE_REPO_MAP` 和 `repo_registry` 表仍然保留，但角色降级为**辅助信号**：

```python
def resolve_repos_v2(
    entities: ExtractedEntities,
    search_terms: SearchTermSet,
    phase1_results: list[CandidateRepo] | None = None
) -> list[str] | None:
    """
    仓库路由的新逻辑：全局符号索引为主，静态映射辅助。
    返回 None 表示走全局符号扫描（默认路径）。
    """
    # 路径 1: 有精确 code_refs → 全局符号索引（最快最准）
    # 不需要预路由，让 Phase 1 的 B-tree 直接定位
    if entities.code_refs:
        return None
    
    # 路径 2: Phase 1 结果置信度不够时，用静态映射补充候选仓库
    if phase1_results and len(phase1_results) < 2:
        # 全局扫描只找到 0-1 个仓库 → 可能是符号名没匹配上
        # 用静态映射和 repo_registry 语义匹配补充候选
        supplemental = []
        if entities.module and entities.module in MODULE_REPO_MAP:
            supplemental.extend(MODULE_REPO_MAP[entities.module])
        if entities.module:
            supplemental.extend(search_repo_registry(entities.module, top_k=3))
        return list(set(supplemental))[:5]
    
    # 路径 3: 完全无锚点 + Phase 1 零结果 → 反问用户
    if not phase1_results or len(phase1_results) == 0:
        return None  # 由上层处理，反问用户
    
    # 默认: Phase 1 结果足够 → 不需要静态映射
    return None
```

### 3.10 与全量 grep 的对比

```
场景: "OAuth token 刷新逻辑在哪"
搜索词: ["oauth", "token_refresh", "token", "refresh", "login", "auth"]

┌─────────────────────────┬──────────────────────┬──────────────────────┐
│                         │ 全量 grep             │ 全局符号索引+两阶段   │
│                         │ (每仓库 ripgrep)       │                      │
├─────────────────────────┼──────────────────────┼──────────────────────┤
│ 涉及仓库数              │ 500（全部）           │ Phase1: 500→Phase2: 3│
│ 搜索目标                │ 文件内容全文          │ 符号名索引→源码      │
│ IO 操作次数             │ 500次（或并发度受限） │ 1次SQL + 3次深度搜索 │
│ 实际延迟（估算）        │ 300-800ms             │ ~40ms                │
│ 结果质量                │ 海量匹配，难排序      │ 按仓库聚合，得分排序  │
│ 新仓库支持              │ 需要手动配置          │ 自动（webhook 增量）  │
└─────────────────────────┴──────────────────────┴──────────────────────┘
```

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

Code Worker 同时使用两种路径，元数据索引覆盖 95% 的查询（B-tree 索引，毫秒级），实时 ripgrep 兜底——当索引可能过期时（`updated_at < HEAD`）验证最新代码，并标注"此结果来自实时搜索，索引可能已过时"。具体流程见 [2.2 节 Phase 2](#22-检索函数两阶段)。

---

## 五、实体驱动的检索分发

```python
async def route_code_search(
    entities: ExtractedEntities,
    user_query: str
) -> SearchResult:
    """按实体类型路由到对应的检索路径。"""
    has_exact_refs = bool(entities.code_refs)
    has_module = bool(entities.module)
    
    if entities.req_ids or entities.person or entities.time_range:
        # 路径 D/E: 需求追溯 / 人员时间锚定
        # 不依赖符号搜索，走 git log 路径
        return await git_log_search(entities)
    
    # Step 1: 构造搜索词（所有路径共用，见第六章）
    search_terms = await build_search_terms(user_query, entities)
    
    if not search_terms.has_any:
        # 完全提取不到搜索词 → 反问用户
        return SearchResult(
            primary=[],
            method="none",
            note="无法从查询中提取有效的代码搜索词。",
            suggestions=generate_query_suggestions(user_query, entities)
        )
    
    if has_exact_refs:
        # 路径 A: 精确引用
        # 用户给了明确的文件名/函数名 → Phase 1 直接精确匹配
        # search_terms 中 exact_terms 权重最高，Phase 1 优先匹配
        return await code_search(
            query=user_query,
            entities=entities,
            search_terms=search_terms  # exact_terms 包含 code_refs
            # expand_context=True（默认）
        )
    elif has_module:
        # 路径 B: 模块锚定的关键词搜索
        # 从 module 构造了英文标识符 + 同义词表扩展
        # Phase 1 全局扫描时会同时使用 fuzzy_terms 和 tag_terms
        return await code_search(
            query=user_query,
            entities=entities,
            search_terms=search_terms
            # Phase 1 零命中时 → 静态映射辅助 → repo_registry 语义匹配
        )
    else:
        # 路径 C: 全量关键词搜索
        # 从 query 提取的所有搜索词
        # Phase 1 零命中时 → 反问用户
        return await code_search(
            query=user_query,
            entities=entities,
            search_terms=search_terms
        )
```

### 实体用法详表

| 实体 | 用法 | 优先级 | 示例 |
|------|------|-------|------|
| `code_refs` | 精确搜索——作为 `exact_terms` 直接匹配全局符号索引 | **最高** | `token_refresh` → `WHERE symbol_name = 'token_refresh'` |
| `req_ids` | 关联搜索——git log 中搜索需求 ID，不依赖符号索引 | 高 | `git log --grep="REQ-2024-0187"` 找变更文件 |
| `module` | 搜索词构造——映射中文术语→英文标识符；辅助 Phase 1 零命中时补充候选仓库 | 高 | "用户登录"→搜索词 `["login","auth","oauth"]` |
| `person` | 作者过滤——`git log --author="张三"` 限定变更范围 | 中 | 结合 `time_range` 精确定位 |
| `time_range` | 时间过滤——限制 git log 的 `--since` / `--until` | 中 | `git log --since="2026-05-29"` |
| `version` | 分支/tag 过滤 | 中 | `git log release/2026Q1` |

**关键变化：** `module` 实体不再主要用于仓库路由（限定搜哪些仓库），而是用于**搜索词构造**——把中文模块名翻译为英文代码标识符。仓库路由由全局符号索引（Phase 1）自动完成。

---

## 六、搜索词构造管线：从用户问题到代码标识符

这是整个检索链条的**第一公里**——也是最难的一环。核心挑战：用户的自然语言（真正常是中文）→ 可以在代码库中有效 grep 的英文标识符。

### 6.1 总览：先分类，再处理

不同类型的用户问题，转换策略完全不同。入口先做分类，不走万能翻译器：

```
用户问题
    │
    ▼
┌──────────────────────────────────────────┐
│ Step 0: Query Type Detection（< 1ms）     │
│ 基于规则 + 正则，不需要 LLM               │
└──────────────────────────────────────────┘
    │
    ├─ Type A: 有精确实体引用（code_refs 非空）
    │   "oauth.py 的 token_refresh"
    │   → 直接提取 + 形态扩展
    │
    ├─ Type B: 中英混合
    │   "用户登录的 OAuth token 刷新逻辑"
    │   → 提取英文（高置信） + 翻译中文
    │
    ├─ Type C: 纯中文业务描述
    │   "用户怎么登录的？登录之后 token 怎么刷新？"
    │   → 同义词表翻译 + 模块→符号关联
    │
    ├─ Type D: 需求追溯（req_ids 非空）
    │   "REQ-187 改了哪些代码"
    │   → 完全不同的路径，不走符号搜索，走 git log
    │
    └─ Type E: 人员/时间锚定（person/time_range 非空）
        "张三上周改了支付模块的什么"
        → git log 过滤 + 模块翻译
```

```python
def detect_query_type(entities: ExtractedEntities, raw_query: str) -> QueryType:
    """纯规则判断，不需要 LLM"""
    
    # 优先级最高：用户明确给了符号引用
    if entities.code_refs:
        return QueryType.EXACT_REFS
    
    # 需求ID → 走 git log 路径
    if entities.req_ids:
        return QueryType.REQ_TRACE
    
    if entities.person or entities.time_range:
        return QueryType.PERSON_TIME
    
    # 语言特征判断
    has_english = bool(re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', raw_query))
    has_chinese = bool(re.findall(r'[一-鿿]', raw_query))
    
    if has_chinese and has_english:
        return QueryType.MIXED_CN_EN
    elif has_chinese and not has_english:
        return QueryType.PURE_CN
    else:
        return QueryType.PURE_EN  # 纯英文，直接提取
```

### 6.2 Type A：精确实体引用

用户给了明确符号名。核心：**形态扩展**（morphological variants）——同一个语义在代码中可能有多种命名约定。

```python
def expand_exact_refs(code_refs: list[str]) -> list[Term]:
    """
    code_refs = ["oauth.py", "token_refresh"]
    → 提取 + 形态扩展
    """
    terms = []
    
    for ref in code_refs:
        # 文件路径 → 提取模块名 + 文件名
        if '/' in ref or '.' in ref:
            parsed = parse_file_ref(ref)
            # "src/auth/oauth.py" → 提取 "oauth", "auth"
            terms.append(Term(parsed.stem, weight=1.0, source="file_path"))
            if parsed.module:
                terms.append(Term(parsed.module, weight=0.6, source="file_path_module"))
        
        # 函数名/类名 → 拆分 + 形态扩展
        elif '_' in ref or ref[0].isupper():
            terms.append(Term(ref, weight=1.0, source="exact_symbol"))
            terms.extend(morphological_variants(ref))
    
    return deduplicate_terms(terms)


def morphological_variants(symbol: str) -> list[Term]:
    """
    token_refresh → 所有可能的代码写法变体。
    
    代码世界中，同样语义有多种命名约定:
      token_refresh    (snake_case)
      tokenRefresh     (camelCase)
      TokenRefresh     (PascalCase)
      refresh_token    (词序不同)
      refreshToken     (camelCase + 词序不同)
      token-refresh    (kebab-case，文件名)
    """
    variants = []
    parts = symbol.lower().split('_')  # ["token", "refresh"]
    
    if len(parts) < 2:
        return variants
    
    # 词序变换
    variants.append(Term('_'.join(reversed(parts)), weight=0.7,
                        source="reversed_order"))
    
    # 命名约定变换
    variants.append(Term(''.join(p.capitalize() for p in parts), weight=0.5,
                        source="pascal_case"))
    variants.append(Term(
        parts[0] + ''.join(p.capitalize() for p in parts[1:]), weight=0.5,
        source="camel_case"
    ))
    
    # 缩写扩展（如果映射表里有）
    for part in parts:
        if part in ABBREV_MAP:
            variants.append(Term(ABBREV_MAP[part], weight=0.4,
                                source="abbrev_expand"))
    
    return variants
```

### 6.3 Type B：中英混合——最重要的场景（~35% 查询）

策略：**英文部分信用户的（高置信），中文部分走翻译。**

```python
def extract_mixed_query(query: str, entities: ExtractedEntities) -> list[Term]:
    """
    "用户登录的 OAuth token 刷新逻辑"
    → 英文直提: "OAuth", "token" (用户明确给的，高置信)
    → 中文翻译: "用户登录" → login/auth, "刷新" → refresh
    """
    terms = []
    
    # Step 1: 提取英文标识符（用户明确提到，权重最高）
    english_tokens = re.findall(r'[a-zA-Z_][a-zA-Z0-9_]{2,}', query)
    for token in english_tokens:
        terms.append(Term(token, weight=0.95, source="user_provided_en"))
    
    # Step 2: 提取中文片段
    chinese_spans = extract_chinese_spans(query)
    # "用户登录的 [OAuth] [token] 刷新逻辑"
    # → chinese_spans = ["用户登录的", "刷新逻辑"]
    
    # Step 3: 每个中文片段独立翻译
    for span in chinese_spans:
        span_clean = clean_span(span)  # 去掉"的""了"等虚词
        if entities.module and entities.module in span_clean:
            # 这个中文片段包含了 Supervisor 识别的 module
            terms.extend(lookup_module_code_terms(entities.module))
        else:
            terms.extend(translate_chinese_span(span_clean, fallback_weight=0.6))
    
    # Step 4: 共现加分——英文 token 和中文翻译结果同时出现时，提升相关词权重
    terms = boost_cooccurring_terms(terms, english_tokens)
    
    return deduplicate_and_rank(terms)[:10]


def boost_cooccurring_terms(terms: list[Term], anchor_tokens: list[str]) -> list[Term]:
    """
    如果用户提到了 "OAuth" 和 "token"，
    那 "refresh_token" 比单纯的 "refresh" 更可能是目标。
    
    做法：检查每个候选词是否与锚定词有已知的共现模式。
    """
    COOCCURRENCE_PATTERNS = {
        ("oauth", "token"): {
            "refresh_token": +0.3, "access_token": +0.3,
            "token_refresh": +0.3, "grant_token": +0.2,
        },
        ("payment", "callback"): {
            "payment_callback": +0.3, "notify_url": +0.2,
        },
        # 更多模式从历史成功搜索中自动挖掘（见 6.9 反馈闭环）
    }
    
    anchor_set = {t.lower() for t in anchor_tokens}
    for term in terms:
        for (a, b), boosts in COOCCURRENCE_PATTERNS.items():
            if a in anchor_set and b in anchor_set:
                if term.text in boosts:
                    term.weight += boosts[term.text]
    return terms
```

### 6.4 Type C：纯中文——三层递进翻译

最常见的场景，用户完全用中文描述代码行为。

```python
async def translate_pure_cn_query(
    query: str,
    entities: ExtractedEntities
) -> list[Term]:
    """
    "用户怎么登录的？登录之后 token 怎么刷新？"
    
    三层递进，前一层命中了就停，不调下一层。
    """
    # ===== 第一层: 同义词映射表（~1ms，覆盖 60-70%）=====
    terms = lookup_synonym_map(query)
    if len(terms) >= 3:
        return terms[:10]
    
    # ===== 第二层: 模块→符号关联（~1ms，覆盖 15-20%）=====
    if entities.module:
        module_terms = lookup_module_code_symbols(entities.module, top_k=10)
        # "用户登录" → 该模块下最高频的符号:
        # login(0.95), authenticate(0.90), auth(0.85), token(0.78)...
        terms.extend(module_terms)
    
    if len(terms) >= 3:
        return terms[:10]
    
    # ===== 第三层: LLM 翻译（~300ms，缓存命中 ~5ms，覆盖 5-10%）=====
    return await llm_translate_to_identifiers(query)
```

**第一层：同义词映射表**

相比 2.4 节的简化版，这里是带权重和上下文的增强版：

```python
# 带权重的结构化映射
CODE_SYNONYM_MAP = {
    "登录": [
        Term("login", weight=0.9, context="通用登录", source="synonym_map"),
        Term("signin", weight=0.7, context="前端/UI层", source="synonym_map"),
        Term("authenticate", weight=0.8, context="后端认证逻辑", source="synonym_map"),
        Term("auth", weight=0.6, context="缩写，模块名/路径", source="synonym_map"),
        Term("do_login", weight=0.5, context="具体实现函数", source="synonym_map"),
    ],
    "刷新": [
        Term("refresh", weight=0.9, context="通用", source="synonym_map"),
        Term("renew", weight=0.6, context="Token/证书场景", source="synonym_map"),
        Term("rotate", weight=0.5, context="密钥轮换场景", source="synonym_map"),
        Term("reload", weight=0.4, context="配置/缓存场景", source="synonym_map"),
    ],
    # ... 底表数据来自 2.4 节 CODE_TERM_MAP，此处只展示增强结构
}
```

**第二层：模块→符号关联（自动统计，非人工维护）**

```python
# 从 global_symbol_index 中自动统计每个业务模块下高频出现的符号
# 定时任务每小时刷新一次
MODULE_SYMBOL_STATS = {
    "用户登录": [
        ("login", 0.95, 12),       # (符号, 归一化权重, 出现仓库数)
        ("authenticate", 0.90, 8),
        ("auth", 0.85, 45),        # 出现太多仓库，但在这个模块下确实高频
        ("oauth", 0.82, 6),
        ("token", 0.78, 30),
        ("session", 0.75, 15),
    ],
    # ...
}

def lookup_module_code_symbols(module: str, top_k: int = 10) -> list[Term]:
    """从模块标签反向查该模块最常关联的代码符号"""
    stats = MODULE_SYMBOL_STATS.get(module, [])
    return [Term(sym, weight=w * 0.7, source="module_symbol_stats")
            for sym, w, repo_count in stats[:top_k]
            if repo_count < 200]  # 出现在 >200 仓库中的符号区分度太低，跳过
```

### 6.5 Type D/E：需求追溯 / 人员时间锚定

这两类**不走符号搜索**，代码检索路径完全不同：

```python
async def git_log_search(entities: ExtractedEntities) -> SearchResult:
    """
    通过 git log 定位代码变更，不需要符号搜索词。
    核心：req_ids / author / time_range 的组合过滤。
    """
    args = []
    
    if entities.req_ids:
        for rid in entities.req_ids:
            args.extend(["--grep", rid])
    
    if entities.person:
        args.extend(["--author", entities.person])
    
    if entities.time_range:
        args.extend([
            "--since", entities.time_range.start.isoformat(),
            "--until", entities.time_range.end.isoformat()
        ])
    
    # git log 找变更文件列表
    changed_files = await run_git_log(args)
    
    if not changed_files:
        return SearchResult.empty(
            note="指定条件下未找到代码变更。"
        )
    
    # 对变更文件做精确读取（不搜全库，只读变更文件）
    results = []
    for file_info in changed_files[:20]:  # 最多 20 个文件
        chunk = await lookup_code_chunk_by_file(
            repo=file_info.repo,
            file_path=file_info.path,
            line=file_info.line
        )
        if chunk:
            results.append(chunk)
    
    return SearchResult(
        primary=results,
        method="git_log",
        changed_files=changed_files
    )
```

### 6.6 搜索词打分与截断

不管走哪条路径，最终都会产出一个候选词列表。最后一步是**排序 + 截断**，控制在 10 个以内：

```python
def score_and_truncate(
    terms: list[Term],
    query_type: QueryType,
    code_idf: CodeSymbolIDF
) -> SearchTermSet:
    """
    综合打分，输出分组的搜索词集。
    
    打分因子：
    1. 来源可信度（用户给的 > 同义词表 > LLM 翻译）
    2. 代码域 IDF（区分度高的符号优先）
    3. 符号特异性（长度惩罚极短的）
    4. 与 query_type 的匹配度
    """
    
    for term in terms:
        score = term.base_weight  # 0.3 - 1.0
        
        # 因子 1: 代码域 IDF
        idf = code_idf.idf.get(term.text, 1.0)
        if idf < 0.5:   # 出现在 >60% 仓库 → 几乎无区分度
            score *= 0.1
        else:
            score *= min(idf * 2, 1.0)
        
        # 因子 2: 符号长度
        if len(term.text) <= 2:
            score *= 0.2  # "id", "no" 这类极短词
        elif len(term.text) >= 8:
            score *= 1.15  # 长符号名更具体
        
        # 因子 3: 是否为已知热点符号（高频出现在 commit log 中）
        if term.text in HOTSPOT_SYMBOLS:
            score *= 1.1  # 经常被修改/讨论的函数更可能是用户关心的
        
        term.final_score = score
    
    # 排序
    terms.sort(key=lambda t: t.final_score, reverse=True)
    
    # 去重：去除子串包含关系中的较短者
    # 如果同时有 "refresh" 和 "token_refresh"，保留 "token_refresh"
    terms = remove_substring_duplicates(terms)
    
    top = terms[:10]
    
    # 分组：不同类型的搜索词用不同的匹配策略
    return SearchTermSet(
        exact_terms=[t for t in top if t.is_exact_match],
        fuzzy_terms=[t for t in top if t.is_fuzzy_match],
        tag_terms=[t for t in top if t.is_tag_match],
    )
```

### 6.7 LLM 翻译的工程化

同义词表覆盖不到时，LLM 翻译是兜底。关键不是翻译质量，而是**缓存策略**：

```python
@dataclass
class LLMTranslationCache:
    """三级缓存"""
    exact_cache: dict[str, list[str]]     # query_hash → terms（永久）
    semantic_cache: dict[str, list[str]]  # embedding 聚类 → terms
    ttl_cache: TTLCache                   # 24h TTL
    
    async def get(self, query: str) -> list[str] | None:
        # 1. 精确命中
        qhash = hashlib.md5(query.encode()).hexdigest()
        if qhash in self.exact_cache:
            return self.exact_cache[qhash]
        
        # 2. 规范化后的查询命中
        normalized = normalize_query(query)
        nhash = hashlib.md5(normalized.encode()).hexdigest()
        if nhash in self.ttl_cache:
            return self.ttl_cache[nhash]
        
        return None
    
    def set(self, query: str, terms: list[str]):
        qhash = hashlib.md5(query.encode()).hexdigest()
        self.exact_cache[qhash] = terms
        nhash = hashlib.md5(normalize_query(query).encode()).hexdigest()
        self.ttl_cache[nhash] = terms


def normalize_query(query: str) -> str:
    """
    规范化后缓存，大幅提升命中率：
    "用户登录的 OAuth token 刷新"  \
    "用户登录的OAuth token刷新"     → 都映射到同一 key
    "oauth token 刷新 用户登录"    /
    """
    # 去标点、去虚词、统一空格
    cleaned = re.sub(r'[，。！？、的了在吗哪是]', ' ', query)
    tokens = sorted(set(cleaned.lower().split()))
    return ' '.join(tokens)


TRANSLATION_PROMPT = """将以下中文业务描述翻译为可能的代码标识符。

要求：
- 输出英文标识符，用逗号分隔，最多 8 个
- 优先输出函数名级别的标识符（如 token_refresh），而不是笼统的词（如 data）
- 考虑常见命名约定：snake_case, camelCase, PascalCase
- 如果中文描述提到了具体技术（OAuth, Redis, Kafka），在候选词中体现

中文: "{query}"

英文标识符:"""


async def llm_translate_to_identifiers(query: str) -> list[Term]:
    """
    仅在以下情况触发：
    1. 同义词映射表未覆盖
    2. query 长度 > 10 字（有足够语义信息）
    3. 不含任何英文词（说明用户纯中文输入）
    
    不会对每个查询都调 LLM——缓存命中率预计 60-70%。
    """
    # 查缓存
    cached = await TRANSLATION_CACHE.get(query)
    if cached:
        return [Term(t, weight=0.5, source="llm_cached") for t in cached]
    
    # 调轻量模型（Haiku），~300ms
    response = await llm.invoke(
        TRANSLATION_PROMPT.format(query=query)
    )
    terms = [t.strip() for t in response.split(",") if t.strip()]
    
    # 入缓存
    TRANSLATION_CACHE.set(query, terms)
    
    return [Term(t, weight=0.5, source="llm_translate") for t in terms]
```

### 6.8 不依赖翻译的快速路径

以下路径不需要任何跨语言映射，直接在 6.1 的 Query Type Detection 中分流：`code_refs` 精确匹配、`req_ids` git log 搜索、`person` + `time_range` git log 过滤、中英混合输入直接提取英文部分。这四类覆盖了估计 60%+ 的中文查询场景。

### 6.9 反馈闭环：从成功搜索中学习

让系统越用越好的关键机制：

```python
class SearchTermFeedbackLoop:
    """
    记录每次搜索的"成功"信号 → 自动优化同义词映射和权重。
    
    "成功"的定义（多种信号组合）：
    1. 用户点击了搜索结果并停留 > 30 秒
    2. 用户复制了搜索结果中的代码片段
    3. 用户在同一 session 中没有改搜索词（结果足够好）
    4. 用户明确点赞/点踩（如果 UI 支持）
    """
    
    async def record_search(
        self,
        query: str,
        search_terms: SearchTermSet,
        phase1_repos: list[CandidateRepo],
        clicked_results: list[str],
        session_id: str
    ):
        """记录一次搜索的完整链路，供离线分析"""
        await db.execute("""
            INSERT INTO search_feedback_log
            (query, search_terms, candidate_repos, clicked, session_id, timestamp)
            VALUES ($1, $2, $3, $4, $5, NOW())
        """, query, search_terms.to_json(), phase1_repos, clicked_results, session_id)
    
    async def mine_new_mappings(self):
        """
        从成功搜索日志中挖掘新的 中文→英文 映射。
        
        发现模式：
        - 用户搜 "额度校验" → 搜索词 ["credit_limit", "quota_check"]
        - 用户点击了 credit_limit → 成功
        - 自动学习: "额度校验" → credit_limit（新增映射，权重 0.5）
        """
        patterns = await db.fetch("""
            SELECT 
                query,
                search_terms,
                clicked
            FROM search_feedback_log
            WHERE clicked IS NOT NULL
              AND timestamp > NOW() - INTERVAL '7 days'
        """)
        
        for row in patterns:
            chinese_spans = extract_chinese_spans(row['query'])
            clicked_terms = set(row['clicked'])
            search_terms = set(row['search_terms'])
            
            for span in chinese_spans:
                # 找到被点击的、且非用户直接提供的搜索词
                for term in clicked_terms:
                    if term in search_terms and term not in row['query']:
                        # 这是一个 中文→英文 映射的候选
                        CODE_SYNONYM_MAP.setdefault(span, []).append(
                            Term(term, weight=0.5, context="auto", source="feedback")
                        )
        
        # 权重衰减：已有映射项，连续 3 周未被点击 → 降权 0.1
        await self.decay_stale_mappings()
```

### 6.10 完整管线

```python
async def build_search_terms(
    query: str,
    entities: ExtractedEntities
) -> SearchTermSet:
    """
    从用户问题到搜索词的完整转换管线。
    
    延迟预算：
    Type A（精确引用）: < 5ms（纯规则）
    Type B（中英混合）: < 10ms（同义词表 + 规则）
    Type C（纯中文）:   < 15ms（同义词表命中，95%情况）
                       ~300ms 首次 / ~5ms 缓存（LLM 翻译，5%情况）
    Type D/E（需求/人员）: 不经过此管线
    """
    
    query_type = detect_query_type(entities, query)
    
    # 不需要符号搜索的类型提前返回
    if query_type in (QueryType.REQ_TRACE, QueryType.PERSON_TIME):
        return SearchTermSet.empty()
    
    # Step 1: 按 query_type 生成候选词
    if query_type == QueryType.EXACT_REFS:
        raw_terms = expand_exact_refs(entities.code_refs)
        # 补充：用户可能同时给了中文描述
        if has_chinese(query):
            chinese_spans = extract_chinese_spans(query)
            for span in chinese_spans:
                raw_terms.extend(translate_chinese_span(span, fallback_weight=0.4))
    
    elif query_type == QueryType.MIXED_CN_EN:
        raw_terms = extract_mixed_query(query, entities)
    
    elif query_type in (QueryType.PURE_CN, QueryType.PURE_EN):
        raw_terms = await translate_pure_cn_query(query, entities)
    
    else:
        raw_terms = []
    
    # Step 2: 应用代码域 IDF 过滤低区分度词
    filtered = [t for t in raw_terms
                if t.text not in CODE_SYMBOL_IDF.noise_symbols]
    
    # Step 3: 分数计算 + 截断
    return score_and_truncate(filtered, query_type, CODE_SYMBOL_IDF)
```

### 6.11 效果预期

| 查询类型 | 占比（估计） | 延迟 | 覆盖方式 |
|---------|------------|------|---------|
| Type A: 精确引用 | ~30% | < 5ms | 直接提取 + 形态扩展 |
| Type B: 中英混合 | ~35% | < 10ms | 英文直提 + 同义词表 |
| Type C: 纯中文 | ~25% | < 15ms（95%） | 同义词表 + 模块→符号关联 |
| Type C 兜底 | ~5% | ~300ms 首次 / ~5ms 缓存 | LLM 翻译 |
| Type D/E: 需求/人员 | ~5% | git log 路径 | 不经过符号搜索管线 |

**同义词映射表覆盖率：** 冷启动 60-70%，上线 3 个月后（反馈闭环运转）→ 85-90%。

**核心洞察：** 90% 的查询不需要 LLM 翻译。同义词表 + 形态扩展 + 模块→符号关联这三层确定性规则覆盖了绝大多数场景。LLM 只在冷启动阶段和长尾查询时触发，而且结果缓存后不再重复调用。

---

## 七、反事实分析：去掉各组件的影响

> **量化估计：** 如果完全去掉实体抽取，Code Worker 的 Recall@10 下降约 20-30 个百分点。但不同组件的影响程度不同。

代码检索的特殊问题：用户要的不是"语义上相似的代码"，而是"那一份确切的代码"。"oauth.py 里的 TokenService"——用户知道文件在哪，只是不想手动翻。

### 7.1 去掉实体抽取

| 失去的实体 | 失去的检索能力 | 影响 |
|-----------|--------------|------|
| `code_refs` | 精确文件/函数名匹配 → exact_terms 为空 | **最严重**——失去最高精度路径，Phase 1 只能走模糊匹配 |
| `module` | 搜索词构造辅助信号 + Phase 1 零命中时的静态映射兜底 | 搜索词质量下降（纯中文时更明显） |
| `req_ids` | git log 关联搜索 | 跨源溯源（需求→代码）链路断裂 |
| `person` + `time_range` | 作者/时间过滤 | 无法缩小历史变更范围 |

### 7.2 去掉全局符号索引（退回搜前路由）

如果将第三章的全局符号索引退回原设计的 MODULE_REPO_MAP 搜前路由：

| 场景 | 影响 |
|------|------|
| 新仓库上线 | 需人工更新映射表，遗漏则搜不到 |
| "用户登录"类宽泛模块 | 映射到 50+ 仓库，限定失去意义 |
| 跨模块查询 | 静态映射表覆盖不到的组合查询 → 退化为全量 grep |
| Phase 1 延迟 | 全量 grep 从 15ms 变为 300-800ms（500 仓库） |

### 7.3 去掉搜索词构造管线（第六章）

如果退回简单的关键词提取（无查询类型检测、无形态扩展、无共现加分）：

| 场景 | 影响 |
|------|------|
| "token_refresh" 精确查询 | 失去形态扩展 → 搜不到 `refreshToken` 写法 |
| "用户登录的 OAuth token 刷新" | 中英混合场景 → 中文部分无法翻译为搜索词 |
| 纯中文查询 | Recall@10 下降 15-20pp（完全依赖 LLM，无同义词表兜底） |
| 高频噪声符号污染 | "handle""process" 等进入搜索词 → Phase 1 结果集膨胀 10 倍 |

---

## 八、数据摄入（Code Worker 视角）

```
代码仓库 (Git)
  → TreeSitter AST 解析（保留函数/类边界，按语法单元分块）
  → 双路输出:
      ├─ code_chunks 表（完整源代码 + 结构化元数据 + 调用图，不生成 embedding）
      └─ global_symbol_index 表（符号名 + 位置 + 业务标签，用于 Phase 1 快速扫描）
  → 触发方式：Git Webhook（push 事件）→ 增量更新变更文件；
             存量代码全量导入
```

与之前设计的关键差异：**去掉了 BGE-M3 嵌入环节**，新增了 `global_symbol_index` 表作为跨仓库检索的核心基础设施。代码 chunk 不再生成 embedding，只存储结构化元数据和源代码原文。

> 完整的数据摄入管道设计见 [数据摄入管道设计](SPMA-design-05-data-ingestion.md)。注意：该文档中与代码 embedding 相关的内容需同步更新；需新增 `global_symbol_index` 表的摄入流程。

---

## 九、设计变更记录

| 日期 | 变更 | 原因 |
|------|------|------|
| 2026-06-06 | 第三章重写：MODULE_REPO_MAP 搜前路由 → 全局符号索引 + 两阶段搜后聚合 | 静态映射表在数百仓库规模下不可线性扩展；搜后聚合更符合"全局索引 + Top-K 深度搜索"的模式 |
| 2026-06-06 | 新增 3.2 `global_symbol_index` 表设计 | 两阶段检索的核心基础设施 |
| 2026-06-06 | 新增 3.5 代码域 IDF | 自动过滤 `get`/`handle` 等高频噪声符号 |
| 2026-06-06 | 新增 3.6 Bloom Filter 预检 | 极端情况下的快速预排除 |
| 2026-06-06 | 新增 3.7 分级缓存体系 | L1 进程内存 + L2 Redis + L3 PostgreSQL，目标覆盖 70-80% 查询 |
| 2026-06-06 | 第六章重写：中文查询处理 → 搜索词构造管线 | 从单一翻译策略扩展为 Type A-E 五类查询类型检测 + 对应转换策略；新增形态扩展、共现加分、模块→符号关联、反馈闭环 |
| 2026-06-06 | 2.1-2.3、五、七 同步更新 | 一致性问题——多处引用了旧的多仓库路由和搜索词构造逻辑 |

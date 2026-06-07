# Design: Code Worker 设计（代码检索）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责下发检索参数给本 Worker
> 模块职责：通过 glob + grep + read_file 三工具协同，结合全局符号索引和 AST 调用图，对数百个代码仓库执行"发现→定位→理解"三阶段检索

---

## 模块在架构中的位置

```
Supervisor Agent（实体抽取 + 意图分类）
    │
    ├── Doc Worker
    ├── Code Worker  ← 本文档范围
    │   ├─ 搜索词构造管线（用户问题 → 代码标识符 + 文件路径模式）
    │   │
    │   ├─ Phase 0: 文件路径路由（文件路径缓存匹配 500→5 候选仓库）
    │   ├─ Phase 1: 实时 ripgrep 搜索（候选仓库内并行 grep，始终最新代码）
    │   │
    │   ├─ Phase 2: 候选仓库深度搜索
    │   │   ├─ grep 精确符号匹配（B-tree 索引 + 实时 ripgrep）
    │   │   ├─ glob 关联文件发现（test、config、schema、migration）
    │   │   ├─ read_file 完整文件上下文（import 链、模块级代码）
    │   │   └─ AST 调用图扩展（调用链上下文补全）
    │   │
    │   └─ Phase 3: 跨仓库结果去重排序 + 上下文理解
    │
    └── SQL Worker
```

---

## 一、设计决策：为什么 Code Worker 用 ripgrep 实时搜索，不建内容索引

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

### 1.2 业界做法调研

| 工具 | 索引方案 | 索引复杂度 | 更新方式 | 规模 |
|------|---------|-----------|---------|------|
| **Claude Code** | **零索引**，纯 ripgrep（Glob→Grep→Read 循环） | 无 | 无需更新，始终搜最新代码 | 单仓库交互式 |
| **Livegrep** | **Trigram 索引**（3 字符序列，语言无关） | 低：只读原始字节，无需解析语法 | 周期性全量重建 | 单机 GB 级 |
| **Sourcegraph/Zoekt** | Trigram 索引 + ctags 符号（符号仅作排序增强） | 中：trigram 是主体 | 轮询仓库变更 → 增量重建 | 500K+ 仓库 |
| **GitHub Blackbird** | **稀疏 Ngram 索引**（变长 Ngram，自研 Rust 引擎） | 中：Kafka 实时流式摄取 | 实时流式增量 | 2 亿仓库 |
| **OpenGrok** | Lucene 倒排索引 | 高：完整文本分析 + 分词 | 周期性全量重建 | 单机/小集群 |

**核心发现：业界无人使用"AST 符号索引"。** 要么零索引（Claude Code），要么用**语言无关的 Ngram 索引**（Livegrep/Zoekt/Blackbird）——后者只需读原始字节提取 Ngram，不需要 TreeSitter 解析任何语言。

**我们上一版设计的 `global_symbol_index`（AST 解析每个函数/类名建索引）是业界已知方案中最重的——它的维护成本比 embedding 更高，不是更低。** 这个批评成立。

### 1.3 Claude Code 的做法

Claude Code 完全不做代码索引和 embedding：

```
1. Glob   → 按文件名模式发现文件（**/*.py, src/auth/**）
2. Grep   → ripgrep 搜索文件内容（符号、字符串、正则）
3. Read   → 读取完整文件（默认 2000 行）
4. 理解   → LLM 在上下文中理解代码结构
5. 重复   → 根据需要再 Grep → 再 Read → 再理解
```

Boris Cherny（Claude Code 技术负责人）公开说过放弃本地向量数据库的三个原因：
- ripgrep 实测效果远超 RAG，"by a lot"
- 索引导入工程包袱——代码持续变更需同步索引
- 代码搜索的特殊性：函数名要么出现要么不出现，不存在"语义上接近"这回事

### 1.4 我们场景的适配

Claude Code 的方案有两个前提：
- 200K token 上下文窗口（一次性装下大量代码）
- 单用户、单任务的交互模式（agent 可以多轮搜索）

这两个前提在我们的 RAG 系统里不完全成立：
- 系统是**多用户共享的知识库**，用户期望**一次返回结果**，不是多轮交互
- 用户期望**秒级响应**，不能像 agent 那样慢慢搜

**但核心结论仍然成立：** ripgrep 比 embedding 更准确，索引导入维护成本，这两个事实不因为场景不同而改变。

因此 Code Worker 的路线是：

> **文件路径缓存（轻量路由） + 实时 ripgrep 搜索（主引擎） + AST 调用图扩展（上下文补全）。不建代码内容索引（不做 embedding，也不做符号索引）。**

### 1.5 为什么文件路径缓存不是"内容索引"

| | global_symbol_index（旧版） | 文件路径缓存（新版） |
|---|---|---|
| **存什么** | 每个函数/类名 + 位置 | 每个文件的路径 |
| **怎么生成** | TreeSitter AST 解析每种语言 | `find` / `git ls-files` |
| **更新成本** | 重新解析变更文件 | 重新列出变更目录的文件 |
| **数据量** | ~300MB（150 万符号） | ~25MB（50 万文件路径） |
| **会过期吗** | 会——重命名/删除函数 | 会——重命名/删除文件，但概率低得多 |
| **能做什么** | 搜"token_refresh 在哪个仓库" | 搜"哪个仓库有 src/auth/ 目录" |

文件路径缓存是**仓库路由**用的，不是内容搜索用的。内容搜索始终走实时 ripgrep。

### 1.6 与 Doc Worker 的路线差异

Doc Worker 用 embedding（BGE-M3），Code Worker 不用。这不是不一致——是两种数据源的本质差异决定的：

| | PRD 文档 | 代码 |
|---|---|---|
| **用户怎么搜** | "用户登录的需求是怎么定的？"——自然语言描述行为 | "oauth.py 的 token_refresh"——有精确的符号名 |
| **内容特征** | 中文长文本，词汇多样化 | 英文标识符，高度结构化 |
| **embedding 的价值** | 中文语义匹配，跨表述召回 | 语义相似≠功能正确，嵌入向量对代码变更不敏感 |
| **grep 的价值** | 低——用户很少记得文档里的精确措辞 | 高——函数名/类名/文件名是精确的检索锚点 |
| **过期风险** | 低——文档更新频率低 | 高——代码随时 commit，任何索引都跟不上 |
| **适合的检索方式** | embedding 为主 + BM25 补充 | **ripgrep 实时搜索为主，不建内容索引** |

---

## 二、检索策略：grep 先行 + AST 调用图扩展

### 2.1 两阶段检索路径

Code Worker 采用**轻量路由 + 实时搜索**策略——文件路径缓存确定候选仓库，ripgrep 搜最新代码：

```
所有查询 → 搜索词构造管线（见第六章）
              │
              ▼
          Phase 0: 文件路径路由（~30ms）
              │  文件路径缓存匹配 → 候选仓库 5~8 个
              │
              ▼
          Phase 1: 实时 ripgrep 搜索（~200ms）
              │  候选仓库内并行 ripgrep → 始终最新代码
              │
              ├─ 命中：
              │     ▼
              │   Phase 2: 上下文补全（~50ms）
              │     ├─ read_file 完整文件上下文
              │     ├─ glob 关联文件发现
              │     └─ AST 调用图扩展
              │
              └─ 零命中 → 扩展搜索词重试 / 反问用户
```

### 2.2 检索函数（两阶段）

```python
async def code_search(
    query: str,
    entities: ExtractedEntities,
    search_terms: SearchTermSet | None = None
) -> SearchResult:
    """轻量路由 + 实时搜索：文件路径路由 → ripgrep → 上下文补全。"""
    if search_terms is None:
        search_terms = await build_search_terms(query, entities)
    
    if not search_terms.has_any:
        return await git_log_search(entities)
    
    # ===== Phase 0: 文件路径路由（~30ms）=====
    candidate_repos = await route_repos(
        search_terms=search_terms,
        entities=entities,
        min_candidates=3
    )
    
    if not candidate_repos:
        # 路由完全失败 → 反问用户或全量兜底
        if should_ask_user(entities):
            return ask_clarification_result(query)
        candidate_repos = await fallback_route(search_terms, limit=10)
    
    # ===== Phase 1: 实时 ripgrep 搜索（~200ms）=====
    ripgrep_results = await parallel_ripgrep(
        repos=candidate_repos,
        patterns=search_terms.all_terms()[:10],
        file_patterns=infer_file_patterns(search_terms, entities)
    )
    
    if not ripgrep_results:
        # ===== 渐进式回退：不直接返回空（最多 ~470ms，见 2.2.1 节）=====
        fallback_results = await progressive_fallback_search(
            original_patterns=search_terms.all_terms()[:10],
            candidate_repos=candidate_repos,
            file_patterns=infer_file_patterns(search_terms, entities),
            original_query=query
        )
        
        if fallback_results:
            ripgrep_results = fallback_results
            # 继续走 Phase 2 上下文补全...
        else:
            return SearchResult(
                primary=[],
                note=(
                    f"在 {len(candidate_repos)} 个候选仓库中均未找到匹配。\n"
                    f"已尝试: 精确搜索 → 单字拆分 → 扩大仓库范围 → 模糊匹配。\n"
                    f"搜索词: {search_terms.all_terms()[:10]}\n"
                    f"建议: 提供文件名、函数名或需求ID以获得精确结果。"
                ),
                suggestions=generate_query_suggestions(query, entities),
                method="all_fallbacks_exhausted"
            )
    
    # ===== Phase 2: 上下文补全（~50ms）=====
    # read_file 完整文件 + glob 关联文件 + AST 调用图扩展
    file_contexts = await read_file_context(
        seed_files=ripgrep_results.files[:10],
        candidate_repos=candidate_repos,
        expand_imports=True
    )
    related_files = await glob_related_files(ripgrep_results, candidate_repos)
    ast_expanded = await expand_via_call_graph(ripgrep_results, direction="both")
    
    ranked = rank_and_deduplicate(
        ripgrep_results, file_contexts, related_files, ast_expanded,
        query, search_terms
    )
    
    return SearchResult(
        primary=ranked[:20],
        file_contexts=file_contexts,
        candidate_repos=candidate_repos,
        method="file_path_routing+ripgrep+read_file+ast"
    )
```

**Phase 1 ripgrep 搜索实现：**

```python
async def parallel_ripgrep(
    repos: list[str],
    patterns: list[str],
    file_patterns: list[str] | None = None,
    max_results_per_repo: int = 50,
    timeout_ms: int = 300
) -> RipgrepResults:
    """
    在候选仓库中并行执行 ripgrep。
    
    参数:
    - repos: 候选仓库列表（已从文件路径路由得出）
    - patterns: ripgrep 搜索模式（从搜索词构造，支持正则）
    - file_patterns: 文件类型过滤（如 ["*.py", "*.java"]）
    - max_results_per_repo: 每个仓库最多返回的结果数
    - timeout_ms: 单个仓库的搜索超时
    
    返回: 按仓库+文件聚合的匹配结果
    """
    pattern_str = '|'.join(patterns)
    glob_str = ' '.join(f'-g "{p}"' for p in (file_patterns or []))
    
    tasks = []
    for repo in repos:
        repo_path = REPO_WORK_DIR / repo
        cmd = f"rg --json -l {glob_str} '{pattern_str}' {repo_path}"
        tasks.append(run_ripgrep(cmd, timeout_ms))
    
    results = await asyncio.gather(*tasks)  # 并行执行
    return aggregate_results(results, max_results_per_repo)
```

### 2.2.1 Phase 1 零命中的渐进式回退策略

ripgrep 精确搜索零命中时，不直接返回空结果——逐层降低搜索精度，换取召回。四层递进，任一层命中就停：

```
Layer F1: 词干拆分搜索（~20ms）
  把 "credit_limit_validator" → ["credit", "limit", "validator"] → 分别搜
  任何一个词干匹配到的文件可能就是目标

Layer F2: 扩大候选仓库范围（~50ms）
  文件路径路由可能漏了仓库 → 用仓库注册表补充更多候选仓库

Layer F3: 编辑距离模糊匹配（~100ms）
  后缀替换: validator↔validation, refresh↔refreshing
  字符交换: refresh↔refersh

Layer F4: LLM 重新解释搜索意图（~300ms）
  让 LLM 基于原始 query 构造完全不同的搜索方向
```

```python
async def progressive_fallback_search(
    original_patterns: list[str],
    candidate_repos: list[str],
    file_patterns: list[str] | None = None,
    original_query: str = ""
) -> RipgrepResults | None:
    """
    Phase 1 ripgrep 零命中时，走四层渐进式回退。
    
    每层代价递增、精度递减。任一层命中就停止。
    最坏情况总延迟: ~470ms（仍在可接受范围）。
    """
    
    # ===== Layer F1: 词干拆分搜索（~20ms）=====
    stems = _extract_stems_from_patterns(original_patterns)
    stems = {s for s in stems if len(s) >= 3}
    
    if stems:
        stem_pattern = '|'.join(stems)
        stem_results = await parallel_ripgrep(
            repos=candidate_repos,
            patterns=[stem_pattern],
            file_patterns=file_patterns,
            max_results_per_repo=30
        )
        
        if stem_results:
            stem_results.method = "fallback_stem_split"
            stem_results.note = (
                f"精确搜索无结果，已自动扩展为单字搜索。"
                f"原始搜索词: {original_patterns[:5]}"
            )
            stem_results.confidence = 0.75
            return stem_results
    
    # ===== Layer F2: 扩大候选仓库范围（~50ms）=====
    expanded_repos = await query_repo_registry(
        keywords=list(stems) if stems else original_patterns,
        exclude=candidate_repos
    )
    
    expanded_candidates = list(dict.fromkeys(
        candidate_repos + [r.name for r in expanded_repos[:5]]
    ))
    
    if len(expanded_candidates) > len(candidate_repos):
        search_pattern = '|'.join(stems) if stems else '|'.join(original_patterns)
        expanded_results = await parallel_ripgrep(
            repos=expanded_candidates,
            patterns=[search_pattern],
            file_patterns=file_patterns,
            max_results_per_repo=20
        )
        
        if expanded_results:
            expanded_results.method = "fallback_expanded_repos"
            expanded_results.note = (
                f"在更多仓库中找到匹配。"
                f"新增搜索仓库: {set(expanded_candidates) - set(candidate_repos)}"
            )
            expanded_results.confidence = 0.65
            return expanded_results
    
    # ===== Layer F3: 编辑距离模糊匹配（~100ms）=====
    fuzzy_patterns = _generate_fuzzy_variants(
        original_patterns,
        max_variants=20
    )
    
    if fuzzy_patterns:
        fuzzy_results = await parallel_ripgrep(
            repos=expanded_candidates,
            patterns=fuzzy_patterns[:10],
            file_patterns=file_patterns,
            max_results_per_repo=15
        )
        
        if fuzzy_results:
            fuzzy_results.method = "fallback_fuzzy_match"
            fuzzy_results.note = (
                f"精确搜索无结果，尝试了模糊匹配。"
                f"可能匹配: {fuzzy_patterns[:5]}"
            )
            fuzzy_results.confidence = 0.55
            return fuzzy_results
    
    # ===== Layer F4: LLM 重新解释搜索意图（~300ms）=====
    if original_query:
        alternative_terms = await _llm_alternative_search_terms(
            original_query=original_query,
            failed_terms=original_patterns
        )
        
        alt_results = await parallel_ripgrep(
            repos=expanded_candidates,
            patterns=alternative_terms[:10],
            file_patterns=file_patterns,
            max_results_per_repo=20
        )
        
        if alt_results:
            alt_results.method = "fallback_llm_retry"
            alt_results.note = (
                f"尝试了替代搜索方向: {alternative_terms[:5]}"
            )
            alt_results.confidence = 0.45
            return alt_results
    
    return None


def _extract_stems_from_patterns(patterns: list[str]) -> set[str]:
    """
    将搜索模式拆分为独立词干。
    
    ["credit_limit", "quota_check", "limit_validation"]
    → {"credit", "limit", "quota", "check", "validation"}
    """
    stems = set()
    for p in patterns:
        parts = re.split(r'[_-]', p)
        for part in parts:
            sub_parts = re.findall(
                r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', part
            )
            stems.update(s.lower() for s in sub_parts if len(s) >= 3)
    return stems


def _generate_fuzzy_variants(
    patterns: list[str],
    max_variants: int = 20
) -> list[str]:
    """
    生成搜索词的编辑距离变体。
    
    只做两种安全变换，不生成随机变体（会引入噪音）：
    1. 后缀替换: validator↔validation↔validate
    2. 常见字符交换: refresh↔refersh
    """
    SUFFIX_VARIANTS = {
        'er':   ['ing', 'ion', 'ed', 'ement'],
        'ing':  ['er', 'ed', 'ion'],
        'ion':  ['e', 'ing', 'ed'],
        'ed':   ['ing', 'er', 'ion'],
        'ment': ['e', 'ing'],
    }
    
    variants = []
    for p in patterns:
        for suffix, replacements in SUFFIX_VARIANTS.items():
            if p.endswith(suffix):
                base = p[:-len(suffix)]
                for repl in replacements:
                    variants.append(base + repl)
        
        # 相邻字符交换
        for i in range(len(p) - 1):
            swapped = list(p)
            swapped[i], swapped[i+1] = swapped[i+1], swapped[i]
            variants.append(''.join(swapped))
    
    return list(dict.fromkeys(v for v in variants if v not in patterns))[:max_variants]


async def _llm_alternative_search_terms(
    original_query: str,
    failed_terms: list[str]
) -> list[str]:
    """
    当所有搜索策略都失败时，让 LLM 重新理解用户意图，
    构造完全不同的搜索方向。
    """
    PROMPT = f"""用户的搜索查询是："{original_query}"

我们尝试了以下英文标识符，但在代码库中全部找不到：
{chr(10).join(f'  ✗ {t}' for t in failed_terms[:5])}

请分析用户的真实意图，提出完全不同的搜索思路：
1. 用户可能想问什么具体的技术概念？
2. 这些概念在代码中可能用什么其他命名？
3. 有没有更通用的关键词可以先定位到相关模块？

输出：5 个新的英文标识符，用逗号分隔。"""

    response = await llm.invoke(PROMPT)
    return [t.strip() for t in response.split(",") if t.strip()]
```

**回退结果的透明度：** 回退命中的结果必须标注降级策略，让用户知道"这不是精确匹配，但可能是你要的"：

```python
# RipgrepResults 的附加元数据
class RipgrepResults:
    files: list[FileMatch]
    method: str       # "ripgrep" | "fallback_stem_split" | "fallback_expanded_repos" | ...
    note: str         # 给用户的说明
    confidence: float # 1.0（精确）→ 0.3（四层兜底），UI 据此做视觉区分
```

**典型效果：**

```
场景: 用户搜 "额度校验" → 翻译为 ["credit_limit", "quota_check", "limit_validation"]
实际代码: src/credit/credit_limit_validator.py::validate_credit_limit()

原方案:
  ripgrep("credit_limit|quota_check|limit_validation")
  → 零命中（validator ≠ validation）
  → 返回空结果 ❌

新方案:
  Layer F1: 词干拆分 → ["credit", "limit", "quota", "check", "validation"]
  → ripgrep("credit|limit|quota|check|validation")
  → 命中 credit_limit_validator.py（"credit"+"limit"两个词干同时命中）✓
  → 标注 "fallback_stem_split"，confidence=0.75
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

### 2.6 三工具协同：glob + grep + read_file

Code Worker 采用三工具协同模式——这与 Claude Code 的 Grep→Read→Understand 循环一致，但适配了我们的多用户 RAG 场景：

| 工具 | 角色 | 回答什么问题 | 延迟 | 适用场景 |
|------|------|-------------|------|---------|
| **glob** | 面定位 | "这个模块有哪些文件？" | ~5ms | 项目结构探索、关联文件发现 |
| **grep** | 点定位 | "token_refresh 在哪一行？" | ~10ms | 符号/模式精确匹配 |
| **read_file** | 深度理解 | "这个文件完整内容是什么？谁 import 了它？" | ~5ms | 上下文补全、跨文件理解 |

三者不可互相替代，各补对方的盲区：

```
glob 的盲区：知道文件路径，不知道文件里有什么   → grep 补
grep 的盲区：知道第 42 行有匹配，不知道前后文    → read_file 补
read 的盲区：知道一个文件，不知道还有哪些相关文件 → glob 补
```

#### 2.6.1 glob：文件路径模式匹配

**解决的核心问题：** 代码内容搜索走实时 ripgrep，但文件路径的语义信息不在 ripgrep 的匹配范围中。用户问"认证模块的目录结构是怎样的"——这不是内容查询，是文件路径查询。

```python
async def glob_file_discovery(
    search_terms: SearchTermSet,
    candidate_repos: list[str],
    entities: ExtractedEntities
) -> list[FileInfo]:
    """
    从搜索词和实体中推断文件路径模式，在候选仓库中匹配。
    
    输入: search_terms=["oauth", "token"], module="用户登录"
    输出: [
        FileInfo(repo="auth-service", path="src/auth/oauth.py"),
        FileInfo(repo="auth-service", path="src/auth/oauth_test.py"),
        FileInfo(repo="auth-service", path="config/oauth_config.yaml"),
    ]
    """
    patterns = []
    
    # 来源1: 从搜索词构造文件路径模式
    for term in search_terms.exact_terms + search_terms.fuzzy_terms:
        patterns.append(f"**/*{term}*")        # **/*oauth*
        patterns.append(f"**/{term}/**")       # **/oauth/**
        patterns.append(f"**/*{term}*.py")     # **/*oauth*.py
        patterns.append(f"**/test*{term}*")    # test files
    
    # 来源2: 从 module 实体推断目录结构
    if entities.module:
        module_patterns = module_to_path_patterns(entities.module)
        # "用户登录" → ["**/auth/**", "**/login/**", "**/sso/**"]
        patterns.extend(module_patterns)
    
    # 来源3: 常见工程文件模式
    patterns.extend([
        "**/README*", "**/*.yaml", "**/*.yml", "**/*.toml",
        "**/Dockerfile*", "**/docker-compose*", "**/Makefile",
    ])
    
    # 去重 + 限制数量
    patterns = list(dict.fromkeys(patterns))[:20]
    
    # 在每个候选仓库中并行执行 glob
    results = []
    for repo in candidate_repos:
        repo_files = await glob_in_repo(repo, patterns)
        for f in repo_files:
            f.relevance = score_file_relevance(f, search_terms, entities)
        results.extend(repo_files)
    
    return sorted(results, key=lambda f: f.relevance, reverse=True)[:30]
```

**glob 的典型使用场景：**

| 用户查询 | glob 模式 | 发现内容 |
|---------|----------|---------|
| "oauth 模块有哪些文件" | `**/oauth*/**`, `**/auth/**` | 模块的完整文件树 |
| "token_refresh 的测试在哪" | `**/test*token*`, `**/token*test*` | 单元测试文件 |
| "认证服务的配置文件" | `**/auth*/**/*.yaml`, `**/auth*/**/*.toml` | 配置、部署文件 |
| "跟 oauth 相关的数据库迁移" | `**/migration*/*oauth*`, `**/alembic/**/*oauth*` | DDL 变更脚本 |

#### 2.6.2 read_file：完整文件上下文

**解决的核心问题：** `code_chunks` 表按函数/类分块存储。但以下信息不在任何函数/类内部，`code_chunks` 存不到：

- `import` 语句和依赖关系
- 模块级常量、配置变量
- `__init__.py` 的公开导出列表
- 装饰器的实现（如果定义在其他文件）
- 文件级 docstring

```python
async def read_file_context(
    seed_chunks: list[CodeChunk],
    candidate_repos: list[str],
    expand_imports: bool = True,
    max_files: int = 10
) -> list[FileContext]:
    """
    从 grep 命中的 chunk 出发，读取完整文件获取上下文。
    
    seed_chunks: grep 找到的 code_chunks
    → 读取每个 chunk 所属的完整文件
    → 可选：追踪 import 链，读取被 import 的模块
    """
    files_to_read = set()
    
    for chunk in seed_chunks:
        files_to_read.add((chunk.repo, chunk.file_path))
    
    # 去重，限制数量
    files_to_read = list(files_to_read)[:max_files]
    
    results = []
    for repo, file_path in files_to_read:
        content = await read_file(repo, file_path)
        
        file_ctx = FileContext(
            repo=repo,
            file_path=file_path,
            content=content,
            # 解析 import 链
            imports=extract_imports(content),
            # 提取模块级定义
            module_level=extract_module_level_defs(content),
            # 文件的 docstring
            docstring=extract_file_docstring(content)
        )
        results.append(file_ctx)
        
        # 追踪关键 import
        if expand_imports:
            for imp in file_ctx.imports:
                if is_internal_import(imp) and len(results) < max_files:
                    imported_ctx = await resolve_and_read(repo, imp)
                    if imported_ctx:
                        results.append(imported_ctx)
    
    return results
```

**read_file vs code_chunks 的分工：**

| | `code_chunks`（已有） | `read_file`（新增） |
|---|---|---|
| **粒度** | 单个函数/类 | 完整文件 |
| **内容** | 函数体 + docstring | import + 模块常量 + 所有函数/类 |
| **速度** | ~5ms（B-tree） | ~5ms（文件系统 read） |
| **适用** | 精确符号定位后的快速返回 | 需要完整上下文的理解型查询 |

**使用准则：** 当用户只问"token_refresh 逻辑是什么"→ 返回 code_chunks 即可。当用户问"oauth 模块的认证流程是怎么设计的"→ 需要 read_file 获取完整文件上下文。

#### 2.6.3 三工具协同的典型链路

```
用户: "oauth 的 token 刷新逻辑怎么实现的，有测试吗？"

Step 1 — glob（发现文件）: ~5ms
  patterns: **/*oauth*, **/auth/**, **/test*token*, **/token*test*
  → 发现: auth-service/src/auth/oauth.py
          auth-service/tests/test_oauth.py
          auth-service/tests/test_token_refresh.py

Step 2 — grep（精确定位）: ~10ms
  在发现的文件中 grep "token_refresh\|refresh_token\|def refresh"
  → oauth.py:42: def token_refresh(token: str) -> Token:
  → oauth.py:87: def refresh_token_pair(access, refresh):

Step 3 — read_file（理解上下文）: ~10ms
  读取 oauth.py 完整内容:
  → import 链: from jose import jwt, import redis
  → 模块常量: REFRESH_TOKEN_TTL = 3600
  → 同级函数: validate_token(), rotate_credentials()
  读取 test_oauth.py:
  → 使用示例: token_refresh("expired_token")
  → mock 行为: redis.get() 的测试替身

Step 4 — AST 调用图扩展（已有）: ~10ms
  token_refresh 的调用链 → 谁是 caller，谁是 callee

总延迟: ~35ms，返回:
  - token_refresh 源代码 + 完整文件上下文
  - 调用链上下游
  - 测试文件中的使用示例
  - 相关配置常量
```

#### 2.6.4 与纯 agentic grep 的区别

Claude Code 的 agentic 模式是**交互式的**：grep→read→理解→再 grep→再 read，需要多轮交互。每个步骤都需要 agent 思考和决策。

Code Worker 的协同模式是**流水线式的**：glob+grep+read 在一次调用中流水线执行，中间结果自动触发下一步。这适合 RAG 场景——用户期望一次返回，不要多轮交互。

```
Claude Code (交互式):         Code Worker (流水线式):
  agent: "先 glob **/oauth*"    Phase 0: 文件路径路由 (500→5)
  → 看到 20 个文件               Phase 1: ripgrep 并行搜索
  agent: "再 read oauth.py"      Phase 2: read_file + AST 扩展
  → 看到 token_refresh           一次返回完整上下文
  agent: "再 grep token_refresh" 
  → 找到更多引用
  ...多轮交互...
```

---

## 三、多仓库路由：文件路径缓存 + 实时 ripgrep

### 3.1 问题与设计决策

公司后端微服务有几百个源码仓库。用户问"用户登录的 OAuth 逻辑在哪"时，不可能对 500 个仓库逐一执行 ripgrep——即使每个仓库只需 50ms，串行也要 25 秒，并行受限于 CPU 核心数也需数秒。

**三种方案的权衡：**

| 方案 | 代表工具 | 维护成本 | 搜索速度 | 新鲜度 |
|------|---------|---------|---------|--------|
| **零索引** — 直接 ripgrep 扫所有仓库 | Claude Code（单仓库场景） | 零 | 慢（500 仓库 × 50ms） | 实时 |
| **Ngram 索引** — 建内容索引加速搜索 | Livegrep, Zoekt, Blackbird | 中：需重建索引 | 极快（ms 级） | 依赖索引更新频率 |
| **文件路径缓存** — 只索引文件路径做路由 | 本方案 | 极低：`git ls-files` | 快（路由 ~50ms + ripgrep ~200ms） | 实时（ripgrep 始终搜最新） |

**设计决策：文件路径缓存 + 实时 ripgrep。** 理由：

1. **文件路径已经包含强语义信号。** `src/auth/oauth.py` 出现在哪个仓库，那个仓库大概率跟认证相关。这是业界实践——GitHub Blackbird 的索引包含文件路径作为独立搜索维度，Claude Code 的 Glob 第一步也是按文件路径模式发现。

2. **维护成本极低。** `git ls-files` 不需要解析任何语言，比 TreeSitter AST 解析简单两个数量级。

3. **路由不需要 100% 准确。** 路由只是缩小搜索范围（500→5~10），漏掉一两个相关仓库的概率可接受，而且有兜底策略（3.5 节）。

4. **内容搜索始终走 ripgrep。** 不存在"索引过期"问题——ripgrep 搜的是实时文件系统。

### 3.2 文件路径缓存

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

**规模估算：** 500 仓库 × 平均 1000 文件/仓库 = 50 万行 × ~50 字节 = ~40MB（含索引）。对比旧版 `global_symbol_index` 的 450MB，体积缩小 90%+。

**维护方式：** Git webhook push → `git pull` → `git ls-files` → DELETE + INSERT 该仓库记录。单个 1000 文件仓库刷新 < 100ms。

### 3.3 第一步：搜索词 → 文件路径模式

这是路由准确性的关键。不是简单地把搜索词用 `%keyword%` 包起来扔进 LIKE，而是**根据搜索词的语义类型，构造不同的路径模式**。

```python
def build_path_patterns(
    search_terms: SearchTermSet,
    entities: ExtractedEntities
) -> PathPatternSet:
    """
    从搜索词构造文件路径匹配模式。
    
    核心思路：不同的搜索词类型，对应不同的文件路径特征。
    不是简单的 LIKE '%keyword%'，而是有类型的路径匹配。
    
    返回: PathPatternSet 包含三类模式，分别打分
    """
    patterns = PathPatternSet()
    
    # ===== 类型 1: 目录结构匹配（权重 1.0）=====
    # 搜索词是业务模块名 → 匹配顶层目录
    # "auth" → ** /auth/**, ** /auth_*/**, ** /oauth/**
    for term in search_terms.exact_terms + search_terms.fuzzy_terms:
        patterns.add_directory(term)
    
    # ===== 类型 2: 文件名匹配（权重 0.9）=====
    # 搜索词是具体文件名 → 匹配文件名部分
    # "oauth" → ** /oauth.py, ** /oauth_*.py, ** /*oauth*.py
    for term in search_terms.exact_terms:
        patterns.add_filename(term)
    
    # ===== 类型 3: 业务域路径模板（权重 0.8）=====
    # 从 module 实体推断的路径模板
    # "用户登录" → auth/, login/, sso/, oauth/
    if entities.module:
        patterns.add_domain_templates(entities.module)
    
    # ===== 类型 4: 工程约定路径（权重 0.5）=====
    # 通用工程约定：测试、配置、迁移文件
    for term in search_terms.fuzzy_terms[:3]:
        patterns.add_convention(term)
    
    return patterns


class PathPatternSet:
    """分类型的文件路径匹配模式集"""
    
    def __init__(self):
        self.directory_patterns: list[tuple[str, float]] = []  # (pattern, weight)
        self.filename_patterns: list[tuple[str, float]] = []
        self.domain_patterns: list[tuple[str, float]] = []
        self.convention_patterns: list[tuple[str, float]] = []
    
    def add_directory(self, term: str):
        """匹配目录结构。如 auth → ** /auth/**, ** /auth_*/** """
        self.directory_patterns.extend([
            (f"%/{term}/%", 1.0),           # src/auth/
            (f"%/{term}_%/%", 0.7),         # src/auth_service/
            (f"%/{term}-%/%", 0.7),         # src/auth-gateway/
        ])
    
    def add_filename(self, term: str):
        """匹配文件名。如 oauth → ** /oauth.py, ** /oauth_*.py """
        self.filename_patterns.extend([
            (f"%/{term}.%", 0.9),            # src/oauth.py
            (f"%/{term}_%", 0.8),            # src/oauth_token.py
            (f"%/{term}-%", 0.7),            # src/oauth-token.py
            (f"%{term}%", 0.5),              # 兜底：路径中任意位置
        ])
    
    def add_domain_templates(self, module: str):
        """从业务域映射到路径模板。如 用户登录 → [auth, login, sso, oauth] """
        # 用同义词映射表把中文 module 展开为英文关键词
        domain_keywords = lookup_code_terms(module)  # 来自 2.4 节 CODE_TERM_MAP
        for kw in domain_keywords[:5]:
            self.domain_patterns.append((f"%/{kw}/%", 0.8))
            self.domain_patterns.append((f"%/{kw}_%", 0.6))
    
    def add_convention(self, term: str):
        """匹配工程约定路径。如 term=oauth → test_oauth, oauth_config 等"""
        self.convention_patterns.extend([
            (f"%/test%{term}%", 0.5),         # tests/test_oauth.py
            (f"%/{term}%test%", 0.5),         # src/oauth_test.py
            (f"%/{term}%config%", 0.4),       # config/oauth_config.yaml
            (f"%/{term}%migration%", 0.3),    # migrations/2024_oauth_migration.py
        ])
    
    def all_patterns(self) -> list[tuple[str, float]]:
        """合并所有模式，去重（保留最高权重）"""
        all_pats = (
            self.directory_patterns +
            self.filename_patterns +
            self.domain_patterns +
            self.convention_patterns
        )
        # 去重：同一个 pattern 保留最高权重
        seen = {}
        for pat, weight in all_pats:
            if pat not in seen or weight > seen[pat]:
                seen[pat] = weight
        return list(seen.items())
```

**为什么分类型：** 不是所有 `LIKE '%token%'` 命中都有同等价值。`src/auth/token_refresh.py`（目录+文件名同时命中）比 `README.md` 中恰好包含 "token" 这个字符串有意义得多。分类型打分让目录结构命中的权重大于随机子串命中。

### 3.4 第二步：SQL 查询 + 加权打分

```python
async def query_file_path_cache(
    patterns: PathPatternSet,
    min_match: int = 1,
    top_k: int = 15
) -> list[tuple[str, float, int]]:
    """
    执行文件路径缓存查询，返回候选仓库及其得分。
    
    延迟目标: < 30ms（单次 SQL，50 万行表）
    """
    # 构建带权重的 UNION ALL 查询
    # 每种模式类型对应一个 SELECT，按权重标记 match_type
    union_parts = []
    params = []
    param_idx = 0
    
    for pat, weight in patterns.directory_patterns:
        param_idx += 1
        union_parts.append(f"""
            SELECT repo_name, file_path, {weight} AS weight, 'dir' AS match_type
            FROM file_path_cache
            WHERE file_path LIKE ${param_idx}
        """)
        params.append(pat)
    
    for pat, weight in patterns.filename_patterns:
        param_idx += 1
        union_parts.append(f"""
            SELECT repo_name, file_path, {weight} AS weight, 'file' AS match_type
            FROM file_path_cache
            WHERE file_path LIKE ${param_idx}
        """)
        params.append(pat)
    
    for pat, weight in patterns.domain_patterns:
        param_idx += 1
        union_parts.append(f"""
            SELECT repo_name, file_path, {weight} AS weight, 'domain' AS match_type
            FROM file_path_cache
            WHERE file_path LIKE ${param_idx}
        """)
        params.append(pat)
    
    for pat, weight in patterns.convention_patterns:
        param_idx += 1
        union_parts.append(f"""
            SELECT repo_name, file_path, {weight} AS weight, 'conv' AS match_type
            FROM file_path_cache
            WHERE file_path LIKE ${param_idx}
        """)
        params.append(pat)
    
    if not union_parts:
        return []
    
    query = f"""
    WITH matched AS (
        {' UNION ALL '.join(union_parts)}
    ),
    repo_stats AS (
        SELECT 
            repo_name,
            -- 加权求和：每个匹配项按权重贡献分数
            SUM(weight) AS weighted_score,
            -- 匹配到的唯一文件数（去重：同一个文件可能被多个模式匹配）
            COUNT(DISTINCT file_path) AS unique_files,
            -- 匹配类型多样性（同时命中目录+文件名 > 仅命中一种类型）
            COUNT(DISTINCT match_type) AS type_diversity,
            -- 最高单次权重
            MAX(weight) AS top_weight
        FROM matched
        GROUP BY repo_name
        HAVING COUNT(DISTINCT file_path) >= {min_match}
    )
    SELECT 
        repo_name,
        weighted_score,
        unique_files,
        type_diversity,
        top_weight,
        -- 最终得分 = SUM(weight) × 类型多样性加成
        weighted_score * (1.0 + (type_diversity - 1) * 0.3) AS final_score
    FROM repo_stats
    ORDER BY final_score DESC
    LIMIT {top_k}
    """
    
    rows = await db.fetch(query, *params)
    
    return [(row['repo_name'], row['final_score'], row['unique_files']) 
            for row in rows]
```

**打分公式的设计原理：**

```
final_score = SUM(match_weight) × (1 + (type_diversity - 1) × 0.3)

其中:
- SUM(match_weight): 所有匹配项的权重之和
  - 目录命中（1.0）> 文件名命中（0.9）> 域模板（0.8）> 工程约定（0.5）
- type_diversity: 匹配了多少种类型（1-4）
  - 2 种类型 → 1.3× 加成；3 种 → 1.6×；4 种 → 1.9×
  - 原理：同时命中目录+文件名+域模板 > 只在一种类型中命中很多次
```

**为什么不用简单的 COUNT：**

| 场景 | COUNT 结果 | 加权 SUM 结果 | 谁更合理 |
|------|-----------|-------------|---------|
| auth-service: `src/auth/`下有 200 个文件 | 200（最高） | 200 × 1.0 = 200 | COUNT 可以 |
| gateway: README.md 提到 "auth" 10 次 + `src/auth/`下有 5 个文件 | 15 | 5×1.0 + 10×0.3 = 8 | **加权更合理**——README 中的 auth 不应和目录名同等对待 |
| 小服务: `src/login/handler.py` 是唯一匹配 | 1 | 1.0 + 多样性 = 1.0 | **加权更合理**——虽然只有 1 个文件但目录+文件名同时命中 |

### 3.5 第三步：候选仓库截断

Phase 0 得到一批候选仓库和分数后，需要决定取多少个进入 Phase 1（ripgrep 搜索）。这个决策直接影响延迟和召回率：

```python
def trim_candidates(
    scored_repos: list[tuple[str, float, int]],
    max_repos: int = 8,
    min_repos: int = 1,
    score_drop_threshold: float = 0.6  # 分数断崖阈值
) -> list[str]:
    """
    从候选仓库列表中截断，决定进入 Phase 1 的仓库。
    
    三个截断规则，优先级从高到低：
    """
    if not scored_repos:
        return []
    
    repos = [(name, score, count) for name, score, count in scored_repos]
    top_score = repos[0][1]
    
    # ===== 规则 1: 分数断崖检测 =====
    # 如果相邻仓库分数差距 > 60%，在断崖处截断
    # 例: [0.95, 0.92, 0.31, 0.28] → gap = (0.92-0.31)/0.92 = 66% → 截在 0.92 之后
    for i in range(1, len(repos)):
        prev_score = repos[i-1][1]
        curr_score = repos[i][1]
        if prev_score > 0 and (prev_score - curr_score) / prev_score > score_drop_threshold:
            cut_at = max(i, min_repos)
            return [r[0] for r in repos[:cut_at]]
    
    # ===== 规则 2: 分数绝对值阈值 =====
    # 分数太低的不值得搜（路径匹配信号太弱）
    qualified = [r for r in repos 
                 if r[1] >= top_score * 0.15  # 不低于最高分的 15%
                 and r[2] >= 2]                # 至少匹配 2 个文件
    
    # ===== 规则 3: 上限截断 =====
    return [r[0] for r in qualified[:max_repos]]
```

**实际效果示例：**

```
场景 A — 精确查找 "oauth.py 的 token_refresh"：
  auth-service: score=240, files=200  ← 断崖前唯一候选
  gateway:      score=3,   files=2    ← score_drop = 99% > 60% → 截断
  → Phase 1 只搜 auth-service（节省 4 次无效 ripgrep）

场景 B — 模糊查找 "用户登录"：
  auth-service:    score=180, files=150
  sso-service:     score=165, files=120  ← gap = 8%
  user-service:    score=140, files=95   ← gap = 15%
  auth-gateway:    score=130, files=80   ← gap = 7%
  → 分数紧密，全部进入 Phase 1（都是潜在相关的认证服务）
```

### 3.6 第四步：三层兜底

文件路径路由能覆盖约 80% 的查询。剩下 20% 是那些"文件路径不包含搜索词"的场景——比如认证逻辑全部在 `src/core/handler.py` 中，路径中没有任何 `auth`/`login`/`oauth` 关键词。

```python
async def route_repos(
    search_terms: SearchTermSet,
    entities: ExtractedEntities,
    min_candidates: int = 3
) -> RouteResult:
    """
    完整的三层递进路由。每一层独立执行，上一层候选够了就停止。
    """
    # ===== Layer 1: 文件路径缓存匹配（~30ms，覆盖 ~80%）=====
    patterns = build_path_patterns(search_terms, entities)
    candidates = await query_file_path_cache(patterns, min_match=1, top_k=15)
    trimmed = trim_candidates(candidates)
    
    if len(trimmed) >= min_candidates:
        return RouteResult(
            repos=trimmed,
            method="file_path_cache",
            confidence=Confidence.HIGH if len(trimmed) >= 3 else Confidence.MEDIUM
        )
    
    # ===== Layer 2: 仓库注册表匹配（~5ms，额外覆盖 ~15%）=====
    # repo_registry 表存储每个仓库的名称、描述、标签
    # 例: auth-service → tags=["认证", "OAuth", "SSO", "用户管理"]
    registry_candidates = await query_repo_registry(
        keywords=search_terms.all_terms(),
        module=entities.module,
        exclude=trimmed  # 排除 Layer 1 已找到的
    )
    
    merged = trimmed + [
        r.name for r in registry_candidates 
        if r.match_score > 0.6
    ]
    
    if len(merged) >= min_candidates:
        return RouteResult(
            repos=list(dict.fromkeys(merged))[:8],  # 去重保序
            method="file_path_cache+repo_registry",
            confidence=Confidence.MEDIUM
        )
    
    # ===== Layer 3: 全量顶层目录扫描（~100ms，覆盖 ~5%）=====
    # 所有其他层都失败 → 扫描每个仓库的顶层目录结构
    # 一个仓库的顶层目录通常只有 10-30 个，500 个仓库 = 5000-15000 条目
    # 比对文件路径缓存全量扫描（50 万行）小 30-100 倍
    top_dirs = await scan_all_repo_top_dirs()
    dir_candidates = fuzzy_match_dirs(
        top_dirs, 
        [t for t in search_terms.all_terms() if len(t) >= 3],
        top_k=10
    )
    
    merged = merged + [d.repo_name for d in dir_candidates]
    
    return RouteResult(
        repos=list(dict.fromkeys(merged))[:8],
        method="full_top_dir_scan",
        confidence=Confidence.LOW
    )


# ===== Layer 2 实现：仓库注册表 =====

async def query_repo_registry(
    keywords: list[str],
    module: str | None,
    exclude: list[str] = []
) -> list[RepoMatch]:
    """
    用关键词和模块匹配仓库注册表。
    
    repo_registry 是一张极小的表（~500 行），存储每个仓库的：
    - repo_name: "auth-service"
    - display_name: "用户认证服务"
    - description: "处理用户登录、OAuth、SSO、权限管理"
    - tags: ["认证", "OAuth", "登录", "SSO"]
    """
    kw_conditions = []
    params = []
    for kw in keywords[:5]:
        # 中文关键词匹配 display_name 和 description
        kw_conditions.append("display_name ILIKE %s")
        params.append(f"%{kw}%")
        kw_conditions.append("description ILIKE %s")
        params.append(f"%{kw}%")
        # 英文关键词匹配 tags
        kw_conditions.append("%s = ANY(tags)")
        params.append(kw)
    
    if not kw_conditions:
        return []
    
    exclude_clause = ""
    if exclude:
        exclude_clause = f"AND repo_name NOT IN ({','.join(['%s']*len(exclude))})"
        params.extend(exclude)
    
    query = f"""
        SELECT repo_name, 
               -- 简单相关性: 匹配条件数
               ({" + ".join([f'(CASE WHEN {c} THEN 1 ELSE 0 END)' for c in kw_conditions])})::float 
               / {len(kw_conditions)} AS match_score
        FROM repo_registry
        WHERE ({" OR ".join(kw_conditions)})
        {exclude_clause}
        ORDER BY match_score DESC
        LIMIT 10
    """
    
    # ... 执行查询


# ===== Layer 3 实现：全量顶层目录扫描 =====

async def scan_all_repo_top_dirs() -> list[DirEntry]:
    """
    扫描所有仓库的顶层目录结构。
    
    比扫描全部文件路径（file_path_cache 50万行）小 30-100 倍。
    只有前两层都失败时才触发。
    
    缓存策略：顶层目录结构很少变，可以缓存 1 小时。
    """
    if cached := TOP_DIR_CACHE.get():
        return cached
    
    query = """
        SELECT repo_name, 
               SPLIT_PART(file_path, '/', 1) || '/' || SPLIT_PART(file_path, '/', 2) AS top_dir,
               COUNT(*) AS file_count
        FROM file_path_cache
        WHERE file_path NOT LIKE '.git/%'
          AND file_path NOT LIKE 'node_modules/%'
          AND file_path NOT LIKE '__pycache__/%'
        GROUP BY repo_name, 
                 SPLIT_PART(file_path, '/', 1) || '/' || SPLIT_PART(file_path, '/', 2)
    """
    rows = await db.fetch(query)
    result = [DirEntry(row['repo_name'], row['top_dir'], row['file_count']) 
              for row in rows]
    
    TOP_DIR_CACHE.set(result, ttl=3600)
    return result


def fuzzy_match_dirs(
    dirs: list[DirEntry],
    keywords: list[str],
    top_k: int = 10
) -> list[DirEntry]:
    """
    模糊匹配顶层目录。用编辑距离和子串匹配。
    """
    scored = []
    for d in dirs:
        score = 0.0
        for kw in keywords:
            if kw.lower() in d.top_dir.lower():
                score += 1.0  # 子串匹配
            elif levenshtein_ratio(kw.lower(), d.top_dir.lower()) > 0.7:
                score += 0.5  # 编辑距离相近
        if score > 0:
            scored.append((d, score))
    
    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:top_k]]
```

**三层兜底的覆盖率与延迟：**

```
Layer 1: 文件路径缓存（80% 查询，~30ms）
  └─ 失败时 ↓

Layer 2: 仓库注册表（额外 15%，~5ms）
  └─ 失败时 ↓
  
Layer 3: 顶层目录扫描（额外 4%，~100ms）
  └─ 失败时 ↓
  
全量兜底: 反问用户或扩大至 50 仓库 ripgrep（< 1%，~2-3s）
```

### 3.7 与搜索词构造管线的集成

文件路径路由的输入来自 [第六章搜索词构造管线](#六搜索词构造管线从用户问题到代码标识符)。两个模块的接口：

```python
# 第六章产出 SearchTermSet
search_terms = await build_search_terms(query, entities)
# SearchTermSet 包含:
#   exact_terms:   ["token_refresh"]        → 文件名精确匹配
#   fuzzy_terms:   ["oauth", "auth"]        → 目录模糊匹配
#   tag_terms:     ["认证", "OAuth"]         → 域模板匹配

# 第三章消费 SearchTermSet，额外补充 entities.module
patterns = build_path_patterns(search_terms, entities)
# entities.module = "用户登录" → domain_keywords = ["auth", "login", "sso", "oauth"]
```

**关键设计原则：** 搜索词构造管线不需要理解文件路径路由的实现。它只负责产出搜索词。路由模块自己决定如何把这些词映射为路径模式。两个模块通过 `SearchTermSet` 解耦。

### 3.8 完整执行流程模拟

以下用三个真实问题走完整条管线——从用户输入到 Phase 1 候选仓库列表。假设环境为 500 个微服务仓库的生产集群。

#### 模拟 A：精确引用 — "oauth.py 的 token_refresh 函数逻辑是什么"

```
┌─ Step 0: Supervisor 实体抽取 ─────────────────────────────┐
│ entities.code_refs = ["oauth.py", "token_refresh"]         │
│ entities.module = None                                     │
│ query_type = EXACT_REFS                                    │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 1: 搜索词构造（第六章） ─────────────────────────────┐
│ exact_terms:  ["token_refresh", "oauth.py"]                │
│ fuzzy_terms:  ["token", "refresh", "oauth"]  (形态扩展)    │
│ tag_terms:    []                                           │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 2: 文件路径模式构造（3.3） ──────────────────────────┐
│ 目录结构 (1.0):  %/oauth/%, %/token/%, %/refresh/%        │
│ 文件名   (0.9):  %/oauth.%, %/token_refresh%              │
│ 域模板   (0.8):  (module 为空，跳过)                       │
│ 工程约定 (0.5):  %/test%oauth%, %/test%token_refresh%    │
│                                                            │
│ 共生成 12 个 LIKE 模式                                     │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 3: SQL 查询（3.4） ─────────────────────────────────┐
│ SELECT repo_name, SUM(weight) * (1+(types-1)*0.3) AS score│
│ FROM file_path_cache JOIN patterns                         │
│ GROUP BY repo_name ORDER BY score DESC LIMIT 15            │
│                                                            │
│ 实际执行结果:                                              │
│ ┌──────────────────┬───────┬───────┬────────┬───────────┐ │
│ │ repo_name        │ score │ files │ types  │ top_match │ │
│ ├──────────────────┼───────┼───────┼────────┼───────────┤ │
│ │ auth-service     │ 387.2 │   312 │   4    │ dir(1.0)  │ │
│ │ auth-gateway     │  12.4 │     9 │   2    │ file(0.9) │ │
│ │ sso-service      │   3.1 │     3 │   1    │ dir(1.0)  │ │
│ │ user-service     │   1.8 │     2 │   1    │ conv(0.5) │ │
│ │ monitor-service  │   0.5 │     1 │   1    │ conv(0.5) │ │
│ └──────────────────┴───────┴───────┴────────┴───────────┘ │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 4: 候选截断（3.5） ─────────────────────────────────┐
│ 断崖检测:                                                  │
│   auth-service(387.2) → auth-gateway(12.4)                │
│   gap_ratio = (387.2-12.4)/387.2 = 96.8% > 60% → 截断!   │
│                                                            │
│ 结果: 只保留 auth-service                                 │
│ confidence = HIGH（断崖极深 + 精确引用）                   │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 5: 输出 ────────────────────────────────────────────┐
│ Phase 1 候选仓库: ["auth-service"]                         │
│ Phase 1 ripgrep 命令:                                     │
│   rg --json 'token_refresh|oauth' -g '*.py'              │
│      /repos/auth-service/                                 │
│                                                            │
│ 路由方法: file_path_cache                                  │
│ 置信度: HIGH                                               │
│ 总延迟: ~35ms (模式构造 2ms + SQL 28ms + 截断 1ms)        │
└────────────────────────────────────────────────────────────┘
```

#### 模拟 B：中英混合 — "用户登录的 OAuth token 刷新逻辑在哪"

```
┌─ Step 0: Supervisor 实体抽取 ─────────────────────────────┐
│ entities.code_refs = []                                    │
│ entities.module = "用户登录"                               │
│ query_type = MIXED_CN_EN                                   │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 1: 搜索词构造（第六章） ─────────────────────────────┐
│ 英文直提:    "OAuth"(0.95), "token"(0.95)                 │
│ 中文→同义词: "用户登录"→login,auth,authenticate,oauth,sso │
│ 中文→同义词: "刷新"→refresh,renew,rotate                  │
│ 共现加分:    "OAuth"+"token"→refresh_token(+0.3)          │
│                                                            │
│ exact_terms:  []                                           │
│ fuzzy_terms:  ["OAuth","token","login","auth",            │
│                "authenticate","refresh","sso"]             │
│ tag_terms:    ["认证","OAuth","Token"]                     │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 2: 文件路径模式构造（3.3） ──────────────────────────┐
│ 目录结构 (1.0):  /oauth/, /auth/, /login/, /token/,       │
│                  /sso/, /authenticate/, /refresh/          │
│ 文件名   (0.9):  /oauth%, /auth%, /login%, /token%        │
│ 域模板   (0.8):  module="用户登录"→auth,login,sso,oauth   │
│ 工程约定 (0.5):  test*oauth*, test*auth*, oauth*config*   │
│                  oauth*migration*                          │
│                                                            │
│ 共生成 28 个 LIKE 模式                                     │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 3: SQL 查询（3.4） ─────────────────────────────────┐
│ 28 个 LIKE 条件，UNION ALL + GROUP BY                      │
│ pg_trgm GIN 索引加速，每个 LIKE 走 Bitmap Index Scan      │
│                                                            │
│ 实际执行结果:                                              │
│ ┌──────────────────┬───────┬───────┬────────┬───────────┐ │
│ │ repo_name        │ score │ files │ types  │ top_match │ │
│ ├──────────────────┼───────┼───────┼────────┼───────────┤ │
│ │ auth-service     │ 452.8 │   385 │   4    │ dir(1.0)  │ │
│ │ auth-gateway     │ 218.6 │   172 │   3    │ dir(1.0)  │ │
│ │ sso-service      │ 185.3 │   140 │   3    │ dir(1.0)  │ │
│ │ user-service     │ 162.4 │   118 │   3    │ dir(1.0)  │ │
│ │ login-guard      │  58.2 │    45 │   2    │ file(0.9) │ │
│ │ session-service  │  42.1 │    32 │   2    │ file(0.9) │ │
│ │ captcha-service  │  15.3 │    12 │   1    │ conv(0.5) │ │
│ └──────────────────┴───────┴───────┴────────┴───────────┘ │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 4: 候选截断（3.5） ─────────────────────────────────┐
│ 断崖检测:                                                  │
│   auth-service(452.8) → auth-gateway(218.6) gap=52%       │
│   auth-gateway(218.6) → sso-service(185.3) gap=15%        │
│   sso-service(185.3) → user-service(162.4) gap=12%        │
│   user-service(162.4) → login-guard(58.2) gap=64% → 截断! │
│                                                            │
│ 分数绝对值: 全部 > 15% × 452.8 = 67.9?                    │
│   login-guard: 58.2 < 67.9 → 额外排除                     │
│                                                            │
│ 结果: [auth-service, auth-gateway, sso-service,           │
│        user-service] 共 4 个仓库                           │
│ confidence = HIGH（分数聚集 + 4 种匹配类型）               │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 5: 输出 ────────────────────────────────────────────┐
│ Phase 1 候选仓库:                                          │
│   ["auth-service","auth-gateway","sso-service","user-service"]
│ Phase 1 ripgrep 命令 (并行 4 路):                          │
│   rg --json 'OAuth|token|login|auth|refresh' \            │
│      -g '*.py' -g '*.java' /repos/auth-service/           │
│   rg --json 'OAuth|token|login|auth|refresh' \            │
│      -g '*.py' -g '*.java' /repos/auth-gateway/           │
│   ... (sso-service, user-service)                         │
│                                                            │
│ 路由方法: file_path_cache                                  │
│ 置信度: HIGH                                               │
│ 总延迟: ~32ms (模式 3ms + SQL 27ms + 截断 1ms)            │
└────────────────────────────────────────────────────────────┘
```

#### 模拟 C：纯中文 + 路由失败 — "额度校验的逻辑在哪"

```
┌─ Step 0: Supervisor 实体抽取 ─────────────────────────────┐
│ entities.code_refs = []                                    │
│ entities.module = "额度校验"                               │
│ query_type = PURE_CN                                       │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 1: 搜索词构造（第六章） ─────────────────────────────┐
│ 第一层-同义词表: "额度校验"→未覆盖                          │
│ 第二层-模块→符号: "额度校验"→未覆盖                         │
│ 第三层-LLM翻译:  "额度校验"→["credit_limit","quota_check", │
│                  "limit_validation","amount_verify"]        │
│                  (缓存未命中，首次耗时 ~300ms)              │
│                                                            │
│ exact_terms:  []                                           │
│ fuzzy_terms:  ["credit_limit","quota_check",              │
│                "limit_validation","amount_verify"]         │
│ tag_terms:    []                                           │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 2: 文件路径模式构造（3.3） ──────────────────────────┐
│ 目录结构:  /credit/, /quota/, /limit/, /amount/           │
│ 文件名:    /credit_limit%, /quota_check%,                  │
│            /limit_validation%, /amount_verify%             │
│ 域模板:    module="额度校验"→未在映射表中                   │
│ 工程约定:  test*credit_limit*, test*quota_check*           │
│                                                            │
│ 共生成 16 个 LIKE 模式                                     │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 3: SQL 查询（3.4） ─────────────────────────────────┐
│ 实际执行结果:                                              │
│ ┌──────────────────┬───────┬───────┬────────┬───────────┐ │
│ │ repo_name        │ score │ files │ types  │ top_match │ │
│ ├──────────────────┼───────┼───────┼────────┼───────────┤ │
│ │ credit-service   │   8.4 │     7 │   2    │ dir(1.0)  │ │
│ │ risk-service     │   2.1 │     2 │   1    │ conv(0.5) │ │
│ └──────────────────┴───────┴───────┴────────┴───────────┘ │
│                                                            │
│ ⚠ 只有 2 个候选，且分数极低                                │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 4: 候选截断（3.5） ─────────────────────────────────┐
│ candidates = ["credit-service", "risk-service"]            │
│ len < min_candidates(3) → 触发 Layer 2                     │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 5: Layer 2 仓库注册表兜底（3.6） ───────────────────┐
│ 查询 repo_registry:                                        │
│   WHERE display_name ILIKE '%额度%'                       │
│      OR description ILIKE '%额度%'                         │
│      OR 'credit_limit' = ANY(tags)                        │
│      OR '额度校验' = ANY(tags)                             │
│                                                            │
│ 结果:                                                      │
│   billing-service (score=0.85) — tags:["额度","计费"]     │
│   credit-service  (score=0.90) — 已在 Layer 1 中         │
│   order-service   (score=0.72) — desc:"...订单额度校验..." │
│                                                            │
│ 合并: [credit-service, risk-service,                       │
│        billing-service, order-service]                     │
│ len=4 ≥ min_candidates(3) → 返回                           │
└────────────────────────────────────────────────────────────┘
    │
    ▼
┌─ Step 6: 输出 ────────────────────────────────────────────┐
│ Phase 1 候选仓库:                                          │
│   ["credit-service","billing-service","order-service"]     │
│   (risk-service 分数太低被排除)                            │
│ Phase 1 ripgrep 命令:                                      │
│   rg --json 'credit_limit|quota_check|limit_validation' \ │
│      -g '*.py' -g '*.java' \                              │
│      /repos/credit-service/ /repos/billing-service/ ...    │
│                                                            │
│ 路由方法: file_path_cache + repo_registry (Layer 1→2 兜底)│
│ 置信度: MEDIUM                                             │
│ 总延迟: ~335ms (搜索词构造 300ms LLM翻译 + 路由 35ms)     │
│ 注: LLM 翻译结果缓存后，下次命中 ~35ms                     │
└────────────────────────────────────────────────────────────┘
```

#### 三个模拟的对比总结

```
┌────────────┬──────────────┬──────────────┬──────────────┐
│            │ 模拟 A       │ 模拟 B       │ 模拟 C       │
│            │ 精确引用     │ 中英混合     │ 纯中文+兜底  │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 搜索词构造 │ < 5ms        │ < 10ms       │ ~300ms(首次) │
│            │ (直接提取)   │ (同义词表)   │ /5ms(缓存)   │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 路径模式数 │ 12           │ 28           │ 16           │
├────────────┼──────────────┼──────────────┼──────────────┤
│ SQL 耗时   │ ~28ms        │ ~27ms        │ ~25ms        │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 候选仓库数 │ 1 (截断)     │ 4 (截断)     │ 3 (Layer1+2) │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 路由层     │ Layer 1      │ Layer 1      │ Layer 1→2    │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 置信度     │ HIGH         │ HIGH         │ MEDIUM       │
├────────────┼──────────────┼──────────────┼──────────────┤
│ 总路由延迟 │ ~35ms        │ ~32ms        │ ~335ms/35ms  │
│ Phase 1    │ 1×rg(~50ms)  │ 4×rg(~80ms)  │ 3×rg(~70ms) │
│ 端到端延迟 │ ~150ms       │ ~200ms       │ ~400ms/150ms │
└────────────┴──────────────┴──────────────┴──────────────┘
```

**关键观察：**
- 模拟 A：精确引用时断崖极深（96.8% gap），自适应截断省掉 4 次无效 ripgrep
- 模拟 B：中英混合场景，同义词表覆盖全部翻译，零 LLM 调用
- 模拟 C：同义词表和模块→符号都未覆盖 → LLM 翻译是唯一兜底 → 首次 300ms，但结果缓存后第二次仅 5ms
- 所有场景的 SQL 查询都在 30ms 以内——50 万行对 PostgreSQL 完全没有压力

### 3.9 与全量 grep 的对比

```
场景: "OAuth token 刷新逻辑在哪"
候选仓库: 5 个（从文件路径路由得出）

┌─────────────────────────┬──────────────────────┬──────────────────────┐
│                         │ 全量 grep             │ 文件路径路由 + ripgrep│
├─────────────────────────┼──────────────────────┼──────────────────────┤
│ 涉及仓库数              │ 500（全部）           │ 5（文件路径路由）    │
│ 索引                    │ 无                    │ 文件路径缓存（25MB）  │
│ 索引维护                │ 无                    │ git ls-files（~100ms）│
│ IO 操作                 │ 500 次               │ 5 次（并行）         │
│ 实际延迟（估算）        │ 300-800ms             │ ~200ms               │
│ 内容新鲜度              │ 实时                  │ 实时（ripgrep）      │
│ 新仓库支持              │ 无需配置              │ git ls-files 自动发现│
└─────────────────────────┴──────────────────────┴──────────────────────┘
```

**核心洞察：** 索引的必要性不在于"要不要索引"，而在于"索引什么"。索引文件路径（25MB，零解析）比索引代码内容（300MB+，需 AST 解析）简单 10 倍，却能解决 80% 的路由问题。真正的代码内容搜索，交给 ripgrep。

### 3.10 未来升级路径：可选 Ngram 索引

如果文件路径路由 + ripgrep 的延迟无法满足需求（例如 ripgrep 在超大仓库上超过 500ms），可以引入**可选的 Ngram 索引**作为加速层——注意这是优化，不是替代：

```
当前:  文件路径路由 → ripgrep（候选仓库）
未来:  文件路径路由 → Ngram 索引快速定位文件 → ripgrep 精确匹配（候选文件）
```

Ngram 索引的方案参考 Livegrep/Zoekt：
- 对文件内容提取 trigram（3 字符序列）
- 语言无关，不需要解析
- 索引体积 ~3-5× 源代码大小
- 定期重建（非实时），ripgrep 用于最新未索引内容

这不是当前阶段需要的——只有在性能基准测试证明 ripgrep 是瓶颈时才引入。

---

## 四、AST 调用图存储（只做上下文扩展，不做搜索索引）

### 4.1 定位变化

Code Worker 的**搜索主引擎是 ripgrep**（实时文件系统），不需要内容索引。但 AST 调用图仍然有价值——它回答的不是"代码在哪"而是"谁调用了它、它调用了谁"。

因此 `code_chunks` 的角色从"搜索索引 + 源代码存储"降级为**"AST 调用图元数据"**：

| | code_chunks（旧版） | code_chunks（新版） |
|---|---|---|
| **存储内容** | 完整源代码 | 不存源代码（ripgrep + read_file 实时获取） |
| **存储元数据** | 函数名/类名 + 调用图 | 只存调用图（calls, called_by, imports） |
| **用于搜索** | 是（B-tree 索引搜函数名） | 否（ripgrep 搜实时文件） |
| **用于上下文扩展** | 是 | 是（核心用途） |
| **更新方式** | Git webhook → TreeSitter 重新解析 | Git webhook → TreeSitter 解析调用图部分 |

### 4.2 数据库 Schema（简化版）

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
- ripgrep 能找到代码位置（文件路径 + 行号）
- read_file 能读取完整源代码（包括 import、装饰器、模块常量——比 chunk 更完整）
- 存源代码的维护成本（每次 commit 需重新提取和存储函数体）远高于"用时再读"

### 4.3 关于调用图（calls / called_by）

`calls` 和 `called_by` 来自 TreeSitter AST 分析。这两个字段用于**上下文扩展**——ripgrep 找到 `token_refresh` 后，顺藤摸瓜找到它的调用链，回答"谁调用了它"和"它影响了谁"。见 [2.5 节](#25-ast-调用图扩展)。

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

**第二层：模块→符号关联（从文件路径缓存 + 代码采样中自动统计）**

```python
# 从文件路径缓存中推导：哪些仓库属于哪些业务模块
# 再对这些仓库执行一次轻量 ripgrep 采样，提取最高频的代码符号
# 定时任务每周刷新一次（比旧方案每小时刷新的 global_symbol_index 简单得多）

MODULE_SYMBOL_STATS = {
    "用户登录": [
        ("login", 0.95, 12),       # (符号, 归一化权重, 出现仓库数)
        ("authenticate", 0.90, 8),
        ("auth", 0.85, 45),
        ("oauth", 0.82, 6),
    ],
    # ... 冷启动时人工种子，运行时自动更新
}

def lookup_module_code_symbols(module: str, top_k: int = 10) -> list[Term]:
    stats = MODULE_SYMBOL_STATS.get(module, [])
    return [Term(sym, weight=w * 0.7, source="module_symbol_stats")
            for sym, w, repo_count in stats[:top_k]
            if repo_count < 200]
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
) -> SearchTermSet:
    """
    综合打分，输出分组的搜索词集。
    
    打分因子：
    1. 来源可信度（用户给的 > 同义词表 > LLM 翻译）
    2. 符号特异性（长符号名更具体）
    3. 与 query_type 的匹配度
    """
    
    for term in terms:
        score = term.base_weight  # 0.3 - 1.0
        
        # 因子 1: 符号长度（替代了旧版 IDF，更简单且同样有效）
        if len(term.text) <= 2:
            score *= 0.2  # "id", "no" 极短词区分度低
        elif len(term.text) >= 8:
            score *= 1.15  # 长符号名更具体
        
        # 因子 2: 是否为已知热点符号（高频出现在 commit log 中）
        if term.text in HOTSPOT_SYMBOLS:
            score *= 1.1
        
        term.final_score = score
    
    terms.sort(key=lambda t: t.final_score, reverse=True)
    terms = remove_substring_duplicates(terms)
    top = terms[:10]
    
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

### 6.7.2 翻译结果的存在性验证

LLM 翻译的标识符可能"合理但不存在"——LLM 不知道代码库的实际命名。例如用户问"削峰填谷"，LLM 翻译为 `peak_shaving`，但实际代码用的是 `traffic_smoothing`。

**方案：翻译后做一次极轻量的 ripgrep 存在性预检。**

不是完整搜索——只统计匹配文件数（`rg -c --max-count 1`），不返回内容。10 个词并行检查，延迟预算 ~50ms。

```python
async def validate_translation_existence(
    terms: list[Term],
    candidate_repos: list[str],
    timeout_ms: int = 50
) -> list[Term]:
    """
    对翻译结果做轻量 ripgrep 存在性检查。
    
    rg -c --max-count 1 只输出匹配计数，不返回内容，IO 最小。
    延迟预算：10 个词 × 并行 × 50ms = ~50ms 总延迟。
    """
    
    async def check_term(term: Term) -> Term:
        pattern = term.text
        cmd = f"rg -c --max-count 1 '{pattern}' {' '.join(candidate_repos)}"
        
        try:
            result = await run_ripgrep(cmd, timeout_ms=timeout_ms)
            match_count = sum(1 for line in result.stdout.splitlines() if line.strip())
            
            if match_count > 0:
                term.weight *= 1.05       # 确认存在，微升权重
                term.existence_verified = True
                term.match_file_count = match_count
            else:
                term.weight *= 0.15       # 不存在，大幅降权
                term.existence_verified = False
                term.match_file_count = 0
        except TimeoutError:
            term.existence_verified = None  # 超时，保持原权重
        
        return term
    
    verified = await asyncio.gather(*[check_term(t) for t in terms])
    verified.sort(key=lambda t: t.weight, reverse=True)
    return verified
```

**全部翻译词都不存在时的重翻译闭环：**

```python
async def llm_translate_with_validation(
    query: str,
    candidate_repos: list[str]
) -> list[Term]:
    """
    LLM 翻译 + 存在性验证的联合流程。
    
    如果所有翻译词在代码库中都不存在 → 把"这些词不存在"反馈给 LLM，
    让它换一种翻译思路。这是存在性验证的核心价值——
    "翻译 → 验证 → 反馈 → 重翻译"的闭环，而非"翻译 → 搜索"的单向流程。
    """
    raw_terms = await llm_translate_to_identifiers(query)
    
    if not raw_terms:
        return []
    
    verified = await validate_translation_existence(raw_terms, candidate_repos)
    
    existing = [t for t in verified if t.existence_verified is True]
    unverified = [t for t in verified if t.existence_verified is None]
    nonexistent = [t for t in verified if t.existence_verified is False]
    
    if len(existing) >= 2:
        return existing[:8]
    
    if len(existing) == 1:
        return (existing + unverified)[:8]
    
    # 全部不存在 → 重翻译
    if nonexistent:
        retry_terms = await llm_retranslate_with_feedback(
            query=query,
            failed_terms=[t.text for t in nonexistent],
            repo_context=candidate_repos
        )
        return retry_terms[:8]
    
    return raw_terms[:8]


RETRANSLATE_PROMPT = """将以下中文业务描述翻译为可能的代码标识符。

重要提示：上一轮翻译的以下标识符在代码库中**不存在**，请更换翻译思路：
{dont_use}

目标代码仓库为：{repos}（请考虑这些仓库的命名风格）

要求：
- 考虑不同的英文同义词和命名习惯
- 考虑缩写形式（如 limit→lim, validator→valid, amount→amt）
- 如果中文描述涉及多个概念，为每个概念给出独立的标识符
- 最多输出 6 个

中文: "{query}"

英文标识符:"""


async def llm_retranslate_with_feedback(
    query: str,
    failed_terms: list[str],
    repo_context: list[str]
) -> list[Term]:
    """告知 LLM 之前的翻译不存在，要求重新翻译。"""
    prompt = RETRANSLATE_PROMPT.format(
        dont_use='\n'.join(f"  ✗ {t}" for t in failed_terms),
        repos=', '.join(repo_context[:10]),
        query=query
    )
    response = await llm.invoke(prompt)
    terms = [t.strip() for t in response.split(",") if t.strip()]
    return [Term(t, weight=0.4, source="llm_retranslate") for t in terms]
```

**延迟分析：**

| 路径 | 延迟 | 发生率 |
|------|------|--------|
| 缓存命中 + 全部存在 | ~55ms（+50ms vs 原方案） | ~60% |
| 首次翻译 + 全部存在 | ~350ms（+50ms vs 原方案） | ~35% |
| 全部不存在 → 重翻译 | ~650ms | ~5% × 10% = 0.5% |

15% 的延迟增加换来"不搜不存在的词"——一个不存在的搜索词浪费的不止 50ms：它浪费 ripgrep 时间、占用搜索词位、挤掉可能存在的高价值词。

**与搜索词构造管线的集成：** 6.4 节第三层 LLM 翻译调用 `llm_translate_with_validation` 替代原 `llm_translate_to_identifiers`。存在性验证需要候选仓库列表（Phase 0 路由结果）——如果路由也失败（无候选仓库），跳过验证，直接返回 LLM 翻译结果做全量搜索。

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

### 6.9.2 被动学习通道：从代码仓库自动挖掘中英映射

现有反馈闭环（6.9）依赖用户点击行为来学习新映射。但上线初期存在**冷启动死循环**：映射表不准确 → 搜索结果差 → 用户不点击 → 反馈信号弱 → 映射表无法改进。

**核心洞察：代码仓库本身就是一个巨大的中英对照语料库。** 不需要等用户来点击——commit message、repo 元数据、代码注释中天然包含了大量"中文语义 ↔ 英文标识符"的对照信息。这些信息是**白捡的**，不需要任何用户交互。

#### 管道 P1：commit message 挖掘（信号最强）

开发者写 commit message 时用中文描述"做了什么"，而 changed files 列表天然给出了对应的英文标识符。这是三条管道中信号最强、噪音最低的——commit message 本质上是人类做的"意图→代码"映射标注。

```python
class CommitMessageMiner:
    """
    从 git log 的 commit message 中挖掘 中文→英文标识符 映射。
    
    延迟：离线批处理，每天凌晨跑一次。
    覆盖：500 仓库的团队，日均 ~200 条 commit，每月 ~6000 条候选。
    """

    async def mine_from_commits(
        self,
        repos: list[str],
        since: str = "7 days ago"
    ) -> list[DiscoveredMapping]:
        """扫描最近 N 天的 commit，提取中英对照。"""
        mappings = []
        
        for repo in repos:
            commits = await git_log(repo, since=since, format="%H|%s", name_only=True)
            
            for commit in commits:
                chinese_spans = self._extract_action_spans(commit.message)
                # "修复额度校验超时问题" → ["额度校验", "超时"]
                
                if not chinese_spans:
                    continue
                
                identifiers = set()
                for f in commit.changed_files:
                    identifiers.update(self._extract_stems(f))
                    # credit_limit_validator.py → credit, limit, validator
                
                for span in chinese_spans:
                    for ident in identifiers:
                        if len(ident) >= 4:  # 太短区分度低
                            mappings.append(DiscoveredMapping(
                                chinese=span, english=ident,
                                source="commit_message", repo=repo,
                                confidence=0.0,  # 聚合后计算
                                discovered_at=datetime.now()
                            ))
        
        return self._aggregate_and_filter(mappings)

    def _extract_action_spans(self, message: str) -> list[str]:
        """
        从 commit message 中提取有意义的动作/对象中文片段。
        
        "修复额度校验超时问题，优化了缓存策略"
        → ["额度校验", "超时", "缓存"]
        
        用 jieba 分词 + 停用词过滤。虚词（修复、优化、问题、的、了）去掉。
        """
        words = jieba.cut(message)
        stopwords = {'修复', '优化', '新增', '删除', '修改', '重构', '调整',
                     '问题', '逻辑', '代码', '的', '了', '和', '与', '及'}
        return [w for w in words if len(w) >= 2 and w not in stopwords]

    def _extract_stems(self, file_path: str) -> list[str]:
        """
        从文件路径中提取语义词干。
        
        src/credit/credit_limit_validator.py → ["credit", "limit", "validator"]
        src/auth/oauth_token_refresh.py       → ["auth", "oauth", "token", "refresh"]
        """
        stem = Path(file_path).stem
        parts = re.split(r'[_-]', stem)
        result = []
        for p in parts:
            result.extend(re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z][a-z]|\d|\b)', p))
        return [r.lower() for r in result if len(r) >= 3]

    def _aggregate_and_filter(
        self,
        raw: list[DiscoveredMapping]
    ) -> list[DiscoveredMapping]:
        """
        聚合 + 去噪 + 置信度打分。
        
        一个映射被多个 commit / 多个仓库独立发现 → 置信度高。
        只出现一次 → 可能是巧合，过滤掉。
        """
        groups = defaultdict(list)
        for m in raw:
            key = (m.chinese, m.english)
            groups[key].append(m)
        
        filtered = []
        for (cn, en), items in groups.items():
            # 规则1：至少出现 2 次
            if len(items) < 2:
                continue
            # 规则2：中文 ≥ 2 字，英文 ≥ 4 字符
            if len(cn) < 2 or len(en) < 4:
                continue
            # 规则3：过滤通用英文词
            if en.lower() in {'test', 'main', 'index', 'init', 'config',
                              'util', 'helper', 'common', 'base', 'model',
                              'service', 'controller', 'handler', 'manager'}:
                continue
            
            unique_repos = len(set(i.repo for i in items))
            confidence = min(0.9, 0.3 + 0.1 * len(items) + 0.1 * unique_repos)
            
            filtered.append(DiscoveredMapping(
                chinese=cn, english=en,
                source="commit_message",
                confidence=confidence,
                occurrence_count=len(items),
                unique_repos=unique_repos
            ))
        
        return sorted(filtered, key=lambda m: m.confidence, reverse=True)
```

**映射生效路径：** CommitMessageMiner 每日凌晨产出候选映射 → 置信度 ≥ 0.7 的自动写入 `CODE_SYNONYM_MAP`（weight=confidence × 0.7，source="commit_mined"）→ 不需要人工审核。每月汇总邮件通知新增映射。

#### 管道 P2：仓库元数据挖掘

利用 3.6 节 `repo_registry` 表自带的 display_name、description、tags：

```python
class RepoMetadataMiner:
    """
    从 repo_registry 提取中文→英文映射。
    
    display_name="额度校验服务" + repo_name="credit-service"
    → "额度校验" ↔ credit
    
    tags=["认证", "OAuth", "登录"] + repo_name="auth-service"
    → "认证" ↔ auth, "登录" ↔ auth
    """

    async def mine_from_registry(self) -> list[DiscoveredMapping]:
        repos = await db.fetch("""
            SELECT repo_name, display_name, description, tags
            FROM repo_registry
        """)
        
        mappings = []
        for r in repos:
            repo_stems = self._extract_stems(r['repo_name'])
            
            cn_words = jieba.cut(
                (r['display_name'] or '') + ' ' + (r['description'] or '')
            )
            cn_words = [w for w in cn_words if len(w) >= 2]
            
            for cn in cn_words:
                for stem in repo_stems:
                    if len(stem) >= 3:
                        mappings.append(DiscoveredMapping(
                            chinese=cn, english=stem,
                            source="repo_metadata", confidence=0.5
                        ))
            
            for tag in (r['tags'] or []):
                if re.search(r'[一-鿿]', tag):
                    for stem in repo_stems:
                        if len(stem) >= 3:
                            mappings.append(DiscoveredMapping(
                                chinese=tag, english=stem,
                                source="repo_tag", confidence=0.6
                            ))
        
        return self._aggregate(mappings)
```

#### 管道 P3：代码注释挖掘（Phase 2+，可选）

```python
class CodeCommentMiner:
    """
    从代码注释中挖掘 中文→函数名 映射。
    
    # 额度校验：检查单笔金额是否超出用户授信额度
    def check_credit_limit(user_id: str, amount: float) -> bool:
    
    → "额度校验" ↔ check_credit_limit
    
    只对 docstring 中文占比 > 50% 的函数做提取，避免英文 docstring 被误解析。
    """
    # 实现逻辑与管道 P1 类似，只是语料来源为 TreeSitter 提取的 docstring
```

#### 预期效果

| 指标 | 仅反馈闭环 | + 被动学习通道 |
|------|-----------|-------------|
| 冷启动映射数 | ~50 条 | ~50 + 首日 ~30 条 |
| 上线 1 周 | ~55 条 | ~120 条 |
| 上线 1 月 | ~80 条 | ~300 条 |
| 覆盖率达 85% | ~3 个月 | ~1 个月 |
| 覆盖率达 90% | ~6 个月 | ~2 个月 |

**关键：** 被动管道不需要等用户交互。代码仓库每天在 commit，每天就有新的中英对照数据。第一天跑就能从历史 commit 中挖出 ~30-50 条高置信度映射，直接跨越冷启动死循环。

> **反馈闭环 vs 被动学习的分工：** 反馈闭环（6.9）覆盖"用户用的口语/简称"（如"挂了"→"服务不可用"），这些词不会出现在 commit message 中。被动学习覆盖"开发者用的业务术语"（如"额度校验"→credit_limit），这些词不会出现在用户口语中但大量存在于工程记录中。两者互补，不是替代。

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
    
    # Step 2: 分数计算 + 截断
    return score_and_truncate(raw_terms, query_type)
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
  → 两路极简输出:
      ├─ file_path_cache 表（git ls-files → 文件路径列表，~25MB）
      │    用于 Phase 0 文件路径路由（500→5 仓库）
      │    触发: Git webhook push → git pull → git ls-files → upsert（~100ms/仓库）
      │
      └─ code_metadata 表（TreeSitter AST → 调用图元数据）
           用于 Phase 2 AST 调用图扩展（calls/called_by/imports）
           触发: Git webhook push → TreeSitter 解析变更文件 → upsert 调用图
      
      此外: 文件系统工作副本（git clone）
            用于 Phase 1 ripgrep 实时搜索 + Phase 2 read_file
            触发: 服务启动时 git clone；运行时 git webhook → git pull
```

**关键简化：** 
- 不再需要 `global_symbol_index`（AST 解析每个函数名建索引）——节省 ~450MB 存储和 TreeSitter 全量解析成本
- `code_chunks` 改为 `code_metadata`——不存源代码，只存调用图
- 核心搜索引擎是 ripgrep（始终搜最新文件系统），不依赖任何内容索引

> 完整的数据摄入管道设计见 [数据摄入管道设计](SPMA-design-05-data-ingestion.md)。

---

## 九、设计变更记录

### v3（2026-06-07）：架构重设计

**触发原因：** 用户指出 `global_symbol_index` 的维护成本与 embedding 同等——每次 commit 需重新解析。业界调研确认无人使用 AST 符号索引。

| 变更 | 说明 |
|------|------|
| 删除 `global_symbol_index` | 不再建符号内容索引。搜索全部由实时 ripgrep 承担 |
| 新增 3.3-3.7 文件路径路由详细实现 | 模式分类型构造（目录/文件名/域模板/工程约定）→ 加权 SUM + 多样性打分 → 分数断崖截断 → 三层兜底 → 搜索词管线集成 |
| `code_chunks` → `code_metadata` | 不存源代码（read_file 实时读），只存调用图 |
| 第一章重写 | 加入 Claude Code / Livegrep / Sourcegraph / Blackbird / OpenGrok 五大工具架构对比 |
| 第三章重写 | 文件路径路由 + ripgrep 替代全局符号索引 + 两阶段搜索 |
| 第四章简化 | code_chunks 从搜索索引降级为调用图元数据 |
| 第八章简化 | 数据摄入从三路简化为两路 |
| 其他章节适配 | 2.1、2.2、2.6、6.4、6.6 同步清理旧索引引用 |

### v2（2026-06-06 ～ 2026-06-07 上午）

| 变更 | 状态 |
|------|------|
| 全局符号索引 + 两阶段搜后聚合（第三章原版） | 被 v3 替代 |
| 代码域 IDF、Bloom Filter 预检、分级缓存（3.5-3.7 原版） | 被 v3 替代 |
| 自适应 K + 两轮检索兜底（3.11-3.13 原版） | 被 v3 替代 |
| 三工具协同 2.6、搜索词构造管线第六章、精简冗余 | 保留并适配 v3 |

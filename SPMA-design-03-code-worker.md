# Design: Code Agent 设计（代码检索Agent）

> 所属项目：[SPMA 全局概览](SPMA-design-00-global-overview.md)
> 权威架构：[5独立Agent架构设计](SPMA-design-07-agent-architecture.md) — **如有冲突以此为准**
> 相关模块：[Supervisor Agent](SPMA-design-01-supervisor-agent.md) — 负责通过 Send API 下发检索参数给本 Agent
> 模块职责：作为**检索 Agent**，通过 glob + grep + read_file 三工具协同 + AST 调用图扩展，对数百个代码仓库执行多轮自主检索循环——ripgrep 搜索 → 完备度判断 → 不够 → 调用链展开重搜 → 够了返回结果

---

## Agent 收敛契约

| 参数 | 值 |
|------|-----|
| **Agent 类型** | 检索 Agent |
| **最大轮数** | ≤3 |
| **收敛条件** | 结果≥3条 AND (调用链深度≤2层 OR 第3轮无新增文件) |
| **超时(含执行)** | 2s |
| **超时策略** | 返回当前结果+标注 |
| **确定性收敛** | 结果≥3 AND 第3轮无新增文件 → 自动收敛（不调LLM） |
| **LLM 兜底** | 确定性条件不满足 → Haiku判断是否充足（~300ms） |

### Agent 循环图

Code Agent 作为独立的 LangGraph 子图运行，节点与流转如下：

```
search（ripgrep 实时搜索）
    │
    ▼
assess（完备度判断）
    │
    ├─ 不够 → expand（AST调用链展开）→ 回到 search
    └─ 够了 → 返回结果（END）
```

- **search 节点：** 执行 ripgrep 搜索（Phase 1），含搜索词构造、文件路径路由和渐进式回退
- **assess 节点：** 完备度判断——确定性条件优先（结果≥3 AND 调用链≤2层→收敛），不满足时 LLM 兜底
- **expand 节点：** AST 调用图扩展——沿调用链 BFS 展开（upstream/downstream/both），提取新线索后回到 search
- **条件边：** assess → 收敛则 END，不收敛 → expand → search（最多 3 轮）

### Agent 状态数据模型

```python
class CodeAgentState(AgentState):
    """Code Agent 专属状态"""
    round: int                    # 当前检索轮次
    query: str                    # 本轮检索query
    search_terms: SearchTermSet   # 搜索词集合
    results: list[dict]           # 本轮检索结果
    assessment: str               # LLM完备度判断 ("sufficient" | "insufficient: missing X")
    confidence: float             # LLM自评信心 0-1
    call_depth: int               # 当前调用链展开深度
    new_files_this_round: int     # 本轮新增文件数
    has_exact_match: bool         # 是否命中精确引用（code_refs）
    llm_calls: int                # 本轮LLM调用次数
    latency_ms: int               # 本轮延迟
```

---

## 模块在架构中的位置

```
Supervisor Agent
    │
    │ Send API
    ▼
┌─────────────────────────────────────────┐
│           Code Agent  ← 本文档范围        │
│  (检索Agent, ≤3轮, 2s超时)               │
│  ┌─────────────────────────────────┐    │
│  │ Round 1: ripgrep 实时搜索        │    │
│  │   ├─ 搜索词构造管线               │    │
│  │   ├─ Phase 0: 文件路径路由        │    │
│  │   ├─ Phase 1: 实时 ripgrep 搜索   │    │
│  │   └─ Phase 2: 上下文补全          │    │
│  └──────────────┬──────────────────┘    │
│  ┌──────────────▼──────────────────┐    │
│  │ 完备度判断:                       │    │
│  │  结果≥3 AND 调用链≤2层 → 收敛     │    │
│  │  不够 → AST调用链展开 + 重搜      │    │
│  └──────────────┬──────────────────┘    │
│  ┌──────────────▼──────────────────┐    │
│  │ 够了 → 返回结果 + WorkerOutput    │    │
│  └─────────────────────────────────┘    │
└─────────────────────────────────────────┘
    │
    ├── Doc Agent
    └── SQL Agent
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

**上一版设计的 `global_symbol_index`（AST 解析每个函数/类名建索引）是业界已知方案中最重的——它的维护成本比 embedding 更高，不是更低。**

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

### 2.1 Agent 循环内检索路径

Code Agent 采用**轻量路由 + 实时搜索 + 多轮 Agent 循环**策略——文件路径缓存确定候选仓库，ripgrep 搜最新代码，完备度判断决定是否继续：

```
Agent 循环入口（Supervisor Send API 触发）
    │
    ▼
Round 1: 搜索词构造管线（见第六章）
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
              └─ 零命中 → progressive_fallback_search() 四层递进回退
    │
    ▼
完备度判断（assess 节点）:
  ├─ 结果≥3 AND 调用链深度≤2层 → 收敛 ✓（确定性，不调LLM）
  ├─ 结果≥3 AND 第3轮无新增文件 → 收敛 ✓（确定性）
  ├─ 结果≥3 AND LLM判断"信息充足" → 收敛 ✓
  └─ 不满足 → Round 2+: AST调用链展开 + 新线索重搜
                        │
                        ▼
                  完备度判断 → 收敛 or Round 3（最后一轮，强制返回）
```

### 2.2 检索主流程

`code_search()` 是 Code Worker 的核心入口，执行三阶段流水线：

```
输入: query（用户原始查询）, entities（Supervisor 抽取的实体）, search_terms（可选，预构造的搜索词）
输出: SearchResult（包含排名结果、文件上下文、候选仓库、检索方法）

┌─────────────────────────────────────────────────────────────────┐
│                     code_search 三阶段流水线                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  [1] 搜索词构造（如果未传入）                                       │
│      query + entities → build_search_terms() → SearchTermSet     │
│          │                                                       │
│          ├─ 搜索词为空 → 走 git_log_search() 路径（需求追溯）        │
│          │                                                       │
│          ▼                                                       │
│  [2] Phase 0: 文件路径路由（~30ms）                                │
│      search_terms + entities → route_repos() → 候选仓库 5~8 个     │
│          │                                                       │
│          ├─ 路由失败 → should_ask_user? → 反问用户 / 全量兜底        │
│          │                                                       │
│          ▼                                                       │
│  [3] Phase 1: 实时 ripgrep 搜索（~200ms）                          │
│      候选仓库 × 搜索词 × 文件类型过滤 → parallel_ripgrep()           │
│          │                                                       │
│          ├─ 命中 → 继续 Phase 2                                   │
│          └─ 零命中 → progressive_fallback_search() 四层递进回退     │
│              ├─ 任一层命中 → 继续 Phase 2                          │
│              └─ 全部失败 → 返回空结果 + 建议                        │
│          │                                                       │
│          ▼                                                       │
│  [4] Phase 2: 上下文补全（~50ms）                                   │
│      命中文件 → read_file 完整上下文 + glob 关联文件 + AST 调用图     │
│          │                                                       │
│          ▼                                                       │
│  [5] 输出: 去重 → 排序 → SearchResult (top 20)                     │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

**Phase 1 ripgrep 搜索说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `repos` | 候选仓库列表，来自 Phase 0 路由结果 | — |
| `patterns` | 搜索模式列表（支持正则），用 `\|` 连接 | — |
| `file_patterns` | 文件类型过滤（如 `*.py`, `*.java`），通过 `-g` 传给 ripgrep | — |
| `max_results_per_repo` | 每仓库最多返回的结果数 | 50 |
| `timeout_ms` | 单仓库搜索超时 | 300ms |

**执行逻辑：** 对每个候选仓库构造 `rg --json -l -g "*.py" 'pattern1|pattern2' <repo_path>` 命令，所有仓库**并行异步执行**，结果按仓库+文件聚合后返回。核心命令示例：

```
rg --json -l -g "*.py" -g "*.java" 'token_refresh|oauth|refresh_token' /repos/auth-service
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

**四层递进回退的详细逻辑：**

```
progressive_fallback_search() 执行流程
═══════════════════════════════════════════════════

输入: original_patterns（原始搜索词）, candidate_repos, file_patterns, original_query

┌─────────────────────────────────────────────────┐
│ Layer F1: 词干拆分搜索（~20ms, confidence=0.75） │
├─────────────────────────────────────────────────┤
│ 1. 将原始模式拆分为独立词干                        │
│    输入: ["credit_limit", "quota_check"]          │
│    拆分: 先按 _ - 切分 → 再按驼峰边界切分           │
│    输出: {"credit", "limit", "quota", "check"}    │
│ 2. 过滤长度 < 3 的词干（太短无区分度）              │
│ 3. 用 | 连接所有词干，在候选仓库中并行 ripgrep      │
│ 4. 命中 → 标注 "fallback_stem_split"，返回          │
│    未命中 → 继续 F2                                │
└─────────────────────────────────────────────────┘
         │ 未命中
         ▼
┌─────────────────────────────────────────────────┐
│ Layer F2: 扩大候选仓库范围（~50ms, confidence=0.65）│
├─────────────────────────────────────────────────┤
│ 1. 用词干或原始搜索词查 repo_registry 表           │
│ 2. 补充 5 个元数据匹配的仓库到候选列表              │
│ 3. 用词干模式在扩大后的仓库列表中搜索                │
│ 4. 命中 → 标注 "fallback_expanded_repos"，返回      │
│    未命中 → 继续 F3                                │
└─────────────────────────────────────────────────┘
         │ 未命中
         ▼
┌─────────────────────────────────────────────────┐
│ Layer F3: 编辑距离模糊匹配（~100ms, confidence=0.55）│
├─────────────────────────────────────────────────┤
│ 生成模糊变体（只做两种安全变换）：                   │
│                                                    │
│   变换 1 — 后缀替换（基于预定义映射表）：             │
│   ┌──────────┬──────────────────────────┐         │
│   │ 后缀     │ 替换为                    │         │
│   ├──────────┼──────────────────────────┤         │
│   │ er       │ ing, ion, ed, ement      │         │
│   │ ing      │ er, ed, ion              │         │
│   │ ion      │ e, ing, ed               │         │
│   │ ed       │ ing, er, ion             │         │
│   │ ment     │ e, ing                   │         │
│   └──────────┴──────────────────────────┘         │
│   例: validator → validating, validation, validated│
│                                                    │
│   变换 2 — 相邻字符交换：                            │
│   例: refresh → refersh                           │
│                                                    │
│ 最多生成 20 个变体 → ripgrep 搜索 → 命中则返回        │
└─────────────────────────────────────────────────┘
         │ 未命中
         ▼
┌─────────────────────────────────────────────────┐
│ Layer F4: LLM 重新解释搜索意图（~300ms, confidence=0.45）│
├─────────────────────────────────────────────────┤
│ 1. 向 LLM 发送：原始 query + 已失败的搜索词列表     │
│ 2. LLM 分析用户真实意图，提出完全不同的搜索方向      │
│ 3. 用 LLM 建议的新词再次 ripgrep 搜索               │
│ 4. 命中 → 标注 "fallback_llm_retry"，返回            │
│    未命中 → 返回 None（全部回退失败）                 │
└─────────────────────────────────────────────────┘
```

**各层回退词的生成规则：**

| 辅助函数 | 输入 | 输出 | 核心逻辑 |
|---------|------|------|---------|
| `_extract_stems_from_patterns()` | `["credit_limit", "quota_check"]` | `{"credit","limit","quota","check"}` | 按 `_`、`-` 切分 + 驼峰边界识别（`[A-Z]?[a-z]+\|[A-Z]+(?=[A-Z][a-z])`）→ 滤除长度 < 3 的词 |
| `_generate_fuzzy_variants()` | `["validator", "refresh"]`，max=20 | `["validating","validation","refersh",...]` | 后缀替换（5 组已知映射）+ 相邻字符交换 → 去重 + 排除已有模式 → 截断到 20 个 |
| `_llm_alternative_search_terms()` | query="削峰填谷"，failed=["peak_shaving"] | `["traffic_smoothing","load_balance",...]` | LLM 分析失败原因 → 提案完全不同的搜索方向 → 返回 5 个新词 |

**回退结果的透明度：**

回退命中的结果通过以下字段标注降级策略，让 UI 能做视觉区分：

| 字段 | 类型 | 取值 | 说明 |
|------|------|------|------|
| `files` | `list[FileMatch]` | — | 匹配到的文件列表 |
| `method` | `str` | `"ripgrep"` / `"fallback_stem_split"` / `"fallback_expanded_repos"` / `"fallback_fuzzy_match"` / `"fallback_llm_retry"` | 命中所用的策略 |
| `note` | `str` | 如 "精确搜索无结果，已自动扩展为单字搜索" | 给用户的说明文字 |
| `confidence` | `float` | 1.0（精确）→ 0.45（四层兜底） | UI 据此做视觉区分（如颜色深浅） |

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

| 中文术语 | 英文代码标识符 |
|---------|---------------|
| **用户认证域** | |
| 登录 | `login`, `signin`, `authenticate`, `auth`, `oauth`, `sso` |
| 注册 | `register`, `signup`, `create_user`, `enroll` |
| 权限 | `permission`, `acl`, `rbac`, `authorize`, `role`, `access_control` |
| 会话 | `session`, `token`, `jwt`, `cookie` |
| 密码 | `password`, `passwd`, `credential`, `secret` |
| 验证码 | `captcha`, `verification_code`, `otp`, `sms_code` |
| **订单域** | |
| 下单 | `create_order`, `place_order`, `checkout`, `purchase` |
| 退款 | `refund`, `reverse`, `chargeback`, `return` |
| 购物车 | `cart`, `basket`, `shopping_cart`, `line_item` |
| 优惠券 | `coupon`, `voucher`, `promo_code`, `discount` |
| **支付域** | |
| 支付 | `payment`, `pay`, `charge`, `transaction`, `billing` |
| 对账 | `reconciliation`, `settlement`, `clearing`, `balance` |
| 分账 | `split`, `commission`, `profit_sharing`, `fee` |
| **通知域** | |
| 推送 | `push`, `notify`, `notification`, `fcm`, `apns`, `firebase` |
| 短信 | `sms`, `message`, `text_message`, `send_sms` |
| 邮件 | `email`, `mail`, `send_mail`, `smtp` |
| **通用技术术语** | |
| 超时 | `timeout`, `ttl`, `expire`, `expiry`, `deadline`, `ttl` |
| 重试 | `retry`, `backoff`, `circuit_breaker`, `resilience` |
| 缓存 | `cache`, `redis`, `memcache`, `cached`, `invalidate` |
| 队列 | `queue`, `kafka`, `rabbitmq`, `pulsar`, `message`, `event` |
| 定时任务 | `cron`, `scheduler`, `job`, `task`, `scheduled`, `interval` |
| 限流 | `rate_limit`, `throttle`, `quota`, `limiter` |
| 熔断 | `circuit_breaker`, `fallback`, `degradation`, `hystrix` |
| 幂等 | `idempotent`, `dedup`, `duplicate`, `exactly_once` |
| 分布式锁 | `lock`, `distributed_lock`, `mutex`, `redis_lock` |

> 这是底表数据。带权重和上下文的增强版结构见 [6.4 节](#64-type-c纯中文三层递进翻译)。维护方式见 [Supervisor Agent 设计 - 映射表维护](SPMA-design-01-supervisor-agent.md#映射表维护人工种子--自动发现--人工审核闭环)。

### 2.5 AST 调用图扩展

grep 找到目标函数后，沿 AST 调用图扩展上下文——这是"不用 embedding 但仍能补全相关代码"的关键机制：

```
expand_via_call_graph() 执行流程
═══════════════════════════════════════

输入: seeds（Phase 1 ripgrep 匹配到的文件）, direction（"upstream"/"downstream"/"both"）, max_depth（默认 2）

┌──────────────────────────────────────────────────────────┐
│ Step 1: 查 code_metadata 获取调用图                        │
│   seeds（文件路径 + 行号）→ lookup_code_metadata()          │
│   → 获取每个种子函数的 calls（调用了谁）和 called_by（谁调用了它）│
├──────────────────────────────────────────────────────────┤
│ Step 2: BFS 逐层扩展（max_depth 层）                        │
│   depth=0: frontier = [种子函数的 code_metadata]            │
│   depth=1:                                                │
│     upstream 方向 → 遍历 frontier 中每个 meta.called_by    │
│     downstream 方向 → 遍历 frontier 中每个 meta.calls       │
│     both 方向 → 两方向同时扩展                              │
│     每次发现新的 caller/callee → 查其 code_metadata → 加入  │
│       下一层 frontier                                      │
│   depth=2: 重复上述过程（再扩展一层调用链）                   │
├──────────────────────────────────────────────────────────┤
│ Step 3: 返回所有已扩展的 code_metadata 列表                  │
└──────────────────────────────────────────────────────────┘
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

```
glob_file_discovery() 模式构造逻辑
═══════════════════════════════════════════

输入: search_terms（如 ["oauth", "token"]）, candidate_repos, entities（module="用户登录"）

┌──────────────────────────────────────────────────────────────┐
│ 模式来源                                                     │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  来源1 — 搜索词 → 文件路径模式（每个 term 生成 4 种变体）：       │
│    term = "oauth"                                            │
│    ├─ **/*oauth*         (路径任意位置包含)                     │
│    ├─ **/oauth/**        (作为目录层级)                        │
│    ├─ **/*oauth*.py      (特定语言文件)                        │
│    └─ **/test*oauth*     (测试文件)                           │
│                                                              │
│  来源2 — module 实体 → 目录结构模式：                           │
│    entities.module = "用户登录"                               │
│    → module_to_path_patterns() 查同义词表                      │
│    → ["**/auth/**", "**/login/**", "**/sso/**"]              │
│                                                              │
│  来源3 — 通用工程文件模式（始终附加）：                           │
│    **/README*, **/*.yaml, **/*.yml, **/*.toml,               │
│    **/Dockerfile*, **/docker-compose*, **/Makefile           │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  去重 + 截断: 合并三类来源 → 去重 → 保留前 20 个模式             │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────┐
│ 并行执行: 每个候选仓库 × 每个模式 → glob 命令                   │
│ 相关性打分: score_file_relevance(file, search_terms, entities) │
│ 结果: 按 relevance 降序排列，取前 30 个文件                      │
└──────────────────────────────────────────────────────────────┘
```

**glob 的典型使用场景：**

| 用户查询 | glob 模式 | 发现内容 |
|---------|----------|---------|
| "oauth 模块有哪些文件" | `**/oauth*/**`, `**/auth/**` | 模块的完整文件树 |
| "token_refresh 的测试在哪" | `**/test*token*`, `**/token*test*` | 单元测试文件 |
| "认证服务的配置文件" | `**/auth*/**/*.yaml`, `**/auth*/**/*.toml` | 配置、部署文件 |
| "跟 oauth 相关的数据库迁移" | `**/migration*/*oauth*`, `**/alembic/**/*oauth*` | DDL 变更脚本 |

#### 2.6.2 read_file：完整文件上下文

**解决的核心问题：** ripgrep 只返回匹配行，但代码理解需要完整的文件级上下文。以下信息不在任何函数/类内部，仅靠行匹配无法获取：

- `import` 语句和依赖关系
- 模块级常量、配置变量
- `__init__.py` 的公开导出列表
- 装饰器的实现（如果定义在其他文件）
- 文件级 docstring

```
read_file_context() 执行流程
════════════════════════════════════

输入: seed_files（Phase 1 ripgrep 匹配到的文件）, candidate_repos,
      expand_imports（是否追踪 import 链，默认 True）, max_files（默认 10）

┌────────────────────────────────────────────────────────────┐
│ Step 1: 收集待读取文件                                        │
│   从 seed_files 提取 (repo, file_path) 去重 → 截断到 max_files│
├────────────────────────────────────────────────────────────┤
│ Step 2: 逐个读取完整文件内容                                   │
│   read_file(repo, file_path) → 完整源代码                     │
│   对每个文件提取:                                             │
│   ├─ imports:      extract_imports(content)  → 依赖列表       │
│   ├─ module_level: extract_module_level_defs(content) → 常量  │
│   └─ docstring:    extract_file_docstring(content) → 文件注释  │
├────────────────────────────────────────────────────────────┤
│ Step 3（可选）: 追踪 import 链                                │
│   for imp in file_ctx.imports:                              │
│     if is_internal_import(imp) and len(results) < max_files: │
│       resolve_and_read(repo, imp) → 读取被 import 的内部模块   │
│       追加到 results                                         │
├────────────────────────────────────────────────────────────┤
│ 输出: FileContext 列表，每个包含:                              │
│   repo, file_path, content, imports[], module_level{},       │
│   docstring                                                 │
└────────────────────────────────────────────────────────────┘
```

**read_file 的价值：**

`read_file` 获取的是完整文件内容，补足 ripgrep 单行匹配所缺失的上下文——`import` 语句、模块常量、装饰器、文件级 docstring 等。这些信息决定了对代码的**理解质量**，而不仅仅是定位准确度。

**使用准则：** 当用户只问"token_refresh 逻辑是什么"→ ripgrep 匹配行 + AST 调用图通常足够。当用户问"oauth 模块的认证流程是怎么设计的"→ 需要 read_file 获取完整文件上下文。

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

**核心思路：** 不同的搜索词类型对应不同的文件路径特征。`build_path_patterns()` 函数输入 `SearchTermSet` + `ExtractedEntities`，输出 `PathPatternSet`（含四类加权模式）。

**四类路径匹配模式：**

| 模式类型 | 权重 | 输入来源 | 生成的 LIKE 模式 | 示例（term="auth"） |
|---------|------|---------|-----------------|-------------------|
| **目录结构** | 1.0 ~ 0.7 | `exact_terms` + `fuzzy_terms` | `%/term/%`, `%/term_%/%`, `%/term-%/%` | `%auth%`, `%auth_%/%`, `%auth-%/%` |
| **文件名** | 0.9 ~ 0.5 | `exact_terms` | `%/term.%`, `%/term_%`, `%/term-%`, `%term%` | `%/oauth.py`, `%/oauth_%`, `%oauth%` |
| **业务域模板** | 0.8 ~ 0.6 | `entities.module` → 同义词表展开 | `%/kw/%`, `%/kw_%` | "用户登录"→`%/auth/%`, `%/login/%`, `%/sso/%` |
| **工程约定** | 0.5 ~ 0.3 | `fuzzy_terms` 前 3 个 | 测试/配置/迁移变体 | `%/test%auth%`, `%/auth%config%` |

**模式构造流程：**

```
build_path_patterns(search_terms, entities)
═══════════════════════════════════════════════

[1] 目录结构模式（权重最高 1.0）
    遍历 exact_terms + fuzzy_terms → add_directory(term)
    每个 term 生成 3 个 LIKE 模式:
      %/term/%     (term 作为独立目录，得分 1.0)
      %/term_%/%   (term 作为目录前缀，得分 0.7)
      %/term-%/%   (term 作为目录前缀，得分 0.7)

[2] 文件名模式（权重次高 0.9）
    遍历 exact_terms → add_filename(term)
    每个 term 生成 4 个 LIKE 模式:
      %/term.%     (term 作为文件名，得分 0.9)
      %/term_%     (term 作为文件名前缀，得分 0.8)
      %/term-%     (term 作为文件名前缀，得分 0.7)
      %term%       (term 出现在路径任意位置，得分 0.5)

[3] 业务域路径模板（权重 0.8）
    entities.module → lookup_code_terms(module) → 英文关键词列表
    例: "用户登录" → ["auth","login","sso","oauth"]
    每个关键词 × 2 个模式:
      %/kw/%       (关键词作为目录，得分 0.8)
      %/kw_%       (关键词作为前缀，得分 0.6)

[4] 工程约定路径（权重最低 0.5）
    遍历 fuzzy_terms 前 3 个 → add_convention(term)
    每个 term 生成 4 个工程约定模式:
      %/test%term%       (测试文件, 0.5)
      %/term%test%       (测试文件, 0.5)
      %/term%config%     (配置文件, 0.4)
      %/term%migration%  (迁移文件, 0.3)

[5] 合并去重:
    all_patterns() → 4 类合并 → 同一 pattern 保留最高权重 → 返回 (pattern, weight) 列表
```

**为什么分类型：** 不是所有 `LIKE '%token%'` 命中都有同等价值。`src/auth/token_refresh.py`（目录+文件名同时命中）比 `README.md` 中恰好包含 "token" 这个字符串有意义得多。分类型打分让目录结构命中的权重大于随机子串命中。

### 3.4 第二步：SQL 查询 + 加权打分

`query_file_path_cache()` 是路由查询的核心——执行一次带参数化 LIKE 的 SQL，在 50 万行表上完成加权打分。延迟目标 < 30ms。

**查询构造逻辑：**

```
query_file_path_cache(patterns, min_match=1, top_k=15)
══════════════════════════════════════════════════════════

[1] 遍历四类模式，为每类生成 UNION ALL 子查询:
    
    目录模式:  SELECT repo_name, file_path, 1.0 AS weight, 'dir' AS match_type
               FROM file_path_cache WHERE file_path LIKE '%/auth/%'
    UNION ALL
    文件名模式: SELECT repo_name, file_path, 0.9 AS weight, 'file' AS match_type
               FROM file_path_cache WHERE file_path LIKE '%/oauth.%'
    UNION ALL
    域模板:    SELECT repo_name, file_path, 0.8 AS weight, 'domain' AS match_type
               FROM file_path_cache WHERE file_path LIKE '%/auth/%'
    UNION ALL
    约定模式:  SELECT repo_name, file_path, 0.5 AS weight, 'conv' AS match_type
               FROM file_path_cache WHERE file_path LIKE '%/test%oauth%'
    ...（每种模式一行 LIKE，参数化绑定，防 SQL 注入）

[2] 聚合 + 打分 (CTE: repo_stats):
    
    GROUP BY repo_name
    → SUM(weight)                AS weighted_score     （加权分数之和）
    → COUNT(DISTINCT file_path)  AS unique_files       （去重文件数）
    → COUNT(DISTINCT match_type) AS type_diversity     （命中了几种类型？1~4）
    → MAX(weight)                AS top_weight         （最高单次权重）
    
    HAVING unique_files >= min_match  （至少匹配 min_match 个不同文件）

[3] 最终得分公式:
    
    final_score = weighted_score × (1.0 + (type_diversity - 1) × 0.3)
    
    多样性加成:
      type_diversity=1  → ×1.0   (仅命中一种类型)
      type_diversity=2  → ×1.3   (命中两种类型)
      type_diversity=3  → ×1.6   (命中三种类型)
      type_diversity=4  → ×1.9   (四种全命中)

[4] ORDER BY final_score DESC LIMIT top_k → 返回候选仓库列表
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

Phase 0 得到一批候选仓库和分数后，需要决定取多少个进入 Phase 1（ripgrep 搜索）。这个决策直接影响延迟和召回率。

`trim_candidates()` 用三条规则按优先级从高到低截断候选仓库列表：

```
trim_candidates(scored_repos, max_repos=8, min_repos=1, score_drop_threshold=0.6)
═════════════════════════════════════════════════════════════════════════════

输入: [(repo_name, final_score, unique_files), ...]（按 final_score 降序排列）

┌─────────────────────────────────────────────────────────────────┐
│ 规则 1（最高优先级）: 分数断崖检测                                │
├─────────────────────────────────────────────────────────────────┤
│ 遍历排序后的仓库，检查相邻分数 gap                                │
│   gap = (prev_score - curr_score) / prev_score                  │
│   若 gap > 0.6（60%）→ 在断崖处截断                              │
│                                                                 │
│ 示例: [0.95, 0.92, 0.31, 0.28]                                 │
│   gap(0.92→0.31) = (0.92-0.31)/0.92 = 66% > 60%                │
│   → 截断在 index=2，返回前 2 个仓库                               │
│                                                                 │
│ 确保至少返回 min_repos 个仓库（即使断崖出现在更早位置）             │
└─────────────────────────────────────────────────────────────────┘
         │ 无断崖 → 继续规则 2
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 规则 2: 分数绝对值阈值                                            │
├─────────────────────────────────────────────────────────────────┤
│ 过滤条件:                                                        │
│   • score >= top_score × 0.15（不低于最高分的 15%）               │
│   • unique_files >= 2（至少匹配 2 个不同文件）                     │
│                                                                 │
│ 示例: top_score=240                                              │
│   auth-service: 240 ✓  gateway: 3 ✗（< 36 = 240×0.15）          │
└─────────────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 规则 3: 上限截断                                                  │
├─────────────────────────────────────────────────────────────────┤
│ qualified[:max_repos] → 最多返回 8 个仓库                        │
└─────────────────────────────────────────────────────────────────┘
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

```
route_repos() 三层递进路由
═══════════════════════════════

输入: search_terms, entities, min_candidates（默认 3）
输出: RouteResult（repos[], method, confidence）

┌─────────────────────────────────────────────────────────────────┐
│ Layer 1: 文件路径缓存匹配（~30ms, 覆盖 ~80% 查询）                 │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  patterns = build_path_patterns(search_terms, entities)         │
│           → 四类加权 LIKE 模式                                   │
│                                                                 │
│  candidates = query_file_path_cache(patterns)                   │
│             → SQL 加权求和 + 多样性打分                           │
│                                                                 │
│  trimmed = trim_candidates(candidates)                          │
│          → 三规则截断（断崖/阈值/上限）                            │
│                                                                 │
│  if len(trimmed) >= min_candidates:                             │
│    → 返回 trimmed, method="file_path_cache", confidence=HIGH    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         │ trimmed 不足 min_candidates → 进入 Layer 2
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 2: 仓库注册表匹配（~5ms, 额外覆盖 ~15%）                     │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  query_repo_registry() 查询逻辑:                                  │
│                                                                 │
│  repo_registry 表结构（~500 行）:                                 │
│  ┌──────────────┬──────────────────────────────────┐            │
│  │ 字段          │ 内容                              │            │
│  ├──────────────┼──────────────────────────────────┤            │
│  │ repo_name    │ "auth-service"                   │            │
│  │ display_name │ "用户认证服务"                     │            │
│  │ description  │ "处理用户登录、OAuth、SSO、权限管理" │            │
│  │ tags         │ ["认证","OAuth","登录","SSO"]      │            │
│  └──────────────┴──────────────────────────────────┘            │
│                                                                 │
│  匹配策略（三路并行）:                                             │
│  ├─ 中文关键词 → 匹配 display_name 和 description（ILIKE）        │
│  ├─ 英文关键词 → 匹配 tags（ANY 数组）                            │
│  └─ module 实体 → 匹配 display_name                              │
│                                                                 │
│  相关性打分:                                                     │
│    match_score = (满足的匹配条件数) / (总匹配条件数)                │
│                                                                 │
│  过滤 + 合并:                                                    │
│  ├─ match_score > 0.6 的仓库才纳入                                │
│  ├─ 排除 Layer 1 已找到的仓库                                    │
│  └─ 与 Layer 1 结果合并去重，上限 8 个                             │
│                                                                 │
│  if len(merged) >= min_candidates:                              │
│    → 返回 merged, method="file_path_cache+repo_registry",       │
│      confidence=MEDIUM                                           │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
         │ merged 仍不足 min_candidates → 进入 Layer 3
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ Layer 3: 全量顶层目录扫描（~100ms, 额外覆盖 ~4%）                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  为什么只扫顶层目录？                                             │
│  - 一个仓库通常只有 10-30 个顶层目录                               │
│  - 500 个仓库 = 5000-15000 条目（vs 50 万行全量表）               │
│  - 顶层目录结构很少变 → 可缓存 1 小时（TOP_DIR_CACHE）              │
│                                                                 │
│  scan_all_repo_top_dirs() 查询逻辑:                              │
│    从 file_path_cache 中提取每个仓库的前两级目录                    │
│    SPLIT_PART(file_path, '/', 1) || '/' ||                      │
│    SPLIT_PART(file_path, '/', 2)                                │
│    排除: .git/, node_modules/, __pycache__/                     │
│                                                                 │
│  fuzzy_match_dirs() 匹配逻辑:                                    │
│  ├─ 子串匹配（keyword in top_dir）→ +1.0 分                      │
│  └─ 编辑距离相近（levenshtein_ratio > 0.7）→ +0.5 分              │
│                                                                 │
│  if len(merged) >= min_candidates:                              │
│    → 返回 merged, method="full_top_dir_scan", confidence=LOW    │
│  else:                                                          │
│    → 全量兜底: 反问用户 或 扩大至 50 仓库 ripgrep（< 1%, ~2-3s）   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
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

```
SearchTermSet（第六章产出）               ExtractedEntities（Supervisor 产出）
├─ exact_terms: ["token_refresh"]       ├─ module: "用户登录"
├─ fuzzy_terms: ["oauth", "auth"]       ├─ code_refs: ["oauth.py"]
└─ tag_terms:   ["认证", "OAuth"]       └─ req_ids: ["REQ-187"]
        │                                       │
        └────────────┬──────────────────────────┘
                     ▼
            build_path_patterns(search_terms, entities)
                     │
                     ▼
            PathPatternSet（四类加权 LIKE 模式）
```

**关键设计原则：** 搜索词构造管线不需要理解文件路径路由的实现。它只负责产出搜索词。路由模块自己决定如何把这些词映射为路径模式。两个模块通过 `SearchTermSet` 解耦。

### 3.8 完整执行流程模拟

以下用三个真实问题走完整条管线——从用户输入到 Phase 1 候选仓库列表。假设环境为 500 个微服务仓库的生产集群。

#### 模拟 A：精确引用 — "oauth.py 的 token_refresh 函数逻辑是什么"

| 步骤 | 内容 |
|------|------|
| **实体抽取** | `code_refs=["oauth.py","token_refresh"]`, module=None, type=EXACT_REFS |
| **搜索词构造** | exact=["token_refresh","oauth.py"], fuzzy=["token","refresh","oauth"]（形态扩展），共 3+3=6 个词 |
| **路径模式构造** | dir: `%/oauth/%`, `%/token/%`, `%/refresh/%`; file: `%/oauth.%`, `%/token_refresh%`; convention: `%/test%oauth%` 等，共 12 个 LIKE 模式 |
| **SQL 查询** | **auth-service**: score=387.2, files=312, types=4（dir+file+conv）；auth-gateway: 12.4；sso-service: 3.1；user-service: 1.8；monitor-service: 0.5 |
| **候选截断** | 断崖检测: auth-service(387.2)→auth-gateway(12.4)，gap=96.8% > 60% → **截断**，只保留 auth-service |
| **输出** | 候选: `["auth-service"]`，方法: file_path_cache，置信度: HIGH，延迟: ~35ms |

#### 模拟 B：中英混合 — "用户登录的 OAuth token 刷新逻辑在哪"

| 步骤 | 内容 |
|------|------|
| **实体抽取** | `code_refs=[]`, module="用户登录", type=MIXED_CN_EN |
| **搜索词构造** | 英文直提: OAuth(0.95), token(0.95)；中文→同义词: "用户登录"→login,auth,authenticate,oauth,sso，"刷新"→refresh,renew,rotate；共现加分: OAuth+token→refresh_token(+0.3)。fuzzy_terms 共 7 个，tag_terms 3 个 |
| **路径模式构造** | dir: 7 个目录模式；file: 4 个文件名模式；domain: module→auth,login,sso,oauth；convention: test/ config/ migration 变体，共 28 个 LIKE 模式 |
| **SQL 查询** | **auth-service**: 452.8/385files/4types; auth-gateway: 218.6/172/3; sso-service: 185.3/140/3; user-service: 162.4/118/3; login-guard: 58.2/45/2; session-service: 42.1/32/2; captcha-service: 15.3/12/1 |
| **候选截断** | 依次 gap: 52%→15%→12%→**64%**（user-service→login-guard 处截断）；绝对值过滤: login-guard(58.2) < 15%×452.8=67.9 → 额外排除 |
| **输出** | 候选: `["auth-service","auth-gateway","sso-service","user-service"]` 共 4 个，方法: file_path_cache，置信度: HIGH，延迟: ~32ms |

#### 模拟 C：纯中文 + 路由失败 — "额度校验的逻辑在哪"

| 步骤 | 内容 |
|------|------|
| **实体抽取** | `code_refs=[]`, module="额度校验", type=PURE_CN |
| **搜索词构造** | 第一层同义词表: 未覆盖；第二层模块→符号: 未覆盖；第三层 LLM 翻译: → ["credit_limit","quota_check","limit_validation","amount_verify"]（首次 ~300ms）。fuzzy_terms 4 个 |
| **路径模式构造** | dir: /credit/, /quota/, /limit/, /amount/; file: /credit_limit%, /quota_check% 等; domain: 未在映射表; convention: test 变体。共 16 个 LIKE 模式 |
| **SQL 查询** | credit-service: 8.4/7files/2types; risk-service: 2.1/2/1 — ⚠ 仅 2 个候选、分数极低 |
| **候选截断** | len=2 < min_candidates(3) → 触发 Layer 2 仓库注册表兜底 |
| **Layer 2 兜底** | repo_registry 匹配: billing-service(0.85, tags:["额度","计费"]), order-service(0.72, desc:"...订单额度校验...")；合并后 len=4 ≥ 3 → 返回 |
| **输出** | 候选: `["credit-service","billing-service","order-service"]`（risk-service 被排除），方法: file_path_cache+repo_registry，置信度: MEDIUM，延迟: ~335ms（首次）/ ~35ms（缓存后） |

#### 三个模拟的对比总结

| | 模拟 A（精确引用）| 模拟 B（中英混合）| 模拟 C（纯中文+兜底）|
|---|---|---|---|
| **搜索词构造** | < 5ms（直接提取）| < 10ms（同义词表）| ~300ms 首次 / 5ms 缓存 |
| **路径模式数** | 12 | 28 | 16 |
| **SQL 耗时** | ~28ms | ~27ms | ~25ms |
| **候选仓库数** | 1（截断）| 4（截断）| 3（Layer 1→2）|
| **路由层** | Layer 1 | Layer 1 | Layer 1→2 |
| **置信度** | HIGH | HIGH | MEDIUM |
| **总路由延迟** | ~35ms | ~32ms | ~335ms / 35ms |
| **端到端延迟** | ~150ms | ~200ms | ~400ms / 150ms |

**关键观察：**
- 模拟 A：精确引用时断崖极深（96.8% gap），自适应截断省掉 4 次无效 ripgrep
- 模拟 B：中英混合场景，同义词表覆盖全部翻译，零 LLM 调用
- 模拟 C：两层级都未覆盖 → LLM 翻译是唯一兜底 → 首次 300ms，缓存后仅 5ms
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

## 五、实体驱动的检索分发 & Agent Action Guard

### Agent Action Guard

Code Agent 可调用的工具受白名单限制：

```python
ALLOWED_ACTIONS = {
    'code': ['ripgrep', 'read_file', 'glob', 'ast_expand', 
             'completeness_check', 'return_results'],
}
```

### 实体驱动的检索分发决策树

```
route_code_search(entities, user_query)
══════════════════════════════════════════

输入: entities（Supervisor 抽取的实体）, user_query（用户原始查询）
输出: SearchResult

┌──────────────────────────────────────────────────────────────┐
│ 判断 1: 是否为需求追溯/人员时间查询？                            │
│   entities.req_ids 非空?                                     │
│   entities.person 非空?                                      │
│   entities.time_range 非空?                                  │
│                                                              │
│   YES → 路径 D/E: git_log_search(entities)                   │
│         不经过符号搜索管线，直接走 git log                      │
│         → 返回结果                                             │
└──────────────────────────────────────────────────────────────┘
         │ NO
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 1: 搜索词构造（所有路径共用）                               │
│   search_terms = build_search_terms(user_query, entities)     │
│                                                              │
│   search_terms.has_any == False?                             │
│     → 返回空 SearchResult + 建议 + generate_query_suggestions()│
└──────────────────────────────────────────────────────────────┘
         │ search_terms 非空
         ▼
┌──────────────────────────────────────────────────────────────┐
│ 判断 2: 实体类型 → 检索路径                                    │
│                                                              │
│   entities.code_refs 非空?                                   │
│     → 路径 A（精确引用）:                                      │
│       code_search(query, entities, search_terms)             │
│       其中 exact_terms 包含 code_refs，Phase 1 优先精确匹配     │
│                                                              │
│   entities.module 非空?                                      │
│     → 路径 B（模块锚定）:                                      │
│       code_search(query, entities, search_terms)             │
│       从 module 构造了英文标识符，零命中时有静态映射兜底          │
│                                                              │
│   以上皆空:                                                    │
│     → 路径 C（纯关键词）:                                      │
│       code_search(query, entities, search_terms)             │
│       从 query 提取的所有搜索词，零命中时反问用户                │
└──────────────────────────────────────────────────────────────┘
```

### 实体用法详表

| 实体 | 用法 | 优先级 | 示例 |
|------|------|-------|------|
| `code_refs` | 精确搜索——作为 `exact_terms` 直接参与 ripgrep 搜索 | **最高** | `token_refresh` → 精确匹配代码中的符号 |
| `req_ids` | 关联搜索——git log 中搜索需求 ID，不依赖符号索引 | 高 | `git log --grep="REQ-2024-0187"` 找变更文件 |
| `module` | 搜索词构造——映射中文术语→英文标识符；辅助 Phase 1 零命中时补充候选仓库 | 高 | "用户登录"→搜索词 `["login","auth","oauth"]` |
| `person` | 作者过滤——`git log --author="张三"` 限定变更范围 | 中 | 结合 `time_range` 精确定位 |
| `time_range` | 时间过滤——限制 git log 的 `--since` / `--until` | 中 | `git log --since="2026-05-29"` |
| `version` | 分支/tag 过滤 | 中 | `git log release/2026Q1` |

**关键变化：** `module` 实体不再主要用于仓库路由（限定搜哪些仓库），而是用于**搜索词构造**——把中文模块名翻译为英文代码标识符。仓库路由由文件路径缓存（Phase 0）自动完成。

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

`detect_query_type()` 是搜索词构造管线的入口，纯规则判断，延迟 < 1ms，不需要 LLM：

```
detect_query_type(entities, raw_query)
═══════════════════════════════════════

判断优先级（从上到下，命中即返回）:

  ┌─ entities.code_refs 非空?           → QueryType.EXACT_REFS
  ├─ entities.req_ids 非空?             → QueryType.REQ_TRACE
  ├─ entities.person 非空?              → QueryType.PERSON_TIME
  ├─ entities.time_range 非空?          → QueryType.PERSON_TIME
  │
  ├─ 语言特征判断:
  │   has_english = regex: [a-zA-Z_][a-zA-Z0-9_]{2,}
  │   has_chinese = regex: [一-鿿]
  │
  ├─ has_chinese AND has_english  → QueryType.MIXED_CN_EN
  ├─ has_chinese AND NOT english  → QueryType.PURE_CN
  └─ otherwise                     → QueryType.PURE_EN
```

### 6.2 Type A：精确实体引用

用户给了明确符号名。核心：**形态扩展**（morphological variants）——同一个语义在代码中可能有多种命名约定。

`expand_exact_refs()` 的处理逻辑：

```
输入: code_refs = ["oauth.py", "token_refresh"]
输出: Term 列表（去重 + 按权重排序）

对每个 ref 判断类型:

  ref 含 '/' 或 '.' → 按文件路径处理:
    parse_file_ref(ref)
    ├─ parsed.stem  → Term("oauth", weight=1.0, source="file_path")
    │                 例: "src/auth/oauth.py" → "oauth"
    └─ parsed.module → Term("auth", weight=0.6, source="file_path_module")
                      例: 提取路径中的目录名作为辅助搜索词

  ref 含 '_' 或首字母大写 → 按符号名处理:
    ├─ Term(ref, weight=1.0, source="exact_symbol")    保留原样
    └─ morphological_variants(ref)                      形态扩展
```

**形态扩展的变换规则：** 对已知符号名按以下规则生成命名约定变体。变换前提：符号至少含 2 个词干（按 `_` 拆分），单词语义无法形态扩展。

| 变换 | 输入 → 输出 | 权重 | 说明 |
|------|------------|------|------|
| **词序颠倒** | `token_refresh` → `refresh_token` | 0.7 | 函数名 vs 文件名常见差异 |
| **PascalCase** | `token_refresh` → `TokenRefresh` | 0.5 | 类名约定 |
| **camelCase** | `token_refresh` → `tokenRefresh` | 0.5 | 方法名约定 |
| **缩写扩展** | `cfg` → `config` | 0.4 | 依赖 ABBREV_MAP 映射表 |

处理流程：遍历 `code_refs` 中的每个引用——含 `/` 或 `.` 的按文件路径处理（提取 stem 和目录名），含 `_` 或首字母大写的按符号名处理（保留原样 + 生成上述四种变体）。所有生成的 Term 去重并按权重降序排列。

### 6.3 Type B：中英混合——最重要的场景（~35% 查询）

策略：**英文部分信用户的（高置信），中文部分走翻译。**

`extract_mixed_query()` 四步处理：

```
输入: "用户登录的 OAuth token 刷新逻辑", entities（module="用户登录"）
输出: Term 列表（去重 + 排序，最多 10 个）

Step 1 — 提取英文标识符（weight=0.95，最高置信）:
  regex: [a-zA-Z_][a-zA-Z0-9_]{2,}
  query → ["OAuth", "token"]
  → Term("OAuth", 0.95, "user_provided_en")
  → Term("token", 0.95, "user_provided_en")

Step 2 — 提取中文片段:
  extract_chinese_spans(query) → ["用户登录的", "刷新逻辑"]
  (英文词被当作分隔符/锚点，不参与中文分词)

Step 3 — 每个中文片段独立翻译:
  span="用户登录的" → clean_span → "用户登录"
    entities.module="用户登录" 匹配此片段
    → lookup_module_code_terms("用户登录") → [login, auth, oauth, sso]

  span="刷新逻辑" → clean_span → "刷新"
    entities.module 不匹配此片段（它是"用户登录"）
    → translate_chinese_span("刷新", fallback_weight=0.6)
    → [refresh, renew, rotate]

Step 4 — 共现加分:
  boost_cooccurring_terms(terms, anchor_tokens=["OAuth","token"])
```

`boost_cooccurring_terms()` 的共现模式：

如果用户提到了两个锚定词 A 和 B，检查每个候选词是否与 (A, B) 有已知的共现模式：

| 锚定词对 | 候选词加成 | 加成值 | 说明 |
|---------|-----------|--------|------|
| `(oauth, token)` | `refresh_token` | +0.3 | 用户提到 OAuth+token 时，refresh_token 更可能 |
| `(oauth, token)` | `access_token` | +0.3 | |
| `(oauth, token)` | `token_refresh` | +0.3 | |
| `(oauth, token)` | `grant_token` | +0.2 | |
| `(payment, callback)` | `payment_callback` | +0.3 | |
| `(payment, callback)` | `notify_url` | +0.2 | |

> 共现模式表从历史成功搜索中自动挖掘（见 6.8 反馈闭环），上线时用人工种子覆盖最热门的 20-30 组模式。

### 6.4 Type C：纯中文——三层递进翻译

最常见的场景，用户完全用中文描述代码行为。`translate_pure_cn_query()` 用三层递进策略，前一层命中就停止：

```
translate_pure_cn_query(query, entities)
══════════════════════════════════════════

┌─────────────────────────────────────────────────────────────────┐
│ 第一层: 同义词映射表（~1ms, 覆盖 60-70%）                         │
├─────────────────────────────────────────────────────────────────┤
│ lookup_synonym_map(query) → 用 jieba 分词后查 CODE_SYNONYM_MAP   │
│                                                                 │
│ 带权重与上下文的增强映射表（底表数据来自 2.4 节 CODE_TERM_MAP）：    │
│                                                                 │
│ ┌────────┬─────────────┬───────┬──────────────────────┐        │
│ │ 中文    │ 英文标识符    │ 权重  │ 适用上下文             │        │
│ ├────────┼─────────────┼───────┼──────────────────────┤        │
│ │ 登录    │ login       │ 0.9   │ 通用登录              │        │
│ │        │ signin       │ 0.7   │ 前端/UI层            │        │
│ │        │ authenticate │ 0.8   │ 后端认证逻辑          │        │
│ │        │ auth         │ 0.6   │ 缩写, 模块名/路径     │        │
│ │        │ do_login     │ 0.5   │ 具体实现函数          │        │
│ ├────────┼─────────────┼───────┼──────────────────────┤        │
│ │ 刷新    │ refresh      │ 0.9   │ 通用                 │        │
│ │        │ renew        │ 0.6   │ Token/证书场景       │        │
│ │        │ rotate       │ 0.5   │ 密钥轮换场景          │        │
│ │        │ reload       │ 0.4   │ 配置/缓存场景         │        │
│ └────────┴─────────────┴───────┴──────────────────────┘        │
│                                                                 │
│ if len(terms) >= 3 → 返回前 10 个，不进入第二层                  │
└─────────────────────────────────────────────────────────────────┘
         │ 不足 3 个 → 继续
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 第二层: 模块→符号关联（~1ms, 覆盖 15-20%）                        │
├─────────────────────────────────────────────────────────────────┤
│ entities.module 非空? → lookup_module_code_symbols(module)      │
│                                                                 │
│ 数据来源: MODULE_SYMBOL_STATS                                    │
│   从文件路径缓存推导"哪些仓库属于哪些模块"                           │
│   → 对这些仓库轻量 ripgrep 采样（每周刷新一次）                     │
│   → 提取最高频的代码符号                                          │
│                                                                 │
│ ┌──────────┬───────────────┬───────┬──────────┐                │
│ │ 模块      │ 符号           │ 权重  │ 出现仓库数│                │
│ ├──────────┼───────────────┼───────┼──────────┤                │
│ │ 用户登录  │ login         │ 0.95  │ 12       │                │
│ │          │ authenticate  │ 0.90  │ 8        │                │
│ │          │ auth          │ 0.85  │ 45       │                │
│ │          │ oauth         │ 0.82  │ 6        │                │
│ └──────────┴───────────────┴───────┴──────────┘                │
│                                                                 │
│ 过滤: repo_count < 200（排除过于通用的符号，如 "config"）          │
│ 最终权重: weight × 0.7（模块→符号关联的置信度折扣）                │
│                                                                 │
│ if len(terms) >= 3 → 返回前 10 个，不进入第三层                  │
└─────────────────────────────────────────────────────────────────┘
         │ 仍不足 3 个 → 继续
         ▼
┌─────────────────────────────────────────────────────────────────┐
│ 第三层: LLM 翻译（~300ms 首次 / ~5ms 缓存, 覆盖 5-10%）           │
├─────────────────────────────────────────────────────────────────┤
│ llm_translate_with_validation(query, candidate_repos)           │
│ → 轻量模型（Haiku）翻译中文 → 英文标识符                          │
│ → 存在性验证（ripgrep 确认词是否在代码库中存在）                    │
│ → 缓存 24h                                                     │
│ 详细逻辑见 6.7 节                                                │
└─────────────────────────────────────────────────────────────────┘
```

### 6.5 Type D/E：需求追溯 / 人员时间锚定

这两类**不走符号搜索**，代码检索路径完全不同：

`git_log_search()` 通过 git log 定位代码变更：

```
git_log_search(entities)
════════════════════════════

输入: entities（包含 req_ids / person / time_range / version）

┌────────────────────────────────────────────────────────────┐
│ Step 1: 构造 git log 参数                                   │
│                                                            │
│   entities.req_ids 非空?                                   │
│     → git log --grep "REQ-187" --grep "REQ-188"            │
│        (多个需求ID用多个 --grep，OR 关系)                     │
│                                                            │
│   entities.person 非空?                                    │
│     → git log --author "张三"                              │
│                                                            │
│   entities.time_range 非空?                                │
│     → git log --since "2026-05-29" --until "2026-06-07"    │
│                                                            │
│   entities.version 非空?                                   │
│     → git log <branch_or_tag>                              │
├────────────────────────────────────────────────────────────┤
│ Step 2: 执行 git log → 获取变更文件列表                       │
│                                                            │
│   零命中 → 返回空 SearchResult + 说明                        │
├────────────────────────────────────────────────────────────┤
│ Step 3: 对变更文件精确读取                                    │
│   lookup_code_metadata(repo, file_path, line)              │
│   最多 20 个文件                                             │
├────────────────────────────────────────────────────────────┤
│ 输出: SearchResult(primary=results, method="git_log",      │
│                    changed_files=changed_files)            │
└────────────────────────────────────────────────────────────┘
```

### 6.6 搜索词打分与截断

不管走哪条路径，最终都会产出一个候选词列表。最后一步是**排序 + 截断**，控制在 10 个以内。

`score_and_truncate()` 的打分因子与处理流程：

```
score_and_truncate(terms, query_type) → SearchTermSet
═══════════════════════════════════════════════════════

[1] 基础分: term.base_weight（0.3 ~ 1.0，来自上游路径的置信度）

[2] 因子 1 — 符号长度（替代旧版 IDF，更简单且同样有效）:
    len(term.text) <= 2     → score × 0.2   （"id","no"极短词区分度低）
    len(term.text) >= 8     → score × 1.15   （长符号名更具体）
    3 <= len <= 7           → score 不变

[3] 因子 2 — 热点符号加分:
    term.text 在 HOTSPOT_SYMBOLS 中 → score × 1.1
    （高频出现在 commit log 中的符号，用户更可能想找）

[4] 排序 + 去重 + 截断:
    ├─ 按 final_score 降序排列
    ├─ remove_substring_duplicates(): "auth" 和 "authenticate" → 保留 "authenticate"
    │   （更长的符号更具体，覆盖短符号的情况）
    └─ 截断到前 10 个

[5] 输出分组:
    SearchTermSet:
    ├─ exact_terms:  [用于精确匹配的搜索词]
    ├─ fuzzy_terms:  [用于模糊匹配的搜索词]
    └─ tag_terms:    [用于标签匹配的搜索词]
```

| 因子 | 条件 | 系数 | 设计原理 |
|------|------|------|---------|
| 来源可信度 | base_weight | 0.3~1.0 | 用户给的高于同义词表高于 LLM |
| 符号特异性 | len ≤ 2 字符 | ×0.2 | 极短词区分度极低，几乎不参与 Phase 1 |
| 符号特异性 | len ≥ 8 字符 | ×1.15 | 长符号名天然精确，少量加分即可 |
| 热点加权 | term 在 HOTSPOT_SYMBOLS | ×1.1 | 热门符号更可能是用户关心的核心逻辑 |
| 子串去重 | "auth" ⊂ "authenticate" | 保留长的 | 长符号覆盖短符号的语义 |

### 6.7 LLM 翻译的工程化

同义词表覆盖不到时，LLM 翻译是兜底。关键不是翻译质量，而是**缓存策略**。

**三级缓存架构：**

```
LLMTranslationCache 查询流程
══════════════════════════════

查询: llm_translate_to_identifiers("用户登录的 OAuth token 刷新")

┌──────────────────────────────────────────────────────────────┐
│ L1: 精确缓存（exact_cache，永久有效）                           │
│   key = MD5(query原文)                                       │
│   命中 → 返回缓存的 terms，延迟 ~0ms                           │
│   未命中 → 进入 L2                                            │
└──────────────────────────────────────────────────────────────┘
         │ 未命中
         ▼
┌──────────────────────────────────────────────────────────────┐
│ L2: 规范化缓存（ttl_cache，24h TTL）                           │
│                                                              │
│   normalize_query() 规范化策略:                                │
│   输入: "用户登录的 OAuth token 刷新"                           │
│   ├─ 去标点: re.sub(r'[，。！？、的了在吗哪是]', ' ', query)  │
│   ├─ 统一空格 + 小写                                          │
│   └─ 排序: sorted(set(tokens))                               │
│   输出: "oauth token 刷新 用户登录"     ← 不同表述映射到同一 key │
│                                                              │
│   key = MD5(normalized_query)                                │
│   命中 → 返回缓存的 terms，延迟 ~0ms                           │
│   未命中 → 进入 L3                                            │
└──────────────────────────────────────────────────────────────┘
         │ 未命中
         ▼
┌──────────────────────────────────────────────────────────────┐
│ L3: LLM 调用（首次 ~300ms）                                    │
│                                                              │
│   触发条件（全部满足才调）:                                     │
│   ├─ 同义词映射表未覆盖                                        │
│   ├─ query 长度 > 10 字                                      │
│   └─ 不含英文词                                               │
│                                                              │
│   TRANSLATION_PROMPT → Haiku → 解析响应 → 写入 L1+L2 缓存      │
│                                                              │
│   翻译要求:                                                    │
│   ├─ 输出英文标识符，逗号分隔，最多 8 个                          │
│   ├─ 优先函数名级别（如 token_refresh），而非笼统词（如 data）     │
│   ├─ 考虑命名约定: snake_case, camelCase, PascalCase          │
│   └─ 体现原文中的具体技术（OAuth, Redis, Kafka）                 │
└──────────────────────────────────────────────────────────────┘
```

**缓存写入：** LLM 调用成功后，同时写入 exact_cache（永久）和 ttl_cache（24h）。缓存命中率预计 60-70%，且随系统使用逐渐提升。

### 6.7.2 翻译结果的存在性验证

LLM 翻译的标识符可能"合理但不存在"——LLM 不知道代码库的实际命名。例如用户问"削峰填谷"，LLM 翻译为 `peak_shaving`，但实际代码用的是 `traffic_smoothing`。

**方案：翻译后做一次极轻量的 ripgrep 存在性预检。**

不是完整搜索——只统计匹配文件数（`rg -c --max-count 1`），不返回内容。10 个词并行检查，延迟预算 ~50ms。

```
validate_translation_existence(terms, candidate_repos)
══════════════════════════════════════════════════════

核心命令: rg -c --max-count 1 'pattern' repo1 repo2 ...
  -c: 只输出每个文件的匹配行数（不输出内容）
  --max-count 1: 每个文件命中 1 行就停（加速）

对每个 term 并行执行:
  ┌─ 命中（match_count > 0）: weight × 1.05, existence_verified=True
  ├─ 无匹配（match_count=0）: weight × 0.15, existence_verified=False
  └─ 超时（>50ms）:            weight 不变, existence_verified=None
```

**"翻译 → 验证 → 反馈 → 重翻译"闭环流程：**

```
llm_translate_with_validation(query, candidate_repos)
═══════════════════════════════════════════════════════

┌──────────────────────────────────────────────────────────┐
│ Step 1: LLM 翻译                                          │
│   raw_terms = llm_translate_to_identifiers(query)        │
│   如: "削峰填谷" → ["peak_shaving","valley_filling",...]  │
└──────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│ Step 2: 存在性验证（并行，~50ms）                           │
│   validate_translation_existence(raw_terms, repos)       │
│   → 每个 term 在候选仓库中 rg -c --max-count 1           │
└──────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────┐
│ Step 3: 分支决策                                          │
│                                                          │
│   existing（确认存在）>= 2?                               │
│     → 返回 existing[:8]，不重翻译                          │
│                                                          │
│   existing == 1?                                         │
│     → 返回 existing + unverified[:8]，不重翻译             │
│                                                          │
│   existing == 0 AND nonexistent 非空?                     │
│     → 全部翻译词不存在！进入重翻译闭环                      │
└──────────────────────────────────────────────────────────┘
         │ 全部不存在
         ▼
┌──────────────────────────────────────────────────────────┐
│ Step 4: 重翻译（~300ms）                                   │
│                                                          │
│   RETRANSLATE_PROMPT:                                    │
│   ├─ 告知 LLM 哪些词不存在: ✗ peak_shaving ✗ valley_...  │
│   ├─ 告知目标仓库: repos = "credit-service, billing-..."  │
│   └─ 要求换思路: 考虑同义词、缩写、不同命名习惯             │
│                                                          │
│   llm_retranslate_with_feedback() → 新词列表               │
│   如: "削峰填谷" → ["traffic_smoothing","load_balance",...]│
│                                                          │
│   新翻译词 weight=0.4（低于首次翻译的 0.5）                 │
└──────────────────────────────────────────────────────────┘
```

**延迟分析：**

| 路径 | 延迟 | 发生率 |
|------|------|--------|
| 缓存命中 + 全部存在 | ~55ms（+50ms vs 原方案） | ~60% |
| 首次翻译 + 全部存在 | ~350ms（+50ms vs 原方案） | ~35% |
| 全部不存在 → 重翻译 | ~650ms | ~5% × 10% = 0.5% |

15% 的延迟增加换来"不搜不存在的词"——一个不存在的搜索词浪费的不止 50ms：它浪费 ripgrep 时间、占用搜索词位、挤掉可能存在的高价值词。

**与搜索词构造管线的集成：** 6.4 节第三层 LLM 翻译调用 `llm_translate_with_validation` 替代原 `llm_translate_to_identifiers`。存在性验证需要候选仓库列表（Phase 0 路由结果）——如果路由也失败（无候选仓库），跳过验证，直接返回 LLM 翻译结果做全量搜索。

### 6.8 反馈闭环：从成功搜索中学习

让系统越用越好的关键机制。

**“成功”信号的定义（多种信号组合）：**

| 信号 | 说明 | 权重 |
|------|------|------|
| 用户点击结果 + 停留 > 30 秒 | 强正面信号 | 高 |
| 用户复制了搜索结果中的代码片段 | 明确的成功 | 最高 |
| 同一 session 中未改搜索词 | 结果足够好 | 中 |
| 用户明确点赞/点踩 | 直接反馈（如 UI 支持） | 最高 |

**反馈闭环处理流程：**

```
SearchTermFeedbackLoop
═══════════════════════

┌──────────────────────────────────────────────────────────────┐
│ 记录阶段（每次搜索后触发）                                      │
├──────────────────────────────────────────────────────────────┤
│ record_search(query, search_terms, candidate_repos,           │
│               clicked_results, session_id)                    │
│                                                              │
│ 写入 search_feedback_log 表:                                  │
│   字段: query, search_terms(JSON), candidate_repos(JSON),    │
│         clicked(JSON), session_id, timestamp                  │
└──────────────────────────────────────────────────────────────┘
                              │
                              ▼ 离线批处理（每周）
┌──────────────────────────────────────────────────────────────┐
│ 挖掘阶段: mine_new_mappings()                                 │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│ 1. 查询近 7 天有 clicked 记录的反馈日志                        │
│                                                              │
│ 2. 对每条记录:                                                │
│    ├─ extract_chinese_spans(query) → 中文片段                 │
│    ├─ clicked_terms ∩ search_terms → 被点击的搜索词            │
│    └─ 被点击的词不在原始 query 中 → 这是系统生成的翻译          │
│                                                              │
│ 3. 发现模式:                                                  │
│    用户搜 "额度校验"                                           │
│    → search_terms = ["credit_limit", "quota_check"]          │
│    → 用户点击了 credit_limit                                  │
│    → 自动学习: "额度校验" → "credit_limit" (新增, weight=0.5)  │
│                                                              │
│ 4. 写入 CODE_SYNONYM_MAP（source="feedback"）                 │
│                                                              │
│ 5. 权重衰减: 已有映射项连续 3 周未被点击 → 降权 0.1            │
└──────────────────────────────────────────────────────────────┘
```

### 6.8.2 被动学习通道：从代码仓库自动挖掘中英映射

现有反馈闭环（6.8）依赖用户点击行为来学习新映射。但上线初期存在**冷启动死循环**：映射表不准确 → 搜索结果差 → 用户不点击 → 反馈信号弱 → 映射表无法改进。

**核心洞察：代码仓库本身就是一个巨大的中英对照语料库。** 不需要等用户来点击——commit message、repo 元数据、代码注释中天然包含了大量"中文语义 ↔ 英文标识符"的对照信息。这些信息是**白捡的**，不需要任何用户交互。

#### 管道 P1：commit message 挖掘（信号最强）

开发者写 commit message 时用中文描述"做了什么"，而 changed files 列表天然给出了对应的英文标识符。这是三条管道中信号最强、噪音最低的——commit message 本质上是人类做的"意图→代码"映射标注。

**CommitMessageMiner 处理流程：**

```
mine_from_commits(repos, since="7 days ago")
════════════════════════════════════════════════

执行频率: 每日凌晨离线批处理
数据规模: 500 仓库，日均 ~200 commit，月 ~6000 候选

┌──────────────────────────────────────────────────────────────┐
│ Step 1: 扫描 commit log                                       │
│   for repo in repos:                                         │
│     git log --since="7 days ago" --format="%H|%s" --name-only│
│     → 获取每个 commit 的 message + changed_files              │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 2: 提取中文动作/对象片段                                   │
│                                                              │
│   _extract_action_spans(message):                            │
│     输入: "修复额度校验超时问题，优化了缓存策略"                   │
│     jieba 分词 → 停用词过滤（修复/优化/新增/删除/的/了...）      │
│     输出: ["额度校验", "超时", "缓存"]                          │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3: 从 changed_files 提取英文词干                          │
│                                                              │
│   _extract_stems(file_path):                                 │
│     输入: "src/credit/credit_limit_validator.py"              │
│     按 _ - 切分 + 驼峰边界识别                                 │
│     输出: ["credit", "limit", "validator"]                    │
│     过滤: 长度 < 3 的词干                                      │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 4: 聚合 + 去噪 + 置信度打分                               │
│                                                              │
│   _aggregate_and_filter(raw_mappings):                       │
│                                                              │
│   规则 1（最小出现次数）: 至少 2 次 → 过滤巧合                    │
│   规则 2（长度门槛）: 中文 ≥ 2 字 AND 英文 ≥ 4 字符              │
│   规则 3（通用词黑名单）: 过滤 test/main/index/init/config/      │
│          util/helper/common/base/model/service/controller/    │
│          handler/manager                                      │
│                                                              │
│   置信度公式:                                                 │
│     confidence = min(0.9, 0.3 + 0.1 × occurrence_count       │
│                               + 0.1 × unique_repos)           │
│                                                              │
│   示例:                                                       │
│     ("额度校验", "credit_limit") 出现在 5 个不同仓库的 12 个    │
│       commit 中 → confidence = 0.3+1.2+0.5 = 0.9 (上限)      │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 5: 映射生效                                               │
│   confidence >= 0.7 → 自动写入 CODE_SYNONYM_MAP               │
│   写入时: weight = confidence × 0.7, source = "commit_mined"   │
│   每月汇总邮件通知新增映射                                       │
└──────────────────────────────────────────────────────────────┘
```

**映射生效路径：** CommitMessageMiner 每日凌晨产出候选映射 → 置信度 ≥ 0.7 的自动写入 `CODE_SYNONYM_MAP`（weight=confidence × 0.7，source="commit_mined"）→ 不需要人工审核。每月汇总邮件通知新增映射。

#### 管道 P2：仓库元数据挖掘

利用 3.6 节 `repo_registry` 表自带的 display_name、description、tags：

**RepoMetadataMiner 处理流程：**

```
mine_from_registry()
═══════════════════

数据来源: repo_registry 表（~500 行）

┌──────────────────────────────────────────────────────────────┐
│ 对每个仓库:                                                    │
│                                                              │
│ 1. 提取仓库名的英文词干:                                        │
│    repo_name = "credit-service"                              │
│    → _extract_stems() → ["credit", "service"]                │
│    → 过滤: len >= 3                                          │
│                                                              │
│ 2. 对 display_name + description 做中文分词:                   │
│    display_name = "额度校验服务"                               │
│    description = "处理额度校验、额度冻结、额度解冻"               │
│    → jieba.cut() → 过滤长度 < 2 的词                          │
│    → ["额度", "校验", "服务", "处理", "冻结", "解冻"]           │
│                                                              │
│ 3. 对每个中文词 × 英文词干 → 候选映射:                          │
│    "额度校验" × "credit" → (chinese="额度校验", english="credit",│
│                              source="repo_metadata",          │
│                              confidence=0.5)                  │
│                                                              │
│ 4. tags 增强映射:                                              │
│    tags = ["额度管理", "风控", "校验"]                          │
│    若 tag 含中文 → 与英文词干配对，confidence=0.6              │
│                                                              │
│ 5. 聚合去重（同一组出现多次 → 提升置信度）                       │
└──────────────────────────────────────────────────────────────┘
```

#### 管道 P3：代码注释挖掘（远期可选）

从代码注释中挖掘中文→函数名映射（如 `# 额度校验` → `check_credit_limit`），语料来源为 TreeSitter 提取的 docstring。实现思路与 P1 类似，仅语料来源不同，当前阶段不展开。

#### 预期效果

| 指标 | 仅反馈闭环 | + 被动学习通道 |
|------|-----------|-------------|
| 冷启动映射数 | ~50 条 | ~50 + 首日 ~30 条 |
| 上线 1 周 | ~55 条 | ~120 条 |
| 上线 1 月 | ~80 条 | ~300 条 |
| 覆盖率达 85% | ~3 个月 | ~1 个月 |
| 覆盖率达 90% | ~6 个月 | ~2 个月 |

**关键：** 被动管道不需要等用户交互。代码仓库每天在 commit，每天就有新的中英对照数据。第一天跑就能从历史 commit 中挖出 ~30-50 条高置信度映射，直接跨越冷启动死循环。

> **反馈闭环 vs 被动学习的分工：** 反馈闭环（6.8）覆盖"用户用的口语/简称"（如"挂了"→"服务不可用"），这些词不会出现在 commit message 中。被动学习覆盖"开发者用的业务术语"（如"额度校验"→credit_limit），这些词不会出现在用户口语中但大量存在于工程记录中。两者互补，不是替代。

### 6.9 完整管线

`build_search_terms()` 将以上所有策略串联为完整管线：

```
build_search_terms(query, entities) → SearchTermSet
════════════════════════════════════════════════════

输入: query（原始查询）, entities（Supervisor 实体）
输出: SearchTermSet{ exact_terms, fuzzy_terms, tag_terms }
延迟预算:
  Type A（精确引用）: < 5ms
  Type B（中英混合）: < 10ms
  Type C（纯中文）:   < 15ms（95%）, ~300ms/5ms 缓存（5%）
  Type D/E（需求/人员）: 不经过此管线

┌──────────────────────────────────────────────────────────────┐
│ Step 1: 查询类型检测                                          │
│   query_type = detect_query_type(entities, query)             │
│                                                              │
│   REQ_TRACE / PERSON_TIME → 返回空 SearchTermSet（走 git log）│
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 2: 按类型生成候选词                                       │
│                                                              │
│   EXACT_REFS:                                                │
│     raw_terms = expand_exact_refs(entities.code_refs)        │
│     + 补充中文部分翻译（如 query 同时含中文描述）                │
│                                                              │
│   MIXED_CN_EN:                                               │
│     raw_terms = extract_mixed_query(query, entities)         │
│                                                              │
│   PURE_CN / PURE_EN:                                         │
│     raw_terms = translate_pure_cn_query(query, entities)     │
│                                                              │
│   每条路径都产出 Term 列表（含 weight, source）                  │
└──────────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│ Step 3: 分数计算 + 截断                                        │
│   return score_and_truncate(raw_terms, query_type)           │
│   → SearchTermSet:                                           │
│     ├─ exact_terms:  [用于精确匹配的最高置信度词]               │
│     ├─ fuzzy_terms:  [用于模糊匹配的中置信度词]                 │
│     └─ tag_terms:    [用于标签匹配的词]                        │
└──────────────────────────────────────────────────────────────┘
```

### 6.10 效果预期

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

### 7.2 去掉文件路径缓存（退回全量 ripgrep）

如果将第三章的文件路径缓存去掉，退回对 500 个仓库全量执行 ripgrep：

| 场景 | 影响 |
|------|------|
| 每次查询的 IO | 从 5-8 次 ripgrep 变为 500 次，延迟从 ~200ms 变为 3-8s |
| 新仓库上线 | 自动覆盖（ripgrep 扫全部），但代价是每次都扫全部 |
| CPU 开销 | 500 路并行 ripgrep 对服务器压力远大于 5-8 路 |

文件路径缓存的维护成本极低（git ls-files），却能减少 98% 的搜索范围——这是用最低成本换取最大收益的优化。

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

## 九、Agent Worker 输出格式 & 回滚机制

### WorkerOutput 格式

Code Agent 返回给 Supervisor 的输出遵循标准 WorkerOutput 格式：

```python
class WorkerOutput:
    worker_type: str = "code"
    result_count: int          # 返回的代码文件数
    results: list[dict]        # 代码匹配列表
    citations: list[Citation]  # 每条结果的引用元数据
    confidence: float          # Agent自评信心 (0-1)
    has_exact_match: bool      # 是否命中 code_refs 精确匹配
    rounds_used: int           # 使用的检索轮数
    original_query: str        # 原始检索query
```

### 回滚机制

Code Agent 有独立 feature flag，可秒级回退到单次 ripgrep+AST 模式：

```yaml
agents:
  code_agentic: false  # false=ripgrep+AST单次, true=agentic完备度判断+多轮
```

**回滚触发：** 虚假信心率 > 15% OR P99 延迟恶化 > 30% OR Token 成本恶化 > 50%。
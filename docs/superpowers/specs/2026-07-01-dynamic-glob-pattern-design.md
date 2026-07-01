# Design:CodeExplorer `_glob` 动态 pattern 推断

| 字段 | 值 |
| ------ | ----- |
| 日期 | 2026-07-01 |
| 类型 | 局部改造（CodeExplorer `_refine_terms` + `_glob`） |
| 关联 spec | `2026-07-01-code-explorer-reflection-layer-design.md`（reflection 层） |
| 关联代码 | `src/spma/agents/code/explorer.py:235`（硬编码 `**/*.py`） |
| 关联 spec | `2026-07-01-code-agent-routing-and-exploration-design.md`（route 层） |
| 脑暴模式 | 用户问题驱动（**单点修复**，5 节逐节确认） |

---

## 0. 评估摘要

### 0.1 用户原始诉求

> `explore.py#235` 行，匹配模式写死了，这不合理吧？要探索的源码项目不一定是 python，也可能是 java 项目，并且匹配模式也不一定是 `**/*` 应该是根据用户请求意图动态的调整的。请参考 claude code 的实现方式（claude code 源码地址：https://github.com/NanmiCoder/cc-haha.git）

### 0.2 现状

- `src/spma/agents/code/explorer.py:235` `_glob` 写死 `await self._executor.glob_files("**/*.py", state.candidate_repos)`
- `RipgrepExecutor.glob_files(pattern: str, ...)`（`searcher.py:229`）已接受任意 pattern，但调用方未利用
- `_refine_terms`（`explorer.py:189`）每轮 LLM 调用已存在
- `term_builder.MODULE_SYNONYMS`（`term_builder.py:15`）提供中文→英文代码关键词映射

### 0.3 脑暴产出关键事实

| 维度 | 决策 |
| ------ | ------ |
| 决策主体 | **LLM 主导**（仿 claude code Glob tool） |
| 重算频率 | **每轮都重算**（并入 `_refine_terms`，不增加 LLM 调用次数） |
| 输出 schema | **`glob_patterns: list[str]`**（多 pattern 并联） |
| 失败兜底 | **query 词法抽扩展名 → `**/*.*` 泛底** |
| Pattern 粒度 | **全仓库共享一套 patterns**（不按 repo 区分） |
| 选定方案 | **方案 B：A + schema 校验 + 多层降级 + 完整测试** |

### 0.4 第一性原理 & 奥卡姆剃刀

**第一性原理**：`_glob` 是 code agent 的"按文件名发现"步骤，与 `_grep`（按内容发现）互补。当前硬编码 `**/*.py` 实际是 **Python-only 假设**——属于设计遗物（项目早期只探 Python 仓）。当 `candidate_repos` 包含 Java/Go/TS/Rust 仓时，这个假设会立即破坏探索质量（`glob_files` 返回 0 hits → `_glob` 实际无用 → 全部依赖 `_grep`）。

**奥卡姆剃刀**：改动局限在 4 个文件、7 个改动点（详见 §2），不引入新依赖、不破坏状态机、不增加 LLM 调用次数。

---

## 1. 背景与动机

### 1.1 目标

让 CodeExplorer 的文件名发现（glob）步骤：
1. **语言无关**：候选仓库是 Java 时自动用 `**/*.java`，是 Go 时用 `**/*.go`
2. **意图相关**：用户问"YAML 配置"时定位 `**/*.yaml`，问"测试文件"时定位 `**/test_*.py` 或 `**/Test*.java`
3. **不破坏现有保证**：保留 7 mode 收敛判定、6 阶段顺序、reflection 层 cap 机制

### 1.2 参考实现（claude code Glob tool）

claude code 把 `Glob` 暴露为独立 tool（`pattern` + `path` 两个参数），让 LLM 在 ReAct 循环中自由选择 pattern。本设计借鉴其"LLM 主导决策"的思路，但**不采用其 tool-first 架构**——这与最近 spec `2026-07-01-code-explorer-reflection-layer-design.md` 选定的 **方案 B（保留状态机）** 冲突，会推翻 12 commits 的 reflection 层工作。本设计把 LLM 决策内化进 `_refine_terms`，**复用现有 LLM 调用**实现等效效果。

---

## 2. 架构（总览）

### 2.1 改动位置

所有改动局限在 code agent 内部，不污染 `LangGraph graph.py`、不污染 `RepoRegistry`、不污染 `RipgrepExecutor.glob_files` 签名。

```
┌─────────────────────────────────────────────────────┐
│ CodeExplorer._run_one_round                          │
│                                                       │
│  ┌────────────────────────────────────────────┐      │
│  │ _refine_terms(state)                        │      │
│  │   ① round=1 退化: query+entities → defaults│      │
│  │   ② 后续轮: LLM.ainvoke(prompt)             │      │
│  │   ③ 解析 JSON: {exact, fuzzy, tag, globs}   │      │
│  │   ④ validate_glob_pattern(llm_globs)        │      │
│  │   ⑤ 兜底: extract_extensions_from_query()  │      │
│  │   ⑥ 终极兜底: ["**/*.*"]                    │      │
│  │   ⑦ state.search_terms["glob_patterns"] = … │      │
│  │   ⑧ state.glob_patterns_resolved = "llm"    │      │
│  └────────────────────────────────────────────┘      │
│                       │                              │
│                       ▼                              │
│  ┌────────────────────────────────────────────┐      │
│  │ _glob(state)                                │      │
│  │   for pattern in state.search_terms[        │      │
│  │       "glob_patterns"]:                     │      │
│  │     await self._executor.glob_files(        │      │
│  │         pattern, state.candidate_repos)     │      │
│  │   合并去重 (repo, file_path)                │      │
│  └────────────────────────────────────────────┘      │
│                                                       │
└─────────────────────────────────────────────────────┘
```

### 2.2 不动的部分（约束清单）

- ❌ `RipgrepExecutor.glob_files(pattern)` 签名（已接受任意 pattern，正好用上）
- ❌ `ExplorerState` 现有字段（新增 1 个 `glob_patterns_resolved: str`，不破坏 dataclass 兼容）
- ❌ `_run_one_round` 6 阶段顺序与状态机
- ❌ `graph.py` / `build_code_agent_graph`
- ❌ `RepoRegistry` 注入（按脑暴 Q4 选择，零依赖）
- ❌ LLM 调用次数（spec §9 预算 8 次/查询不变）

### 2.3 为什么这样切

- **职责清晰**：`term_builder`（已存在）管关键词 → 扩到管 glob；`_glob` 只管执行
- **状态机零侵入**：把 glob 决策塞进 `_refine_terms` 复用现有 LLM 调用，不破坏 reflection 层
- **可降级**：3 层降级 + 1 个状态字段记来源，运行时可观测

---

## 3. 组件边界

### 3.1 改动清单

| # | 文件 | 组件 | 类型 | 职责 |
|---|------|------|------|------|
| 1 | `term_builder.py` | `extract_extensions_from_query()` | 纯函数（新） | 从 `query` 抽 `\.(java\|py\|go\|ts\|js\|rs\|kt\|swift\|rb\|php\|c\|cpp\|cs\|scala\|sh\|yaml\|yml\|json\|xml\|md\|sql)` 扩展名；返回 `list[str]` 形式 `**/*.<ext>` |
| 2 | `term_builder.py` | `validate_glob_pattern()` | 纯函数（新） | 单个 pattern 校验：必须以 `**/*` 开头、含文件通配片段（`*.<ext>` 或 `**`）、不含路径穿越（`..`）、不含命令注入字符（`; \| & $ \` \n`）。合法返回 True |
| 3 | `state.py` | `SearchTermSet` | 类型扩展 | 仅文档化新字段 `glob_patterns: list[str]`（dict 已经是 duck-typed，无需运行时 schema 改动） |
| 4 | `explorer.py` | `ExplorerState.glob_patterns_resolved` | 状态字段（新） | 字符串枚举 `"llm" \| "fallback_query" \| "fallback_wildcard"`，记录本轮实际命中的 pattern 来源 |
| 5 | `prompts.py` | `_refine_terms` prompt | prompt 模板（改） | JSON 输出 schema 增 `glob_patterns: list[str]`；instruction 告诉 LLM "按用户意图和上轮结果推断应该 glob 哪些文件类型/路径" |
| 6 | `explorer.py` | `_refine_terms()` | 方法（改） | 6 步骤流水线：LLM 返回 → `validate_glob_pattern` 过滤 → 全空时降级到 `extract_extensions_from_query` → 仍空用 `["**/*.*"]`；写 `state.search_terms["glob_patterns"]` 和 `state.glob_patterns_resolved` |
| 7 | `explorer.py` | `_glob()` | 方法（改） | 替换 `glob_files("**/*.py", ...)` 为 `for p in patterns: await glob_files(p, ...)`；合并去重 `(repo, file_path)` |

### 3.2 公共接口

```python
# term_builder.py
def extract_extensions_from_query(query: str) -> list[str]:
    """Return glob patterns inferred from explicit extensions in query.
    Example: '...Java 服务... Spring 配置' → ['**/*.java', '**/*.xml']
    """
    ...

def validate_glob_pattern(pattern: str) -> bool:
    """Validate a single glob pattern is safe to pass to ripgrep --files.
    Rejects: path traversal (../), shell metacharacters, malformed.
    """
    ...
```

```python
# state.py — 文档化
class SearchTermSet(dict):
    """.. existing fields ..
    glob_patterns: list[str]   # file matching patterns from LLM or fallback
    """
```

```python
# explorer.py — ExplorerState 新字段
@dataclass
class ExplorerState:
    # ... existing fields ...
    glob_patterns_resolved: str = ""  # "llm" | "fallback_query" | "fallback_wildcard"
```

```python
# explorer.py — _refine_terms 新增逻辑（伪代码）
llm_patterns = parsed.get("glob_patterns", []) or []
valid = [p for p in llm_patterns if validate_glob_pattern(p)]
if valid:
    state.search_terms["glob_patterns"] = valid
    state.glob_patterns_resolved = "llm"
else:
    fallback = extract_extensions_from_query(state.query)
    if fallback:
        state.search_terms["glob_patterns"] = fallback
        state.glob_patterns_resolved = "fallback_query"
    else:
        state.search_terms["glob_patterns"] = ["**/*.*"]
        state.glob_patterns_resolved = "fallback_wildcard"
```

```python
# explorer.py — _glob 新增逻辑
async def _glob(self, state: ExplorerState) -> list[dict]:
    patterns = state.search_terms.get("glob_patterns") or ["**/*.*"]
    seen, merged = set(), []
    for p in patterns:
        try:
            for hit in await self._executor.glob_files(p, state.candidate_repos):
                key = (hit["repo"], hit["file_path"])
                if key not in seen:
                    seen.add(key)
                    merged.append(hit)
        except Exception as e:
            logger.warning(f"_glob pattern={p} failed: {e}")
    return merged
```

### 3.3 依赖方向（无环）

```
explorer.py  ──→  term_builder.py  ──→  (无内部依赖，纯函数)
   │
   ├──→  RipgrepExecutor.glob_files (现有，未改)
   └──→  prompts.py (现有，改 1 个 prompt 模板)
```

- `term_builder` 是叶子节点（无 spma 内部依赖），单测完全独立
- `explorer.py` 是上层消费者，单测需要 mock `RipgrepExecutor`
- `prompts.py` 是字符串模板，单测只校验字符串包含特定 JSON key

---

## 4. 数据流（3 个 trace）

### 4.1 Trace 1：正常路径（LLM 返回合法 patterns）

```
用户 query: "查找 Spring Boot 的用户认证 Controller"
                ↓
_round=1, _refine_terms:
  ① expanded_context 为空 → 走"首轮退化"分支
  ② LLM 收到 prompt（含 JSON schema 要求 glob_patterns）
  ③ LLM 返回：
     {
       "exact_terms": ["@RestController", "Authentication"],
       "fuzzy_terms": ["auth", "login"],
       "tag_terms": [],
       "glob_patterns": ["**/*Controller.java", "**/security/**/*.java"]
     }
  ④ validate_glob_pattern 过滤 → 2 个全合法
  ⑤ 写 state.search_terms["glob_patterns"] = 2 个
  ⑥ 写 state.glob_patterns_resolved = "llm"
                ↓
_glob:
  for p in ["**/*Controller.java", "**/security/**/*.java"]:
      await glob_files(p, candidate_repos)
  合并去重 → [{repo:"backend", file_path:"src/main/java/.../UserController.java"}, ...]
                ↓
_grep / _read / _expand_via_ast / _assess 继续正常流
```

**关键不变量**：`state.glob_patterns_resolved == "llm"` → 埋点可统计"LLM 决策命中率"。

### 4.2 Trace 2：LLM 失败（异常 / 返回空 / 全部非法）

```
用户 query: "查询订单服务相关代码"
                ↓
_round=2（反思后第二轮）, _refine_terms:
  ② LLM 调用超时（5s timeout）
  ③ 现有 except 分支已捕获：logger.warning，保持上轮 search_terms
  ④ 但 state.glob_patterns_resolved 仍需更新
                ↓
  ★ 关键设计点：refine 失败的兜底链
  ┌──────────────────────────────────────────┐
  │ 上一轮 glob_patterns_resolved == "llm"?   │
  │   是 → 沿用上轮 patterns（不变）          │
  │   否 → 进入降级链                         │
  └──────────────────────────────────────────┘
                ↓ 假设上一轮也失败
  ⑤ extract_extensions_from_query("查询订单服务相关代码")
     - regex: \.(java|py|go|ts|js|...)
     - 命中：无
     - 返回: []
  ⑥ 仍空 → 用 ["**/*.*"]
  ⑦ 写 state.glob_patterns_resolved = "fallback_wildcard"
                ↓
_glob: 1 个 pattern "**/*.*" → 全部文件 → 受 _is_sensitive_path 过滤
```

**关键不变量**：
- LLM 失败不会让 `_glob` 抛异常（try/except 已在原代码）
- `glob_patterns_resolved` 永远从 3 选 1，便于打点
- 不会回到硬编码 `**/*.py`

### 4.3 Trace 3：LLM 返回但部分非法（混合 case）

```
LLM 返回:
{
  "glob_patterns": [
    "**/*.java",              ← 合法
    "**/*.py; rm -rf /",      ← 非法（含 ; 和空格）→ validate 拒绝
    "../../../etc/passwd",   ← 非法（含 .. 路径穿越）→ validate 拒绝
    ""                        ← 非法（空）→ validate 拒绝
  ]
}
                ↓
validate_glob_pattern 过滤 → 仅保留 ["**/*.java"]
                ↓
有合法结果 → 走 Trace 1 路径
state.glob_patterns_resolved = "llm"
```

**关键不变量**：
- 单个非法 pattern 不污染整个集合
- `validate_glob_pattern` 是安全门：永远不让 raw LLM 输出直接进 ripgrep

### 4.4 状态机时序图

```
round 1              round 2              round 3
┌──────────┐         ┌──────────┐         ┌──────────┐
│ LLM ok   │         │ LLM fail │         │ LLM ok   │
│ globs=[2]│         │ globs=[] │         │ globs=[3]│
│ resolved │         │ fallback │         │ resolved │
│ =llm     │         │ =wildcard│         │ =llm     │
└────┬─────┘         └────┬─────┘         └────┬─────┘
     │                    │                    │
     ▼                    ▼                    ▼
  _glob 用             _glob 用            _glob 用
  2 个 pattern         **/*.*              3 个 pattern
```

每轮的 `glob_patterns_resolved` 独立记录，**不跨轮累计**（反映"本轮决策质量"）。

---

## 5. 错误处理（错误码矩阵）

按"错误 → 检测点 → 恢复动作 → 副作用"四列展开。共 8 类错误，全部软降级（无 5xx 类失败）。

| # | 错误 | 检测点 | 恢复动作 | 副作用 |
|---|------|--------|----------|--------|
| 1 | LLM 调用超时/exception | `_refine_terms` try/except（已有） | 沿用上轮 `glob_patterns`；若上轮也无则走 #4 | `glob_patterns_resolved = "fallback_wildcard"`（若降级） |
| 2 | LLM 返回非 JSON | `_refine_terms` 的 `json.loads` 失败（已有 except） | 同 #1 | 同 #1 |
| 3 | LLM JSON 缺 `glob_patterns` 字段 | `parsed.get("glob_patterns", [])` 返回 `[]` | 直接走降级链 #4/#5 | `resolved = "fallback_*"` |
| 4 | `glob_patterns` 全非法（被 `validate_glob_pattern` 全部过滤） | `valid = []` 判定 | 调 `extract_extensions_from_query(state.query)` | 取决于 #5/#6 |
| 5 | `extract_extensions_from_query` 命中扩展名 | regex 至少 1 个 match | 用 query 抽出的 `**/*.<ext>` 列表 | `resolved = "fallback_query"` |
| 6 | `extract_extensions_from_query` 返回 `[]` | 兜底函数返回空 list | 用 `["**/*.*"]` | `resolved = "fallback_wildcard"` |
| 7 | 单个 pattern 的 `glob_files` 抛异常 | `_glob` 内层 try/except（新增） | 跳过该 pattern，记录 warning，继续下一个 | 该 pattern 的结果丢弃；其他 pattern 继续 |
| 8 | 单个 pattern `glob_files` 返回 0 hits | 内层 `if not hits: continue` | 正常路径，不算错误 | 无；下一轮可能反思 |

### 5.1 validate_glob_pattern 判定标准

```python
def validate_glob_pattern(pattern: str) -> bool:
    if not pattern or not isinstance(pattern, str):
        return False
    if ".." in pattern:                    # 路径穿越
        return False
    # shell 注入字符（保守拒绝）
    if re.search(r"[;\|&\$\`\n\r]", pattern):
        return False
    # 必须是 glob：含 * 或 ?
    if not re.search(r"[\*\?]", pattern):
        return False
    # 禁止绝对路径前缀（避免 ripgrep --files 误读）
    if pattern.startswith("/") or re.match(r"^[a-zA-Z]:", pattern):
        return False
    return True
```

白名单思路而非黑名单：因为 ripgrep 接受的 glob 语法很多（`{}`、`[!...]` 等），黑名单容易漏。

### 5.2 错误处理的不可违反约束

1. **绝不**让 raw LLM 输出直接进 ripgrep（必须先过 `validate_glob_pattern`）
2. **绝不**让 `_glob` 因某个 pattern 失败而中断（内层 try/except）
3. **绝不**让 `glob_patterns_resolved` 留空（保证 3 选 1）
4. **绝不**回退到硬编码 `**/*.py`（即使 query 含 "Python"）

---

## 6. 测试

### 6.1 测试金字塔

```
        ┌──────────────────────┐
        │ 1 个 e2e/integration  │  ← 真实 LLM + 真实 repo
        ├──────────────────────┤
        │ 6 个 _refine_terms 单测 │  ← mock LLM
        │ 3 个 _glob 单测         │  ← mock RipgrepExecutor
        ├──────────────────────┤
        │ 9 个 term_builder 单测  │  ← 纯函数
        └──────────────────────┘
```

### 6.2 Unit — `term_builder.py`（9 个 case）

| Case | 输入 | 期望 |
|------|------|------|
| `test_extract_ext_chinese_java` | "查找 Spring Java 控制器" | `["**/*.java"]` |
| `test_extract_ext_multi_lang` | "Python 脚本和 Go 微服务" | `["**/*.py", "**/*.go"]`（按出现顺序） |
| `test_extract_ext_with_explicit_dot` | "改 pom.xml 依赖" | `["**/*.xml"]` |
| `test_extract_ext_yaml_or_yml` | "查看 deployment.yaml 配置" | `["**/*.yaml"]`（去重 yml 变体） |
| `test_extract_ext_no_ext` | "查一下订单服务" | `[]` |
| `test_validate_glob_valid_simple` | `**/*.py` | True |
| `test_validate_glob_valid_compound` | `**/security/**/*.java` | True |
| `test_validate_glob_reject_path_traversal` | `../../etc/passwd` | False |
| `test_validate_glob_reject_shell_injection` | `**/*.py; rm -rf /` | False |
| `test_validate_glob_reject_absolute` | `/etc/passwd`, `C:\Windows\system32` | False |
| `test_validate_glob_reject_empty_or_no_magic` | `""`, `src/main/java` | False |

### 6.3 Unit — `_refine_terms`（6 个 case，mock LLM）

| Case | LLM mock 行为 | 期望 `glob_patterns` | 期望 `resolved` |
|------|--------------|---------------------|----------------|
| `test_refine_llm_returns_valid_globs` | 返回 `["**/*.java", "**/test/*.java"]` | 2 个原样 | `"llm"` |
| `test_refine_llm_partial_invalid` | 返回 `["**/*.java", ";rm", "../x"]` | `["**/*.java"]` | `"llm"` |
| `test_refine_llm_all_invalid_query_has_ext` | 全非法；query="改 pom.xml" | `["**/*.xml"]` | `"fallback_query"` |
| `test_refine_llm_all_invalid_query_no_ext` | 全非法；query="查订单服务" | `["**/*.*"]` | `"fallback_wildcard"` |
| `test_refine_llm_missing_field` | JSON 无 `glob_patterns` 键 | 走 #3/#4 | `"fallback_*"` |
| `test_refine_llm_exception` | `llm.ainvoke` 抛 `TimeoutError` | 走兜底；上轮无则 `"fallback_wildcard"` | `"fallback_wildcard"` |

### 6.4 Unit — `_glob`（3 个 case，mock RipgrepExecutor）

| Case | 场景 | 期望 |
|------|------|------|
| `test_glob_multi_pattern_merge` | 2 个 pattern，各自返回不同 files | `glob_files` 被调 2 次，合并结果 |
| `test_glob_dedup_across_patterns` | 同一文件被 2 个 pattern 命中 | 结果去重为 1 条 |
| `test_glob_one_pattern_fails_isolation` | 第 2 个 pattern 抛 `RuntimeError` | 第 1 个结果保留；warning 日志；不抛 |

### 6.5 Integration — 端到端（1 个 case）

| Case | 场景 | 期望 |
|------|------|------|
| `test_glob_pattern_e2e_java_repo` | 真实 fixture repo（同时含 .java + .py）；mock LLM 返回 `**/*.java`；跑完整 `_run_one_round` | `glob_files` 仅以 `.java` 调用；`expanded_context` 不含 `.py`；`glob_patterns_resolved == "llm"` |

### 6.6 覆盖率目标

| 模块 | 行覆盖 | 分支覆盖 |
|------|--------|----------|
| `term_builder.py`（新增 2 个函数） | 100% | 100% |
| `explorer.py: _refine_terms`（新增 6 步流水线） | ≥ 95% | ≥ 90% |
| `explorer.py: _glob`（重写循环） | ≥ 95% | ≥ 90% |
| `prompts.py`（改 1 个 prompt） | 字符串包含 4 个 JSON key 的 assertion 即可 | n/a |

### 6.7 不测什么（显式非目标）

- ❌ 不测 `RipgrepExecutor.glob_files` 内部 ripgrep 行为（已有测试覆盖）
- ❌ 不测 LLM 真实响应（避免 e2e flaky；6 个 mock 单测足够）
- ❌ 不测 reflection 层与 glob 的交互（已有 reflection spec 覆盖）
- ❌ 不引入新的 Prometheus 指标（§5 显式非目标）

### 6.8 测试约定

- **fixture 复用**：直接用 `tests/unit/agents/code/test_explorer.py` 已有 `MockRipgrepExecutor` 模式（如果有）
- **断言风格**：与项目一致，用 `assert ... == ...`，不用 `pytest.raises(match=...)` 之外的复杂 matcher
- **测试名约定**：`test_<unit>_<scenario>_<expected>`，与项目现有命名一致

---

## 7. 显式非目标

1. **不**改变 6 阶段顺序与状态机
2. **不**增加 LLM 调用次数（仍 8 次/查询预算）
3. **不**引入新 Prometheus 指标（`glob_patterns_resolved` 字段已留好钩子，后续单独 spec）
4. **不**让 glob 变成独立 LangChain `@tool`（与 reflection 层方案 B 冲突）
5. **不**注入 `RepoRegistry` 到 CodeExplorer（脑暴 Q4 选择零依赖）
6. **不**支持 per-repo 差异化 pattern（脑暴 Q5 选择全仓共享）
7. **不**修改 `RipgrepExecutor.glob_files` 签名

---

## 8. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| LLM 持续返回非法 pattern（被 validate 全部过滤） | 低 | 中 | 走 `extract_extensions_from_query` → `**/*.*` 兜底；不影响可用性 |
| LLM 幻觉出不存在语言的扩展名 | 中 | 低 | ripgrep 返回 0 hits → `_grep` 兜底内容搜索；reflection 层会反思 |
| `extract_extensions_from_query` 正则过宽误命中 | 中 | 低 | whitelist 限制在 20+ 主流扩展名；非主流语言不会误命中 |
| 多 pattern 合并去重性能 | 低 | 低 | `_glob` 本就有早停（`seen` set）；pattern 数限制 ≤ 5（LLM prompt 约束） |
| 与 reflection 层 cap 机制冲突 | 极低 | 中 | glob 决策失败时 reflection 层会触发反思（`should_reflect` 不变）；新增的 `fallback_*` 路径不影响评估逻辑 |

---

## 9. 关键设计决策（脑暴记录）

| 决策 | 选项 | 选择 | 理由 |
|------|------|------|------|
| 决策主体 | LLM 主导 / metadata+启发式 / 纯启发式 / 混合 | **LLM 主导** | 仿 claude code Glob tool 思路；精确度最高 |
| 重算频率 | 每轮 / 仅首轮 / 独立阶段 | **每轮** | 反思后可能调整 pattern；零额外 LLM 调用 |
| 输出 schema | 同级单字段 / 列表 / 字段不变后处理 | **列表** | 多 pattern 并联覆盖面更广（同时找 src 和 test） |
| 失败兜底 | metadata / query 词法 / 全量 | **query 词法** | 零依赖；与 _refine_terms 现有逻辑一致 |
| Pattern 粒度 | 全仓共享 / per-repo 二维 | **全仓共享** | 改动最小；多语言场景下 LLM 会同时输出 `**/*.java` + `**/*.py` |
| 选定组装 | A 最小 / B+A 完整 / C tool-first | **B+A 完整** | 与 reflection 层方案 B 风格一致；不破坏 7 mode 保证 |

---

## 10. 实施清单（脑暴产出，下一步 → writing-plans 细化）

- [ ] 在 `term_builder.py` 实现 `extract_extensions_from_query()` + 9 个单测
- [ ] 在 `term_builder.py` 实现 `validate_glob_pattern()` + 测试覆盖在上述 9 个内
- [ ] 在 `state.py` 文档化 `SearchTermSet.glob_patterns` 字段
- [ ] 在 `explorer.py` `ExplorerState` 加 `glob_patterns_resolved: str = ""`
- [ ] 在 `prompts.py` 改 `_refine_terms` prompt：JSON schema + instruction
- [ ] 在 `explorer.py` `_refine_terms` 末尾接 6 步流水线（4-8）
- [ ] 在 `explorer.py` `_glob` 重写为多 pattern 循环 + 异常隔离 + 去重
- [ ] 在 `tests/unit/agents/code/test_explorer.py` 加 6 + 3 = 9 个单测
- [ ] 在 `tests/integration/code/` 加 1 个 e2e fixture
- [ ] 跑全量测试 + 覆盖率报告 ≥ 95%

---

## 11. 关联文档

- 上游：`docs/superpowers/specs/2026-07-01-code-explorer-reflection-layer-design.md`（reflection 层方案 B）
- 上游：`docs/superpowers/specs/2026-07-01-code-agent-routing-and-exploration-design.md`（route 层）
- 设计稿：`docs/designs/SPMA-design-13-industry-research-code-location.md`（design-13 6 阶段方法）
- 现有代码：`src/spma/agents/code/explorer.py`、`src/spma/agents/code/term_builder.py`、`src/spma/agents/code/searcher.py`、`src/spma/agents/code/state.py`、`src/spma/agents/code/prompts.py`
- 现有测试：`tests/unit/agents/code/test_explorer.py`

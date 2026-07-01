# CodeExplorer 反思层设计 — 中立风险评估 + 方案 B 推荐

| 字段 | 值 |
| ------ | ----- |
| 日期 | 2026-07-01 |
| 类型 | 架构评估 + 推荐方案设计 |
| 关联 spec | `2026-07-01-code-agent-routing-and-exploration-design.md` |
| 评估目标 | 中立风险评估 + 推荐（基于 brainstorming 流程） |
| 推荐方案 | **方案 B：保留状态机 + ReAct 反思层** |

---

## 0. 评估摘要

### 0.1 用户原始诉求

> 把 CodeExplorer 基于 langgraph 封装成 agent，使用"计划-ReAct 循环-反思"的 agent 架构，glob、grep、read、... 等作为它的工具挂载到它的工具集下，然后整体作为 build_code_agent_graph 的子图替换 explore_node 节点。

### 0.2 评估产出的关键事实

| 维度 | 现状（已落地 12 commits） | 用户提出的改动 |
| ------ | ------------------------ | -------------- |
| 循环位置 | `CodeExplorer.explore()` 内 `while` | ReAct 由 LangGraph 工具节点驱动 |
| 6 阶段定位 | 实例方法（`_` 前缀），按固定顺序串接 | 改为 LangChain `@tool`，LLM 自由选 |
| 收敛判定 | 7 mode（5 确定性 + 2 LLM 路径），硬保证 | LLM 决定何时 `final_answer` |
| 图结构 | 3 节点薄包装（route/explore/finalize），无循环边 | 嵌入 ReAct 子图替换 explore_node |
| LLM 调用上限 | 8 次/查询（spec §9） | 估 12+ 次/查询 |
| LangChain Tool 抽象 | **0 处** `@tool` / ToolNode / bind_tools | 新增整套 |
| 设计哲学 | "ReAct 不够用 → 下沉 StateGraph"（tech-selection §2.3） | **反向操作** |
| 测试矩阵 | 8 项 Explorer 单测 + 7 mode fixture | 全部失效需重写 |
| 代码量 | 713 行核心代码 | 估净增 250-450 行 |

### 0.3 三方案对比与推荐

| 方案 | 颗粒度 | 6 阶段 @tool | 5 mode 硬保证 | 产品代码净增 | 测试破坏 | 推翻 §2.3 决策 |
| ------ | -------- | -------------- | --------------- | -------------- | ---------- | ---------------- |
| **A：纯 ReAct 单 Agent** | 激进 | ✅ | ❌ 丢失 | ~400 行 | 高 | 是 |
| **B：保留状态机 + 反思层** ✅ 推荐 | 不暴露 | ❌ | ✅ 完整 | ~80 行 | 0 | 否 |
| **C：Plan-ReAct-Reflect 子图** | 激进 + 结构化 | ✅ | ✅ 完整 | ~250 行 | 中 | 部分 |

> 注："产品代码净增"指生产代码（不含测试代码）。方案 B 测试代码净增约 250 行（8 reflection 单元 + 8 Explorer 集成 + 1 e2e fixture）。

### 0.4 第一性原理 & 奥卡姆剃刀

**第一性原理**：CodeExplorer 真正解决的问题是"**程序化保证**不漏掉关键文件"——靠 7 mode + 强制每轮 6 阶段顺序保证。用户提出的"ReAct 化"混淆了两个层面：
1. LLM 自由决策（**当前 `_refine_terms` 已部分实现**）
2. 结构上替换为 ReAct 框架（**无证据表明能降低失败成本**）

**奥卡姆剃刀**：当前 713 行核心 + 8 项单测 + 显式哲学已简洁；ReAct 包装净增 250-400 行 + LLM 推理不可重现 = **复杂度不降反升**。

**推荐方案 B**：用最小变更（净增 ~80 行，0 破坏）回应用户真实诉求（让 LLM 主导决策），同时保护 7 mode 硬保证。

---

## 1. 背景与动机

### 1.1 用户提出的目标

通过将 CodeExplorer 替换为基于 LangGraph 的 ReAct agent，让 LLM 更自主地驱动代码探索流程，把 6 阶段方法暴露为工具集。

### 1.2 现状盘点（来自 explorer.py / graph.py / spec 等）

**CodeExplorer 核心结构**（explorer.py:248 行）：
- 6 阶段方法：`_refine_terms` / `_glob` / `_grep` / `_read` / `_expand_via_ast` / `_assess`
- `ExplorerState` dataclass（17-39 行）独立于 LangGraph state
- `explore()` 入口 `while not converged` 循环（64-68 行）
- 7 mode 收敛判定（completeness.py:142 行）

**build_code_agent_graph 拓扑**（graph.py:86 行）：
- 3 节点：`route_node` → `explore_node` → `finalize_node`
- 无循环边 / 无条件边 / 无 Send API
- `explore_node` 是 proxy：调 `CodeExplorer.explore(state)` 一次

**LangChain Tool 抽象**：
- 全文 0 处 `@tool` / `create_react_agent` / `ToolNode` / `tools_condition` / `bind_tools`

**设计哲学**（tech-selection §2.3:222）：
> "先用 `create_agent` 快速搭建每个 Worker Agent，当中某个 Agent 的循环逻辑超出 ReAct 范式的表达力时，再将该 Agent 下沉为 StateGraph 定制。"

**最近 12 commits**：已完成从"4 节点 LangGraph 循环"到"3 节点薄包装 + CodeExplorer 内部循环"的下沉。

### 1.3 现状的核心约束

- **LLM 成本红线**：spec §9 风险表明确 8 次/查询
- **`max_rounds=6` 硬上限**：设计哲学强调"程序化保证"
- **5 mode 确定性硬保证**：stuck / cap_reached / regression / goal_verified / diminishing_returns 是兜底网
- **8 项 Explorer 单测 + 7 mode fixture**：已有回归保护

---

## 2. 推荐方案（方案 B）：保留状态机 + 反思层

### 2.1 架构

```
[START] → route_node → explore_node(CodeExplorer.explore while loop) → finalize_node → [END]
                                 │
                                 └─ _refine_terms → _glob → _grep → _read → _expand → _assess
                                          ↑                              │
                                          │            ┌─── if 5 mode 未收敛 ───┐
                                          │            ▼                       │
                                          └──── _reflect_and_replan (新增) ←────┘
                                                  │
                                                  └─ LLM 1 次调用：
                                                     - 评估当前 expanded_context 缺口
                                                     - 重新生成 search_terms / 模块同义词
                                                     - 可选修改 candidate_repos
                                                  输出：state.search_terms (重写)
```

**核心思路**：在 `_assess` 判定"未收敛但本轮有进展"时（mode 1: diminishing_returns 或 mode 6: llm_judged），调一次 LLM 反思并重写 `search_terms`。若 LLM 也救不了（连续 2 次反思无进展），走硬截断。

**关键不变量**：
- ✅ 5 mode 硬判定（stuck / cap_reached / regression / goal_verified / diminishing_returns）保持原样
- ✅ `max_rounds=6` 硬上限保持
- ✅ LLM 调用上限：路由 1 + (6 轮 × 1 refine) + (反思触发时 ≤ 1 次/轮 × 反思轮数) + (assess 兜底 ≤ 1 次) = **8~10 次/查询**（vs 现状 8 次；仅反思触发的轮额外 +1 次）
- ✅ 不引入 `@tool` / ToolNode / messages reducer
- ✅ 不改动 graph.py 拓扑
- ✅ 不改动 `ExplorerState` 核心字段

**改动范围**：1 个新方法 + 1 个新 prompt 模块（含 build/parse 2 个函数）+ 1 个 pydantic schema + 17 项新测试（8 reflection 单元 + 8 Explorer 集成 + 1 e2e fixture）。

### 2.2 组件与接口

#### 新增

| 模块 | 文件 | 接口 | 职责 |
| ------ | ------ | ------ | ------ |
| `_reflect_and_replan` 方法 | `src/spma/agents/code/explorer.py` | `async def _reflect_and_replan(self, state: ExplorerState) -> None` | 调 LLM，解析响应，重写 `state.search_terms`；可能修改 `state.candidate_repos` |
| 反思 prompt 模板 | `src/spma/agents/code/prompts/reflection.py`（新建） | `build_reflection_prompt(state) -> str` | 构造 prompt：包含当前 expanded_context 摘要 + search_terms + 本轮新增文件数 |
| 反思响应解析 | `src/spma/agents/code/prompts/reflection.py` | `parse_reflection_response(llm_output) -> ReflectionDecision` | JSON 解析 + pydantic 校验 |
| `ReflectionDecision` dataclass | `src/spma/agents/code/explorer.py`（内部） | `@dataclass class ReflectionDecision: new_search_terms: dict; drop_terms: list[str]; add_repos: list[str]; reasoning: str` | 反思输出结构 |
| `should_reflect` 模式 | `src/spma/agents/code/completeness.py` | 复用 `CodeCompletenessResult.should_reflect: bool` | 当 5 mode 未收敛但仍有进展时，触发反思 |

#### 修改

| 文件 | 变更 |
| ------ | ------ |
| `src/spma/agents/code/explorer.py:CodeExplorer._run_one_round` | 在 `_assess(state)` 后，若 `state.convergence.should_reflect == True`，调 `_reflect_and_replan(state)`，再走下一轮 `_refine_terms` |
| `src/spma/agents/code/explorer.py:ExplorerState` | 加字段 `reflection_count: int = 0`、`consecutive_no_progress_reflections: int = 0` |
| `src/spma/agents/code/completeness.py:CodeCompletenessResult` | 加字段 `should_reflect: bool = False`；5 mode 判定时设置（diminishing_returns=True → should_reflect=True） |
| `docs/superpowers/specs/2026-07-01-code-agent-routing-and-exploration-design.md` §6 DoD | 加 "DoD: 反思触发时 LLM 调用 ≤ 1 次/轮；连续 2 次反思无进展强制 cap" |
| `src/spma/observability/code_metrics.py` | 新增 4 个 Prometheus 指标（见 §2.5） |

#### 删除

无。所有现有模块保留。

### 2.3 数据流

#### 反思触发与流转

```
[每轮开始]
   │
   ▼
_refine_terms(state)         ← 根据 state.expanded_context 生成初始 search_terms
   │
   ▼
_glob(state) → candidates    ← 根据 search_terms 找候选文件
   │
   ▼
_grep(state) → candidates    ← 在候选里搜关键词
   │
   ▼
_read(state, candidates)     ← 读文件，更新 state.expanded_context / seen_files / new_files_this_round
   │
   ▼
_expand_via_ast(state)       ← AST 扩展，更新 expanded_context / new_files_this_round
   │
   ▼
_assess(state)               ← 7 mode 硬判定
   │
   ├─ verdict == "converge"  ──→ break（5 mode 中 goal_verified 命中）
   │
   ├─ verdict == "cap"       ──→ break（max_rounds 命中）
   │
   ├─ verdict == "stuck"     ──→ break（previous_new_files == 0 连续 2 轮）
   │
   └─ verdict == "progress"  ──→ should_reflect 判定
                                  │
                                  ├─ False → 走下轮 _refine_terms
                                  │
                                  └─ True  → _reflect_and_replan(state)
                                              │
                                              ├─ LLM 1 次调用
                                              ├─ 解析为 ReflectionDecision
                                              ├─ 重写 state.search_terms（合并 drop + add）
                                              ├─ state.reflection_count += 1
                                              ├─ 若 new_files_this_round == 0（反思触发的那一轮累计为 0；与 §2.4 错误矩阵第 257 行一致）
                                              │    state.consecutive_no_progress_reflections += 1
                                              │    若 ≥2 → 强制 break（cap_reflect）
                                              └─ 走下轮 _refine_terms（用新 search_terms）
```

#### 关键状态变更

**ExplorerState 新增字段**：
- `reflection_count: int = 0` — 总反思次数
- `consecutive_no_progress_reflections: int = 0` — 连续无进展反思计数

**每轮 _assess 后**：
- `state.convergence: CodeCompletenessResult.should_reflect: bool` 决定是否触发反思
- `state.convergence.verdict: str`（不变）：`"converge" / "progress" / "cap" / "stuck"`

**反思输出回写**：
- `state.search_terms` ← 合并 `ReflectionDecision.new_search_terms` ∪ 原始 term ∖ `drop_terms`
- `state.candidate_repos` ← 可选追加 `ReflectionDecision.add_repos`（仅当 LLM 显式建议，且候选在 `repo_registry` 中存在）
- **不动**：`expanded_context` / `seen_files` / `ripgrep_results` / `previous_new_files`

#### LLM Prompt 数据契约

**输入（构造 prompt）**：
```python
{
  "round": 2,
  "max_rounds": 6,
  "original_query": "...",
  "entities": {"module": [...], "function": [...], "concept": [...]},
  "current_search_terms": {...},
  "expanded_context_summary": "<文件名列表 + 每文件关键 1-2 句摘要>",
  "new_files_this_round": 3,
  "previous_new_files": 5,
  "fallback_layer": 1,
  "candidate_repos": [...]
}
```

**输出（期望 LLM 返回 JSON）**：
```json
{
  "new_search_terms": {"module": ["新同义词"], "function": ["..."]},
  "drop_terms": ["已知无结果的 term"],
  "add_repos": ["仅在重定位到不同仓库时"],
  "reasoning": "本轮发现 X 缺失，疑似 Y 模块..."
}
```

**校验规则**：
- JSON 必须可解析（失败 → 跳过反思，走下轮）
- `new_search_terms` 与原 `search_terms` schema 一致（按 entities key 合并）
- `drop_terms` 必须 ∈ 原 `search_terms`（防止 LLM 删错）
- `add_repos` 必须 ∈ `repo_registry`（白名单过滤）

### 2.4 错误处理

#### 错误矩阵

| 错误源 | 失败模式 | 处理策略 | 失败后状态 |
| -------- | ---------- | ---------- | ------------ |
| LLM 调用超时 | `asyncio.TimeoutError` / `anthropic.APITimeoutError` | 跳过反思，log warning，走下轮原 search_terms | `reflection_count` 不增，`consecutive_no_progress_reflections` 不增 |
| LLM 5xx / rate limit | `anthropic.APIStatusError`（429/5xx） | 同上 + exponential backoff（最多 1 次重试），仍失败则跳过反思 | 同上 |
| JSON 解析失败 | `json.JSONDecodeError` | 跳过反思，走下轮 | 同上 + log error（含 raw output 截断 500 字符） |
| schema 违反：drop_terms 含原 set 之外 | `ValueError` | 仅丢弃违规 drop_terms，其余正常应用 | 部分应用 |
| schema 违反：add_repos 不在 registry | 静默过滤 | 仅过滤不抛错 | 仅在 registry 内 add_repos 生效 |
| 反思后 search_terms 为空 | `new_search_terms` 全空 | **强制 break**（避免下轮无搜索词） | verdict = "cap"（reason = "reflection_empty_terms"） |
| 反思后无新增文件 | `new_files_this_round == 0` | `consecutive_no_progress_reflections += 1`；若 ≥2 → **强制 break** | verdict = "cap"（reason = "reflection_no_progress"） |
| LLM 反思 reasoning 含敏感内容 | 注入攻击 | `reasoning` 字段不进入 prompt 回写，**仅 log**（truncate 200 字符） | 不影响 state |
| 连续反思 cap 触发 | `consecutive_no_progress_reflections ≥ 2` | 强制 break，不计入 `max_rounds` 之外的额外轮 | verdict = "cap" |
| max_rounds 到达 | `round ≥ max_rounds` | break（原有逻辑不变） | verdict = "cap" |

#### 错误处理原则

1. **反思失败是软错误**：LLM 不可用 / 解析失败时，**永远不阻塞主循环**。跳过反思意味着本轮用原 search_terms 重试，可能仍然命中 5 mode 收敛。
2. **反思空术语是硬错误**：空 search_terms 会让下轮 `_glob` 返回空、`_grep` 无结果，整个 round 浪费。**必须强制截断**。
3. **反思无效 cap 是程序保护**：防止 LLM 持续输出"换汤不换药"的反思（成本与无收益）。
4. **可观测性优先于完美恢复**：所有错误 log 到 `code_metrics` 指标 + 结构化日志，便于事后分析。

#### 关键防御点

- **JSON 解析容错**：尝试 `json.loads` → 失败则尝试正则提取 `{...}` 块 → 仍失败则降级跳过
- **term schema 校验**：用 pydantic `ReflectionDecision` 模型做严格校验，校验失败视为 JSON 失败
- **repo 白名单**：`add_repos` 必须 ∈ `repo_registry`（已有单例，注入即可）
- **reasoning 字段隔离**：永远不参与 state 回写，仅写入日志

### 2.5 测试策略

#### 1. 新增单元测试（`tests/agents/code/test_reflection.py`，新建）

| 测试项 | 类型 | 覆盖 |
| -------- | ------ | ------ |
| `test_reflection_prompt_builds_with_minimal_state` | 快照测试 | `build_reflection_prompt` 在最小 ExplorerState 下生成的 prompt 包含所有必需字段 |
| `test_reflection_prompt_truncates_long_context` | 边界测试 | `expanded_context_summary > 2000 字符` → 截断到 ≤ 2000 |
| `test_parse_reflection_response_valid_json` | 正常路径 | 标准 JSON 输入 → 正确 ReflectionDecision |
| `test_parse_reflection_response_invalid_json` | 错误路径 | 无效 JSON → ValueError |
| `test_parse_reflection_response_missing_fields` | 错误路径 | 缺 `new_search_terms` → ValidationError |
| `test_parse_reflection_response_extra_fields_ignored` | 容错 | pydantic `extra="ignore"` 验证 |
| `test_reflection_decision_repo_whitelist` | 边界测试 | `add_repos` 含不在 registry 的 → 静默过滤 |
| `test_reflection_decision_drop_terms_validated` | 边界测试 | `drop_terms` 含原 set 之外的 → ValidationError |

#### 2. 新增集成测试（`tests/agents/code/test_explorer.py` 扩展）

| 测试项 | 覆盖 |
| -------- | ------ |
| `test_explorer_triggers_reflection_on_diminishing_returns` | 注入 fake LLM + state 设 `new_files_this_round < previous_new_files / 2` → 验证 `_reflect_and_replan` 被调 |
| `test_explorer_skips_reflection_on_stuck` | 5 mode 中 `stuck` 触发 → 不调反思（直接 break） |
| `test_explorer_skips_reflection_on_goal_verified` | 5 mode 中 `goal_verified` → 不调反思 |
| `test_explorer_caps_after_2_no_progress_reflections` | 连续 2 次反思无新增文件 → 强制 break，verdict = "cap" |
| `test_explorer_caps_on_empty_reflected_terms` | 反思输出空 search_terms → 强制 break，verdict = "cap" |
| `test_explorer_continues_on_llm_timeout` | fake LLM 抛 TimeoutError → 走下轮原 terms，不阻塞 |
| `test_explorer_continues_on_json_parse_failure` | fake LLM 返回非 JSON → 同上 |
| `test_explorer_respects_max_rounds_with_reflection` | `max_rounds=2` + 反思触发 → 第 2 轮 break 不再反思 |

#### 3. 现有测试回归（**必须不破坏**）

| 测试文件 | 状态 |
| ---------- | ------ |
| `tests/agents/code/test_explorer.py` 8 项 Explorer 单测 | ✅ 保持通过（mock LLM 默认不返回 `should_reflect=True`） |
| `tests/agents/code/test_completeness.py` 7 mode fixture | ✅ 保持通过（`should_reflect` 是新字段，默认 False） |
| `tests/agents/code/test_graph.py` graph 拓扑测试 | ✅ 保持通过（无 graph 改动） |
| `tests/agents/code/test_searcher.py` RipgrepExecutor 测试 | ✅ 保持通过（无 searcher 改动） |

#### 4. 端到端测试（手动 + 现有 e2e）

| 测试项 | 覆盖 |
| -------- | ------ |
| 现有 e2e `test_code_agent_e2e.py` 6 个 fixture | ✅ 保持通过；**新增 1 个 fixture** `test_code_agent_e2e_with_reflection.py` 验证反思路径 |

#### 5. 可观测性指标（`src/spma/observability/code_metrics.py` 扩展）

新增 Prometheus 指标：
- `code_reflection_total` (Counter, labels: outcome={triggered/skipped/failed/capped})
- `code_reflection_duration_seconds` (Histogram)
- `code_reflection_search_terms_changed` (Counter)
- `code_reflection_consecutive_no_progress` (Gauge)

#### 测试原则

1. **反思路径与现有路径严格隔离**：所有现有 fixture 不动，新 fixture 显式设 `state.convergence.should_reflect=True`
2. **fake LLM 优先**：避免真 LLM 调用导致测试 flaky；测试里用 `FakeListLLM` 或 `FakeChatModel`
3. **回归测试 100% 保留**：8 项 Explorer + 7 mode + graph + searcher 单测全部继续跑通
4. **反思触发是显式的**：测试里通过构造特定 state 触发反思，而非依赖 LLM 行为

---

## 3. 已拒绝的方案

### 3.1 方案 A：纯 ReAct 单 Agent（最激进）

**架构**：
```
[START] → route_node → [ReAct Agent 子图] → finalize_node → [END]
                              │
                              ├─ refine_tool  (@tool)
                              ├─ glob_tool    (@tool)
                              ├─ grep_tool    (@tool)
                              ├─ read_tool    (@tool)
                              ├─ expand_tool  (@tool)
                              └─ assess_tool  (@tool)
                              循环由 ToolNode + tools_condition 驱动
                              收敛由 LLM 决定何时停止 tool_call
```

**拒绝理由**：
- ❌ **丢失 5 种确定性收敛 mode 的硬保证**（stuck / cap_reached / regression / diminishing_returns / goal_verified）→ 假收敛风险上升
- ❌ LLM 调用翻倍：6 次/查询 → 12+ 次/查询（**突破成本红线**）
- ❌ **推翻 tech-selection §2.3 决策**（"ReAct 不够用再下沉"是最近 12 commits 的设计依据）
- ❌ 8 项 Explorer 单测 + 7 mode fixture 全部失效重写
- ❌ `previous_new_files` 跨轮维护失效（stuck 判定语义丢失）
- ❌ `fallback_layer` 轮次→映射失效（搜索器 4 层降级逻辑断链）
- ❌ 代码量净增 ~400 行

### 3.2 方案 C：Plan-ReAct-Reflect 3 节点子图（结构化折中）

**架构**：
```
[START] → route_node → explore_node (3 节点子图) → finalize_node → [END]
                                   │
                                   ├─ plan_node:     1 次 LLM 生成搜索计划
                                   ├─ react_node:    6 @tool ReAct 循环
                                   └─ reflect_node:  5 mode 硬判定 + LLM 兜底
                                              └─ 未达 → 回到 plan_node
```

**拒绝理由**（相对方案 B）：
- 中等破坏：6 阶段方法要包装为 `@tool` + ToolMessage 解析
- `previous_new_files` 跨轮语义需要适配（ToolMessage 字段）
- 代码量净增 ~250 行（vs 方案 B 的 ~80 行）
- 8 项 Explorer 单测需要调整为新的子图测试（vs 方案 B 的 0 破坏）
- 部分推翻 tech-selection §2.3 决策

**保留理由**（如果用户更看重"暴露 @tool"）：
- 6 阶段 @tool 全部暴露，LLM 自由决策
- 5 mode 硬判定仍在
- 显式 plan/react/reflect 拆分，可观测性最好

---

## 4. 实施步骤（仅方案 B）

### Phase 1：核心实现（半天）

1. 在 `explorer.py` 加 `ReflectionDecision` dataclass
2. 在 `explorer.py` 加 `_reflect_and_replan` 方法
3. 在 `explorer.py:ExplorerState` 加 2 个新字段
4. 在 `completeness.py:CodeCompletenessResult` 加 `should_reflect` 字段
5. 在 `explorer.py:_run_one_round` 接入反思触发逻辑

### Phase 2：prompt 与校验（半天）

6. 新建 `src/spma/agents/code/prompts/__init__.py`
7. 新建 `src/spma/agents/code/prompts/reflection.py`（含 `build_reflection_prompt` + `parse_reflection_response`）
8. 定义 pydantic `ReflectionDecision` schema

### Phase 3：单测（半天）

9. 新建 `tests/agents/code/test_reflection.py`（8 项）
10. 扩展 `tests/agents/code/test_explorer.py`（8 项集成测试）
11. 验证现有 8 项 Explorer 单测 + 7 mode fixture 全部通过

### Phase 4：可观测性（2 小时）

12. 在 `code_metrics.py` 加 4 个 Prometheus 指标
13. 在 `_reflect_and_replan` 接入 metric 上报

### Phase 5：e2e 与文档（1 小时）

14. 新增 e2e fixture `test_code_agent_e2e_with_reflection.py`
15. 更新 spec `2026-07-01-code-agent-routing-and-exploration-design.md` §6 DoD

**总计**：约 2 人天。

---

## 5. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
| ------ | ------ | ------ | ------ |
| LLM 反思推理不稳定，输出随机 | 中 | 中 | 用 fake LLM 测试覆盖所有错误路径；reasoning 字段不参与 state 回写 |
| 反思空术语导致下轮浪费 | 中 | 高 | 强制 break 机制 + cap 机制 |
| 反思 cap 触发过于频繁 | 低 | 中 | 通过 `consecutive_no_progress_reflections` 调参（默认 2） |
| 反思 LLM 调用超出 8 次/查询预算 | 低 | 高 | 反思每轮最多 1 次；反思触发时查询上调至 8~10 次（vs 现状 8 次）。可接受的边际成本（仅触发时 +1~2 次） |
| 反思引入新故障模式 | 中 | 中 | 反思路径与现有路径隔离；现有 8 项 Explorer 单测不变 |
| LLM 注入攻击（reasoning 含 prompt 注入） | 低 | 高 | reasoning 字段不进入 state 回写，仅 log 截断 |
| `should_reflect` 字段被现有 fixture 误触发 | 低 | 中 | 默认 False；新增 fixture 显式开启 |
| CodeCompletenessResult 字段新增破坏向后兼容 | 低 | 中 | 新字段有默认值；现有调用方不动 |

---

## 6. 决策记录

| 决策项 | 选择 | 理由 |
| -------- | ------ | ------ |
| 是否替换 CodeExplorer | ❌ 否 | 7 mode 硬保证 + LLM 成本红线 + 12 commits 沉没成本 |
| 是否引入 LangChain @tool | ❌ 否 | 奥卡姆剃刀：当前复杂度已足够 |
| 是否引入 messages reducer | ❌ 否 | 与顶层 query_graph 的 messages 字段冲突 |
| 反思触发位置 | `_assess` 后 | 5 mode 已有"是否进展"判定信号，复用而非新建 |
| 反思输出范围 | 仅 search_terms + candidate_repos | 不动 expanded_context / seen_files（避免污染） |
| 反思失败策略 | 软错误（跳过） | 主循环不能被 LLM 不可用阻塞 |
| cap 阈值 | 2 次连续无进展 | 平衡"反思成本"与"反复尝试" |
| repo 白名单来源 | repo_registry 单例 | 已有基础设施，避免新注入 |

---

## 7. 验收标准（DoD）

- [ ] `_reflect_and_replan` 方法实现并接入 `_run_one_round`
- [ ] `build_reflection_prompt` + `parse_reflection_response` 在 `prompts/reflection.py`
- [ ] `ExplorerState` 加 `reflection_count` + `consecutive_no_progress_reflections` 字段
- [ ] `CodeCompletenessResult` 加 `should_reflect` 字段
- [ ] 反思触发时 LLM 调用 ≤ 1 次/轮
- [ ] 连续 2 次反思无进展强制 cap
- [ ] 反思空术语强制 cap
- [ ] 现有 8 项 Explorer 单测 + 7 mode fixture 100% 通过
- [ ] 新增 8 项 reflection 单元测试 + 8 项 Explorer 集成测试通过
- [ ] 4 个 Prometheus 指标上线
- [ ] 端到端 fixture `test_code_agent_e2e_with_reflection.py` 通过
- [ ] spec `2026-07-01-code-agent-routing-and-exploration-design.md` §6 DoD 更新

---

## 8. 引用

- `src/spma/agents/code/explorer.py` — CodeExplorer 主体
- `src/spma/agents/code/completeness.py` — 7 mode 收敛判定
- `src/spma/agents/code/graph.py` — build_code_agent_graph
- `docs/superpowers/specs/2026-07-01-code-agent-routing-and-exploration-design.md` — 上游 spec（被本设计扩展）
- `docs/designs/SPMA-design-13-industry-research-code-location.md` — Claude Code 风格探索流程来源
- `docs/SPMA-technology-selection.md` §2.3 — ReAct vs StateGraph 设计哲学
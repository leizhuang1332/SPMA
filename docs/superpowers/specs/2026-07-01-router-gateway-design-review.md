# Design Review: Router/Entry Gateway (SPMA-design-12)

> **审核日期**: 2026-07-01
> **审核对象**: `docs/designs/SPMA-design-12-router-entry-gateway-design.md`
> **审核原则**: KISS（优先消除不必要抽象、不变式靠语言层 enforce 而非流程纪律）

---

## 〇、TL;DR

设计目标合理：**在 classify 之前加 L0 规则短路 4 类 query（chitchat / empty / oversize / pii / jailbreak）**。整体方向无错，但存在 **5 个必须修改** + **6 个建议修改** + **3 个文档事实错误**。

**最大风险**：worker 契约靠"PR review checklist + 代码注释" enforce —— LangGraph 没有 runtime 机制阻止 worker 节点 `return {..., "router_decision": {...}}` 覆盖 entry_router 已写入值。这个不变量必须用语言机制保证（建议方案见 §3.2 BLOCKING-3）。

**最大简化机会**：独立 `agents/router/` 子包 + 5 个新文件 → 可压缩到 supervisor/ 单文件 + ≤2 dataclass。

---

## 一、必须修改（BLOCKING）

### BLOCKING-1：可达性表 #8 / #15 关于 `query_id` 生成的行号是错的

design-12 可达性表原文：
> #8 | `query_id` 在 `routes/query.py:54` 才生成，SSE `input_state` 里没有
> #15 | query_id 在 entry_router 阶段还没生成 | `query.py:598`

**核实**：`grep -n query_id routes/query.py` 结果显示 query_id 在 **第 54 行 query_stream() 内 str(uuid.uuid4())**，并在 **第 142 行 / 第 181 行 / 第 224 行 input_state dict 里**（`"query_id": query_id`）。

**影响**：
- §4.5 SSE 输入路径改动说 "routes/query.py:598 input_state 提前生成 query_id" —— 这一句**前提是错的**。query_id 早已经进入 input_state 了
- §3.4 entry_router 通过 state.get("query_id") 拿值是**可以工作**的，不依赖任何改动

**修改建议**：删 §4.5 的"SSE 输入路径改动"整段（query_id 已经在 input_state 里）。这是 ghost work。

### BLOCKING-2：StreamMerger 改造依赖关系没说清，log_query 写入路径存在阻塞

§4.5 末尾：
> StreamMerger 改造细节：从 generator 改成 await 返回 (events, final_state)，不在本 spec 范围（单独 PR）。
> §4.5 SSE 输出路径改动：从 graph 返回的 final_state 收 router_decision

**矛盾**：`routes/query.py:294` 已经调用 `synthesis_graph.ainvoke(...)` 但它是 supervisor 的子调用——而 339 行 `log_query` 写入的是 supervisor 的 state，不是 orchestrator graph 的 final_state。

**核实**：
- `routes/query.py:177` / `:224` 才是 query_graph 的 `g.ainvoke(...)`（注释可见是 graph）
- §4.5 的改动代码片段引用 `graph.ainvoke(input_state, config)` "返回 final_state" —— 但当前 StreamMerger（grep stream_merger.py 可知）应该是 iterator/generator，**不支持 .ainvoke 的 final_state 透传**

**修改建议**：必须**先做** StreamMerger 改造 PR，**再做** entry_router PR。或在 entry_router 阶段**绕过** StreamMerger：在 routes/query.py:177 处包一层 try/except，捕获 ainvoke 返回值（这是没 generator 时的标准写法——但当前是 generator），实际成本是 StreamMerger 必须改。

**P0 顺序建议**：
1. PR#1：StreamMerger 改造（generator → async with final_state）
2. PR#2：entry_router shadow（依赖 PR#1）
3. PR#3：P1 真短路

### BLOCKING-3：worker 契约靠"代码注释 + PR review"——runtime 无 enforce

§3.7 原文：
> `_FORBIDDEN_ROUTER_KEYS = frozenset({"router_decision", "router_short_circuit", "router_passthrough_reason"})`
> PR review checklist：worker 节点返回 dict 不含 router_ 前缀字段

**问题**：
- 这只是**冻结集合常量 + 注释**，**没有任何 import 或检查代码**
- LangGraph node 返回 dict 直接 merge 到 state，runtime 不区分"这个字段是谁写的"
- 实际 bug 场景：code_worker 节点某次重构时不小心 return `{"router_decision": "...", "answer": "..."}` —— 会**静默覆盖** entry_router 的值，且 PR review 检查不到（reviewer 看不到小 return 的细节）

**language-level enforcement 方案（KISS 推荐）**：

```python
# spma/agents/supervisor/state.py 顶部
from langgraph.graph.state import StateGraph

# 用 LangGraph 的 Input/Output schema 隔离（需要 LangGraph 0.3+）
# 或更简单：把 router_decision 移到 graph.run 内 **运行时元数据**（如 callbacks）而非 state

# 最低成本方案：base worker 加 assert
# src/spma/agents/base.py
def guard_no_router_keys(node_name: str, output: dict) -> dict:
    forbidden = {"router_decision", "router_short_circuit",
                 "router_passthrough_reason"}
    leaked = forbidden & set(output.keys())
    if leaked:
        raise RuntimeError(
            f"[{node_name}] worker returned router-prefixed keys {leaked}; "
            "router_* is entry_router exclusive"
        )
    return output
```

每个 worker 入口加 `output = guard_no_router_keys("doc_worker", output)`（fail-fast in dev/test）。

**KISS 视角**：与其靠 PR review，**在 worker 节点加 4 行 assert** 把不变量推到 runtime——cost vs benefit 远高于靠纪律。

### BLOCKING-4：P0 阶段跑 7 天 shadow，但 `entry_router_enabled=false` 默认关闭

§3.4 entry_router_node 逻辑：
```python
if not ff.is_enabled("entry_router_enabled"):
    mode = "bypass"   # ← 跳过 L0 规则！
else:
    mode = "shadow"
```

**问题**：
- `entry_router_enabled=false` → mode="bypass" → **不跑 L0 规则，不写 router_decision trace**
- P0 阶段的真实目的是**采集潜在命中率**——但 bypass 直接让数据采集为 0%
- §1 打磨表第 3 行说"已修改为 shadow 模式"，但实际代码却和打磨表不一致

**修改建议**：
- P0 阶段：`entry_router_enabled=true`，**代码逻辑**改为：
  ```python
  mode = "real_short_circuit" if ff.is_enabled("entry_router_enabled") else "shadow"
  ```
- 即 `entry_router_enabled=true` 时短路，`false` 时纯采集
- 或更简单：**P0 直接 hardcode `mode = "shadow"`**（不管 flag），P1 才引入 flag 控制
- 当前文档 §1 打磨表与 §3.4 代码自相矛盾，必须二选一

### BLOCKING-5：JAILBREAK / PII_SELF 规则可能误伤 + 没有"软警告"机制

§3.2 8 条规则把"软拦截（chitchat）"和"硬拦截（PII/JAILBREAK）"用**同一个影子机制**处理——但它们的 UX 期望不同：

- chitchat：用户问"你好"，期望友好回应
- jailbreak：用户试图越狱，**硬拒绝 + 不告知任何信息**（避免 oracle attack）
- PII：用户发了身份证号，**告知不应发，但可能误判（"帮我看看 110000 这种规则"也是 PII 模式）**

**问题**：
- L0 命中后**总返回 AIMessage + END**——硬拦截
- shadow 阶段看不出"JAILBREAK 误伤"和"PII 误伤"——因为没流量
- "system prompt 在哪里" 类技术提问**已经被 regex 收紧**（打磨表第 8 行说过），但**没有给出"误伤 case 列表"**——shadow 阶段如果跑出误伤，需要怎么快速调整？

**修改建议**：
- BLOCKING-5a：JAILBREAK / PII_SELF 的 `response_id` 在 P0 shadow 阶段**只写 trace 不返回**——区别于 chitchat 类的"真短路"
- 即在 entry_router_node 加：
  ```python
  # P0: 只有 chitchat/empty/oversize 真响应；PII/JAILBREAK 只写 trace（误伤评估）
  if mode == "shadow" and matched_rule.category in ("pii", "jailbreak"):
      short_circuit = False  # 即使 ff=true, P0 时这些类别不真短路
  ```
- BLOCKING-5b：shadow 阶段跑完后，人工抽查 ≥50 条（design-12 已承诺 #4 决策门槛）

### BLOCKING-6：决策门槛 #6 PII hash 算法 PM/法务 sign-off 时机不当

§七决策门槛：
> #6 | PII hash 算法 PM/法务 sign-off（sha256[:8]） | PM + 法务 | P0 上线前

**问题**：
- **sha256[:8] = 32 bit 熵**——生日攻击成本 ~2^16 = 65K 次（在已知查询前缀的协助下可大幅降低）——对**身份证号这种低熵 PII**不够安全
- 实际场景：身份证号 = 18 位数字，前 6 位是地区码（公开），后 4 位是校验位（可计算），中间 8 位生日（公开度高）——**单 sha256[:8] 不足以防 rainbow table 攻击**

**正确做法**：
- 用 **HMAC-SHA256 + 随机 salt**（per-row salt 或全局 salt）
- 或者更简单：直接**不保留 prefix，只存 `sha256[:16]`**——doc/code worker 不需要 prefix 来 debug
- design-12 前 8 字符 + sha256[:8] = **可逆工程弱**（已知身份证前 8 字符几乎确定性反推）

**修改建议**：
```python
def _pii_safe(query: str) -> str:
    """PII 命中时不保留 prefix。"""
    if not query:
        return ""
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"***[pii-redacted:{digest}]"
```

---

## 二、建议修改（NICE-TO-HAVE）

### NICE-1：8 条规则 dataclass 过度抽象——可直接 list[tuple]

§3.2 L0Rule dataclass 5 个字段（rule_id / category / pattern / response_id / reason），调用方式 `l0_match(query)` 遍历第一个命中。

**KISS 建议**（无需 dataclass）：

```python
# spma/agents/supervisor/l0_rules.py 单文件
import re

L0_RULES = [
    ("EMPTY", "empty", re.compile(r"^\s*$|^[\s\.\,\!\?]+$"), "empty"),
    ("TOO_LONG", "oversize", re.compile(r"^.{2000,}$"), "oversize"),
    ("GREETING", "chitchat", re.compile(r"^(你好|hi|hello|...)\s*[!！。.～\?]*\s*$", re.I), "greeting"),
    # ...
]

def l0_match(query: str) -> tuple[str, str, str] | None:
    """return (rule_id, category, response_id) or None"""
    for rule_id, category, pattern, response_id in L0_RULES:
        if pattern.search(query):
            return (rule_id, category, response_id)
    return None
```

节省：4 个文件 + 1 个 `L0Rule` dataclass + total=False TypedDict —— **`agents/router/` 子包可整体删除**。

### NICE-2：单 `_router_metrics()` 全局单例在测试时会泄漏 registry

§4.1 `_router_metrics: RouterMetrics | None = None` 全局变量 + `get_router_metrics()` 单例。

- prometheus_client 默认 registry 不允许多次注册同名 metric
- `test_router_metrics.py` 多个测试会冲突

**KISS 建议**：参照 `observability/qr_metrics.py` 现有模式（直接看 qr_metrics.py 实现确认），使用与 qr_metrics 一致的 init 方式。如果不一致会带来认知负担。

### NICE-3：appendix 变更摘要第 4 / 5 行说"已修改"，但实际代码/文档矛盾

附录 A：
> JAILBREAK | regex 太宽，误伤技术提问 | 收紧：只匹配明确越狱意图
> 灰度（原"已修改"）与 §3.2 实际 regex 一致 —— 但 JAILBREAK 仍然会匹配"忽略上面所有指令"这种**不是真 jailbreak 但包含 trigger keyword** 的合法指令

例：`"忽略一下上面所有的引用格式"` —— 会误判 JAILBREAK

**建议**：把 JAILBREAK 类别**剔除 L0**——只靠 trace 检测，不在 L0 真短路；或加 negative test 覆盖 ≥10 个 false positive case。

### NICE-4：cost 模型整段可以删

§九 5-6 次 LLM 调用 × 模型单价 = 估算。**真正有用的只有一句**："如果 shadow 命中率 <3%，ROI 不成立，删除代码"。

整个 §九 建议压缩为：
> Cost = $0（纯正则）。ROI 由 shadow 命中率决定：<3% 删代码，≥3% 启用 P1。详见 §2.3 判定门槛。

节省 ~20 行 speculative 文字。

### NICE-5：测试矩阵有重叠

- test_pii_safe.py（unit）+ test_trace_router_decision.py（integration）都验证 PII hash 化
- test_feature_flag_integration.py 覆盖了 test_entry_router.py 的 bypass 分支（部分）

**建议**：合并为 ≤8 个测试，保持 unit/integration 比例 5/3 而非 8/3。

### NICE-6：§六文件改动清单列了 11 个文件——其中 5 个可合并

`agents/router/__init__.py / l0_rules.py / responses.py / entry_router.py / metrics.py` 5 个文件可以合并为 `agents/supervisor/entry_router.py` 1 个文件 + 1 个 `_router_metrics.py`（仅 metrics 单独文件，因为 prometheus_client 全局状态需独立 import）。

节省：4 个文件。

---

## 三、KISS 压缩建议（如果重写）

如果允许**重写而非补丁**：

| 当前 | 简化后 |
|---|---|
| `spma/agents/router/` 子包（5 文件） | `spma/agents/supervisor/entry_router.py` 单文件（dataclass + rules + responses + node） |
| `spma/agents/router/metrics.py` | `spma/observability/entry_router_metrics.py`（复用 prometheus_client + qr_metrics 模式） |
| `RouterDecision` TypedDict（5 字段） | dict（4 字段：layer/rule_id/category/mode） |
| `route_id/category/confidence/latency_ms/mode/shadow_hit` 7 字段 | 合并为 `{rule_id, category, mode, latency_ms}` 4 字段 |
| 3 个 state 字段（router_decision / short_circuit / passthrough_reason） | 2 个（router_decision + router_short_circuit）—— passthrough_reason 从 router_decision.mode 推 |
| 11 个测试 | 8 个（合并重叠） |
| GIN 索引 + JSONB + 7 天采集 | JSONB + 7 天采集（删 GIN——数据量 <10K 全表扫足够） |
| _pii_safe(prefix + sha256[:8]) | _pii_safe(sha256[:16]) 无 prefix |
| Worker 契约靠 PR review | base.py 加 4 行 guard_no_router_keys |
| P0 `entry_router_enabled=false` → bypass | P0 hardcode `mode = "shadow"`，flag 仅 P1 引入 |

总复杂度：**减少 40-50%**，feature 等价。

---

## 四、文档事实错误（必须修）

### ERR-1：可达性表 #8 / #15

query_id 在 routes/query.py:54 已生成，且 SSE input_state 中已有"query_id": query_id。design-12 引用行号 `#598` 是错的——这行的内容是别的东西。

### ERR-2：附录 A 削龙表第 9 行

> 时间估算 | P0/P1 各 0.5 天 | P0=1.5d+1d / P1=1d

但文档正文 §5.1 P0 = 1.5 天实现 + 1 天测试。这与 §5.2 P1 = 1 天合计 3.5 天的工作量——而 §TL;DR 第 25 行说的是"P0 = 1.5d 实现 + 1d 测试/联调；P1 = 1d"——但实际写起来 1.5+1+1=3.5d 而非"P0/P1 总计 3 天"。

建议统一：**P0 = 1.5d + 1d = 2.5d, P1 = 1d, 总计 3.5d（包括 StreamMerger 改造）**。

### ERR-3：附鈩 A 变更摘要第 12 行

> spec 路径 | 默认 superpowers/specs | 覆盖 docs/designs/SPMA-design-12（in-place 打磨）

`docs/designs/SPMA-design-12-*.md` **不是 superpowers spec**，是项目设计文档（design-12 数字序列）。两份文件的角色不同（specs 是 per-PR 临时设计，designs 是长期架构文档）。原"取消 module_manifest.yaml"的实现详见 `docs/designs/SPMA-design-13-industry-research-code-location.md`。

这不是错误——但 "in-place 打磨" 的说法容易被误解为"spec 文件可以放到 designs 下"——实际应该是"design-12 文档被 superpowers/specs 的某次 PR review 打磨"。

---

## 五、修改优先级

| 优先级 | 项 | 影响 |
|---|---|---|
| P0（必须） | BLOCKING-1, 2, 3, 4 | 影响实现正确性 |
| P0（必须） | BLOCKING-5, 6 | 影响生产安全 + 合规 |
| P1（建议） | NICE-1, 2, 3 | 减少认知负担 |
| P2（可选） | NICE-4, 5, 6 | 锦上添花 |
| 文档 | ERR-1, 2, 3 | 准确性问题 |

---

## 六、未在本 review 覆盖的事

- LangGraph conditional edge `True: END` 行为是否在 0.2+ 稳定（文档声明支持，未亲自验证）
- `from spma.agents.router.entry_router import ...` 的 import path 是否符合项目现有 lint 配置（需 pyproject.toml 确认）
- Query Rewriter（design-11）现有 hash/policy 模式——避免 entry_router 与 qr_metrics 重复设计
- StreamMerger 当前是否是 generator（grep 可知，但 async 行为需实测）

以上 4 项需要在 PR review 阶段补齐验证。

---

## 七、References

- [SPMA-design-12 原文](SPMA-design-12-router-entry-gateway-design.md)
- [SPMA-design-11 query rewrite](SPMA-design-11-query-rewrite-optimization-v2-final.md)（参考 metrics 模式）
- [qr_metrics](../../src/spma/observability/qr_metrics.py)（参考 Prometheus 模式）
- [Query Graph 源码](../../src/spma/api/query_graph.py)
- [Routes Query 源码](../../src/spma/api/routes/query.py)
- [Stream Merger](../../src/spma/api/stream_merger.py)
- [Trace Logger](../../src/spma/observability/trace_logger.py)
- [Feature Flag Service](../../src/spma/infrastructure/feature_flags.py)
- [Supervisor State](../../src/spma/agents/supervisor/state.py)

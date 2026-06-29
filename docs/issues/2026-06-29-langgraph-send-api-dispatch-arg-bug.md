# Issue: LangGraph Send API dispatch_arg 注入机制理解错误

## 问题描述

`src/spma/api/query_graph.py` 中的 Worker 节点错误地从 `state.get("dispatch_arg", {})` 读取派发参数。但 LangGraph Send API 的实际行为是将 `arg` 直接合并（merge）到 state 中，而不是嵌套在 `"dispatch_arg"` 字段里。

## 影响范围

- `doc_worker_node` (`query_graph.py:284-291`)
- `code_worker_node` (`query_graph.py:294-298`)
- `sql_worker_node` (`query_graph.py:301-305`)

## 问题分析

### 1. LangGraph Send API 真实行为

在 LangGraph 1.x 中，`Send(node, arg)` 的第二个参数 `arg` 会被**直接合并**到目标节点的 state。但关键问题是：**LangGraph 的 TypedDict 状态验证会静默丢弃未声明的键**。

当前 `QueryOrchestratorState` 和 `SupervisorState` 均未声明 `agent_type`、`rewritten_query`、`task_id` 等 `WorkerDispatch` 字段，因此：

```python
# 当前代码构建的 dispatch
Send("doc_worker", {"agent_type": "doc", "query_id": "xxx", "rewritten_query": "..."})

# 实际效果：未声明的键被静默丢弃，state 保持不变
{..., "original_query": "...", "entities": {...}, ...}  # 没有 agent_type/rewritten_query

# 代码期望的效果（错误）
{..., "dispatch_arg": {"agent_type": "doc", "query_id": "xxx", ...}, ...}
```

### 2. 代码矛盾点

**构建端** (`dispatcher.py:39-49`)：构建扁平的 `WorkerDispatch` 结构
```python
dispatch: WorkerDispatch = {
    "task_id": f"{query_id}-{source}",
    "query_id": query_id,
    "agent_type": source,
    "original_query": ...,
    "rewritten_query": ...,
    "entities": entities,
    ...
}
```

**消费端** (`query_graph.py:289`)：从不存在的字段读取
```python
dispatch_arg = state.get("dispatch_arg", {})  # 返回空字典 {}
```

### 3. 实际影响

由于 `state.get("dispatch_arg", {})` 返回空字典，`_run_worker` 中的回退逻辑虽然不会报错，但会**丢失 Send API 传递的定制化参数**：

| 字段 | 预期来源 | 实际来源 |
|------|----------|----------|
| `agent_type` | `dispatch_arg` | 默认值 `"doc"` |
| `original_query` | `dispatch_arg` | 回退到 `state["original_query"]` |
| `rewritten_query` | `dispatch_arg` | 回退到 `original_query`（即原始查询） |
| `entities` | `dispatch_arg` | 回退到 `state["entities"]` |

**核心问题**：并行派发时，每个 worker 都收到相同的原始查询，而不是各自的改写查询。

### 4. 注释与实际行为不一致

| 位置 | 注释内容 | 实际情况 |
|------|----------|----------|
| `query_graph.py:287-288` | "LangGraph Send API 会将匹配的 dispatch arg 注入 state 中" | **arg 会被合并到 state，不是注入到 "dispatch_arg" 字段** |
| `query_graph.py:144` | "Send API 会用 dispatch_arg 替换 state" | **是 merge 不是 replace** |

## 修复方案

### 方案一：直接从 state 读取（不可行）

**此方案不可行**。即使 Send API 将 arg 合并到 state，由于 `QueryOrchestratorState` 未声明 `agent_type`、`rewritten_query` 等字段，LangGraph 的 TypedDict 验证会静默丢弃这些未声明的键，导致这些字段根本不会出现在 state 中。

此外，并行派发场景下，多个 worker 同时写入同一 state 会导致字段覆盖冲突（如 `agent_type` 会被最后一个写入的 worker 覆盖）。

### 方案二：包装成 dispatch_arg（唯一可行方案）

需要两个步骤：

**步骤 1**：在 `QueryOrchestratorState` 中声明 `dispatch_arg` 字段

```python
# query_graph.py - QueryOrchestratorState
class QueryOrchestratorState(SupervisorState, total=False):
    # ... 现有字段 ...
    
    dispatch_arg: NotRequired[dict]
    """Send API 注入的派发参数（WorkerDispatch 结构）"""
```

**步骤 2**：在构建 Send 时包装一层

```python
# dispatcher.py 中修改
dispatches.append(Send(f"{source}_worker", {"dispatch_arg": dispatch}))
```

这样 LangGraph 合并后，state 中就会有 `dispatch_arg` 字段，且由于 `dict` 是已声明的类型，其内部内容不会被验证丢弃。

**优势**：
- 避免字段覆盖冲突（每个 worker 的 dispatch_arg 是独立的）
- 符合现有代码结构，改动最小
- Worker 节点代码无需修改，`state.get("dispatch_arg", {})` 即可正确读取

## 关联文件

- `src/spma/api/query_graph.py` - Worker 节点实现
- `src/spma/agents/supervisor/dispatcher.py` - Send 对象构建
- `src/spma/models/worker_output.py` - `WorkerDispatch` 类型定义
- `src/spma/api/routes/query.py:149` - 正确的用法参考（手动处理 Send 对象）

## 版本信息

- LangGraph: 1.2.4

## 优先级

**高** - 影响并行派发功能的正确性，导致改写查询失效。

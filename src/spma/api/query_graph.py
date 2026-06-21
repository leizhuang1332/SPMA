"""QueryOrchestrator StateGraph —— 顶层查询编排图。

将 query.py 中的手动编排（分类 → 抽取 → 改写 → 派发 → Worker → 质量 → 合成）
封装为 LangGraph StateGraph，利用 AsyncPostgresSaver 自动 checkpoint 持久化。

关键架构变化：
- conversation_history 不再从前端传入，而是由 classify_node 从 state["messages"]
  （LangGraph checkpoint 自动恢复）中通过 format_history 提取。
- 使用 Send API 实现并行派发，operator.add reducer 实现 fan-in 收敛。
- synthesis_node 将 final_answer 写入 AIMessage 并追加到 messages，自动持久化。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.types import Send

from langchain_core.messages import BaseMessage, AIMessage

from spma.agents.supervisor.state import SupervisorState

logger = logging.getLogger(__name__)


# ============================================================
# State Schema
# ============================================================

class QueryOrchestratorState(SupervisorState, total=False):
    """查询编排器顶层状态——扩展 SupervisorState，增加 checkpoint 管理的对话历史。

    messages 字段由 add_messages reducer 管理：
    - 每次节点返回 {"messages": [msg]} 时自动追加到消息链
    - LangGraph checkpoint 自动持久化整个 messages 列表
    - classify_node 从中提取最近的对话轮次构建 classification 的 conversation_history
    """

    messages: Annotated[list[BaseMessage], add_messages]
    """Checkpoint 管理的对话历史（LangGraph 自动恢复/持久化）"""

    session_id: str
    """会话 ID，用于 checkpoint 的 thread_id"""

    sources_hint: list[str] | None
    """前端可选的 source 提示，覆盖分类结果中的 sources"""


# ============================================================
# Node Functions
# ============================================================


async def classify_node(state: QueryOrchestratorState) -> dict:
    """分类 + 实体抽取节点。

    从 state["messages"] 中通过 format_history 构建对话上下文，
    替换原来从前端传入的 conversation_history。
    """
    from spma.agents.supervisor.classifier_fallback import classify_with_fallback
    from spma.agents.supervisor.entity_extractor import extract_entities
    from spma.api.extract_turns import format_history
    from spma.llm import get_langchain_client

    llm = get_langchain_client(role="generation")

    # 从 checkpoint 恢复的 messages 中提取对话历史
    conversation_history = format_history(state.get("messages", []))

    classification = await classify_with_fallback(
        query=state["original_query"],
        primary_llm=llm,
        conversation_history=conversation_history,
    )
    entities = await extract_entities(state["original_query"], classification)

    return {
        "classification": classification,
        "entities": entities,
    }


async def rewrite_node(state: QueryOrchestratorState) -> dict:
    """查询改写节点——为每个 source 生成专用查询。"""
    from spma.agents.supervisor.query_rewriter import rewrite_queries
    from spma.llm import get_langchain_client

    llm = get_langchain_client(role="generation")

    rewritten = await rewrite_queries(
        query=state["original_query"],
        classification=state["classification"],
        entities=state.get("entities", {}),
        llm=llm,
    )
    return {"rewritten_queries": rewritten}


async def dispatch_node(state: QueryOrchestratorState) -> dict:
    """派发节点——实际路由由 route_to_workers 条件边通过 Send API 处理。"""
    return {}


def route_to_workers(state: QueryOrchestratorState) -> list[Send]:
    """条件边函数——构建 Send 列表实现并行派发。

    从 classification 中获取 sources，为每个 source 构建一个 Send 对象，
    目标节点为 "{source}_worker"，携带 WorkerDispatch 作为 arg。

    如果没有可派发的 source，发送到 synthesis 节点（跳过 worker）。
    """
    from spma.agents.supervisor.dispatcher import build_dispatches

    dispatches = build_dispatches(
        classification=state["classification"],
        entities=state.get("entities", {}),
        rewritten_queries=state.get("rewritten_queries", {}),
        query_id=state.get("query_id", ""),
    )

    if not dispatches:
        # 没有需要派发的 worker，直接跳到 synthesis
        return [Send("synthesis", {})]

    return dispatches


async def _run_worker(
    state: QueryOrchestratorState,
    dispatch_arg: dict,
) -> dict:
    """私有辅助函数——根据 agent_type 调用对应的子 Agent 图。

    从 dependencies 获取共享检索基础设施（es_client/vector_store/embedder），
    失败时优雅降级返回空结果。
    """
    agent_type = dispatch_arg.get("agent_type", "doc")
    # Send API 会用 dispatch_arg 替换 state，因此所有字段从 dispatch_arg 读取
    original_query = dispatch_arg.get("original_query") or state.get("original_query", "")
    rewritten_query = dispatch_arg.get("rewritten_query") or original_query
    query_id = dispatch_arg.get("query_id", "")
    entities = dispatch_arg.get("entities") or state.get("entities", {})

    try:
        if agent_type == "doc":
            from spma.agents.doc.graph import build_doc_agent_graph
            from spma.api.dependencies import get_es_client, get_vector_store, get_embedder
            from spma.llm import get_langchain_client

            try:
                es_client = get_es_client()
                vector_store = get_vector_store()
                embedder = get_embedder()
            except RuntimeError as e:
                logger.warning("Doc worker 基础设施未初始化: %s", e)
                return {
                    "worker_type": agent_type,
                    "result_count": 0,
                    "citations": [],
                    "confidence": 0,
                    "has_exact_match": False,
                    "error": f"worker_not_ready:{str(e)[:100]}",
                }

            llm = get_langchain_client(role="generation")
            g = build_doc_agent_graph(
                es_client=es_client,
                vector_store=vector_store,
                embedder=embedder,
                llm=llm,
            )
            result = await g.ainvoke({
                "original_query": original_query,
                "rewritten_queries": [rewritten_query],
                "retriever": None,
                "query_id": query_id,
                "entities": entities,
            })
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("final_results", [])
            return {
                "worker_type": agent_type,
                "result_count": len(citations),
                "citations": normalize_citations(agent_type, citations),
                "confidence": result.get("confidence", 0.8),
                "has_exact_match": result.get("has_exact_match", False),
                "rounds_used": result.get("rounds_used", 1),
            }

        elif agent_type == "code":
            from spma.agents.code.graph import build_code_agent_graph
            from spma.api.dependencies import (
                get_file_path_cache,
                get_ripgrep_executor,
                get_ast_parser,
            )
            from spma.llm import get_langchain_client

            try:
                file_path_cache = get_file_path_cache()
                ripgrep_executor = get_ripgrep_executor()
                ast_parser = get_ast_parser()
            except RuntimeError as e:
                logger.warning("Code worker 基础设施未初始化: %s", e)
                return {
                    "worker_type": agent_type,
                    "result_count": 0,
                    "citations": [],
                    "confidence": 0,
                    "has_exact_match": False,
                    "error": f"worker_not_ready:{str(e)[:100]}",
                }

            llm = get_langchain_client(role="generation")
            g = build_code_agent_graph(
                file_path_cache=file_path_cache,
                ripgrep_executor=ripgrep_executor,
                ast_parser=ast_parser,
                llm=llm,
            )
            result = await g.ainvoke({
                "original_query": original_query,
                "rewritten_queries": [rewritten_query],
                "query_id": query_id,
            })
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("ripgrep_results", [])
            return {
                "worker_type": agent_type,
                "result_count": len(citations),
                "citations": normalize_citations(agent_type, citations),
                "confidence": 0.7,
                "has_exact_match": result.get("fallback_layer", 99) == 0,
                "rounds_used": result.get("rounds_used", 1),
            }

        elif agent_type == "sql":
            # SQL worker 待后续实现
            return {
                "worker_type": agent_type,
                "result_count": 0,
                "citations": [],
                "confidence": 0,
                "has_exact_match": False,
                "error": "sql_worker_not_implemented",
            }

        else:
            return {
                "worker_type": agent_type,
                "result_count": 0,
                "citations": [],
                "confidence": 0,
                "has_exact_match": False,
                "error": f"unknown_worker_type:{agent_type}",
            }

    except Exception as e:
        logger.warning("Worker '%s' 执行失败: %s", agent_type, e)
        return {
            "worker_type": agent_type,
            "result_count": 0,
            "citations": [],
            "confidence": 0,
            "has_exact_match": False,
            "error": f"worker_failed:{str(e)[:100]}",
        }


async def doc_worker_node(state: QueryOrchestratorState) -> dict:
    """Doc Worker 节点——调用 Doc Agent 子图。

    LangGraph Send API 会将匹配的 dispatch arg 注入 state 中。
    """
    dispatch_arg = state.get("dispatch_arg", {})
    output = await _run_worker(state, dispatch_arg)
    return {"worker_outputs": [output]}


async def code_worker_node(state: QueryOrchestratorState) -> dict:
    """Code Worker 节点——调用 Code Agent 子图。"""
    dispatch_arg = state.get("dispatch_arg", {})
    output = await _run_worker(state, dispatch_arg)
    return {"worker_outputs": [output]}


async def sql_worker_node(state: QueryOrchestratorState) -> dict:
    """SQL Worker 节点——调用 SQL Agent 子图。"""
    dispatch_arg = state.get("dispatch_arg", {})
    output = await _run_worker(state, dispatch_arg)
    return {"worker_outputs": [output]}


async def synthesis_node(state: QueryOrchestratorState) -> dict:
    """合成节点——调用 Synthesis Agent 子图生成最终回答。

    将 final_answer 包装为 AIMessage 并追加到 messages 中，
    add_messages reducer 自动将其持久化到 checkpoint。
    """
    from spma.agents.synthesis.graph import build_synthesis_agent_graph
    from spma.llm import get_langchain_client

    llm = get_langchain_client(role="generation")

    try:
        synthesis_graph = build_synthesis_agent_graph(llm=llm, audit_llm=llm)
        synthesis_result = await synthesis_graph.ainvoke({
            "original_query": state["original_query"],
            "worker_outputs": state.get("worker_outputs", []),
            "max_rounds": 2,
            "round": 0,
        })
        final_answer = synthesis_result.get("final_answer", "")
    except Exception as e:
        logger.warning("Synthesis agent 失败: %s", e)
        worker_count = len(state.get("worker_outputs", []))
        classification = state.get("classification", {})
        final_answer = (
            f"[降级回答] 针对查询 '{state['original_query']}' "
            f"已分类为 {classification.get('sources', [])} 源，"
            f"Worker 输出 {worker_count} 条结果。完整合成失败: {str(e)[:200]}"
        )

    # 将 final_answer 写入 AIMessage 追加到 messages（checkpoint 自动持久化）
    ai_message = AIMessage(content=final_answer)

    return {
        "final_answer": final_answer,
        "messages": [ai_message],
    }


async def quality_node(state: QueryOrchestratorState) -> dict:
    """质量评估节点——三维评分（count + confidence + exact_match）。"""
    from spma.agents.supervisor.quality import evaluate_workers

    classification = state.get("classification", {})
    query_type = classification.get("query_type", "search")

    result = evaluate_workers(
        state.get("worker_outputs", []),
        query_type,
        threshold=0.6,
    )
    return {"quality_scores": result["scores"]}


def should_reschedule(state: QueryOrchestratorState) -> Literal["reschedule", "converge"]:
    """质量门条件边——评分不足且未超过最大重调度次数时触发重调度。"""
    worker_outputs = state.get("worker_outputs", [])
    if not worker_outputs:
        return "converge"

    reschedule_count = state.get("reschedule_count", 0)
    if reschedule_count >= 2:
        return "converge"

    quality_scores = state.get("quality_scores", {})
    if any(s < 0.6 for s in quality_scores.values()):
        return "reschedule"

    return "converge"


async def reschedule_node(state: QueryOrchestratorState) -> dict:
    """重调度节点——递增计数器，调整实体提示后重新派发。

    从高分 worker 中提取 discovered_entities 作为下一轮的提示。
    """
    worker_outputs = state.get("worker_outputs", [])
    quality_scores = state.get("quality_scores", {})

    from spma.agents.supervisor.dispatcher import extract_discovered_entities

    successful = [
        w for w in worker_outputs
        if quality_scores.get(w.get("worker_type", ""), 0) >= 0.6
    ]
    hints = extract_discovered_entities(successful)

    reschedule_count = state.get("reschedule_count", 0) + 1

    # 合并新发现的实体到现有 entities
    current_entities = dict(state.get("entities", {}))
    for key, values in hints.items():
        existing = current_entities.get(key, []) or []
        for v in values:
            if v not in existing:
                existing.append(v)
        current_entities[key] = existing

    return {
        "reschedule_count": reschedule_count,
        "entities": current_entities,
    }


# ============================================================
# Build Function
# ============================================================


def build_query_orchestrator_graph() -> StateGraph:
    """构建查询编排器 StateGraph（不编译，编译在 app startup 时完成）。

    图结构：
    ```
    classify → rewrite → dispatch ──Send API──┬─ doc_worker ─┐
                                               ├─ code_worker ─┼─→ synthesis → quality ─┬→ END
                                               └─ sql_worker ─┘                         │
                                                        ↑                               │
                                                        └─── reschedule ←── < 0.6 ──────┘
    ```
    """
    graph = StateGraph(QueryOrchestratorState)

    # 注册节点
    graph.add_node("classify", classify_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("doc_worker", doc_worker_node)
    graph.add_node("code_worker", code_worker_node)
    graph.add_node("sql_worker", sql_worker_node)
    graph.add_node("synthesis", synthesis_node)
    graph.add_node("quality", quality_node)
    graph.add_node("reschedule", reschedule_node)

    # 入口
    graph.set_entry_point("classify")

    # 主流程边
    graph.add_edge("classify", "rewrite")
    graph.add_edge("rewrite", "dispatch")

    # Send API fan-out：route_to_workers 返回 list[Send] 实现并行派发
    graph.add_conditional_edges("dispatch", route_to_workers)

    # Fan-in：所有 worker 收敛到 synthesis 节点
    graph.add_edge("doc_worker", "synthesis")
    graph.add_edge("code_worker", "synthesis")
    graph.add_edge("sql_worker", "synthesis")

    # 合成 → 质量评估 → 条件出口
    graph.add_edge("synthesis", "quality")
    graph.add_conditional_edges(
        "quality",
        should_reschedule,
        {
            "reschedule": "reschedule",
            "converge": END,
        },
    )

    # 重调度环路
    graph.add_edge("reschedule", "dispatch")

    return graph

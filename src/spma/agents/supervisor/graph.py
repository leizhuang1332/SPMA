"""Supervisor Agent 的 LangGraph StateGraph 定义。

构建模式:
  分类+抽取 -> 查询改写 -> Send API 并行派发 -> fan-in 收集
  -> 质量评估 -> 评分>=0.6 收敛 / <0.6 + 重调度<2 -> 调整参数重派
"""

import operator
from typing import Literal, Annotated

from langgraph.graph import StateGraph, END
from langgraph.types import Send

from spma.agents.supervisor.state import SupervisorState
from spma.agents.supervisor.classifier_fallback import classify_with_fallback
from spma.agents.supervisor.query_rewriter import rewrite_queries
from spma.agents.supervisor.dispatcher import build_dispatches, extract_discovered_entities
from spma.agents.supervisor.quality import evaluate_workers


def build_supervisor_graph(
    primary_llm,
    fallback_llm=None,
    doc_graph=None,
    code_graph=None,
    sql_graph=None,
    synthesis_graph=None,
    max_rounds: int = 5,
    timeout_ms: int = 5000,
    quality_threshold: float = 0.6,
    reschedule_max: int = 2,
) -> StateGraph:

    async def classify_and_extract_node(state: SupervisorState) -> dict:
        result = await classify_with_fallback(
            query=state["original_query"],
            primary_llm=primary_llm,
            fallback_llm=fallback_llm,
            conversation_history=state.get("conversation_history", ""),
        )
        return {"classification": result, "entities": result.get("entities", {})}

    async def rewrite_node(state: SupervisorState) -> dict:
        # synonym_map 暂未实现，保持为 None
        # 后续可以通过 spma.api.dependencies 获取
        synonym_map = None

        rewritten = await rewrite_queries(
            query=state["original_query"],
            classification=state["classification"],
            entities=state.get("entities", {}),
            llm=primary_llm,
            synonym_map=synonym_map,
            conversation_history=state.get("conversation_history", ""),
        )
        return {"rewritten_queries": rewritten}

    async def dispatch_node(state: SupervisorState) -> dict:
        # 实际路由由 route_dispatches 条件边通过 Send API 处理
        return {}

    def route_dispatches(state: SupervisorState) -> list[Send]:
        return build_dispatches(
            classification=state["classification"],
            entities=state["entities"],
            rewritten_queries=state.get("rewritten_queries", {}),
            query_id=state.get("query_id", ""),
        )

    async def doc_worker_node(state: SupervisorState) -> dict:
        if doc_graph is None:
            return {"worker_outputs": [{"worker_type": "doc", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await doc_graph.ainvoke(state)
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("final_results", [])
            output = {
                "worker_type": "doc",
                "result_count": len(citations),
                "citations": normalize_citations("doc", citations),
                "confidence": 0.8,
                "has_exact_match": result.get("has_exact_match", False),
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": result.get("entities", {}),
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "doc", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def code_worker_node(state: SupervisorState) -> dict:
        if code_graph is None:
            return {"worker_outputs": [{"worker_type": "code", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await code_graph.ainvoke(state)
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("ripgrep_results", [])
            output = {
                "worker_type": "code",
                "result_count": len(citations),
                "citations": normalize_citations("code", citations),
                "confidence": 0.7,
                "has_exact_match": result.get("fallback_layer", 99) == 0,
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": {"code_refs": [r.get("file_path", "") for r in citations[:5]]},
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "code", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def sql_worker_node(state: SupervisorState) -> dict:
        if sql_graph is None:
            return {"worker_outputs": [{"worker_type": "sql", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await sql_graph.ainvoke(state)
            output = {
                "worker_type": "sql",
                "result_count": result.get("result_count", 0),
                "citations": result.get("citations", []),
                "confidence": result.get("confidence", 0.7),
                "has_exact_match": False,
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": {"table_names": result.get("tables_used", [])},
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "sql", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def score_node(state: SupervisorState) -> dict:
        worker_outputs = state.get("worker_outputs", [])
        query_type = state.get("classification", {}).get("query_type", "search")
        evaluation = evaluate_workers(worker_outputs, query_type, quality_threshold)
        return {"quality_scores": evaluation["scores"]}

    def should_reschedule(state: SupervisorState) -> Literal["reschedule", "converge"]:
        worker_outputs = state.get("worker_outputs", [])
        if not worker_outputs:
            return "converge"
        reschedule_count = state.get("reschedule_count", 0)
        if reschedule_count >= reschedule_max:
            return "converge"
        quality_scores = state.get("quality_scores", {})
        if any(s < quality_threshold for s in quality_scores.values()):
            return "reschedule"
        return "converge"

    async def reschedule_node(state: SupervisorState) -> dict:
        worker_outputs = state.get("worker_outputs", [])
        quality_scores = state.get("quality_scores", {})
        successful = [w for w in worker_outputs
                      if quality_scores.get(w.get("worker_type", ""), 0) >= quality_threshold]
        hints = extract_discovered_entities(successful)
        reschedule_count = state.get("reschedule_count", 0) + 1
        current_entities = dict(state.get("entities", {}))
        for key, values in hints.items():
            existing = current_entities.get(key, []) or []
            for v in values:
                if v not in existing:
                    existing.append(v)
            current_entities[key] = existing
        return {"reschedule_count": reschedule_count, "entities": current_entities}

    graph = StateGraph(SupervisorState)
    graph.add_node("classify_and_extract", classify_and_extract_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("doc_worker", doc_worker_node)
    graph.add_node("code_worker", code_worker_node)
    graph.add_node("sql_worker", sql_worker_node)
    graph.add_node("score", score_node)
    graph.add_node("reschedule", reschedule_node)

    graph.set_entry_point("classify_and_extract")
    graph.add_edge("classify_and_extract", "rewrite")
    graph.add_edge("rewrite", "dispatch")
    # Send API fan-out: route_dispatches returning list[Send] 将并行路由至各 worker 节点
    graph.add_conditional_edges("dispatch", route_dispatches)
    # Fan-in: 所有 worker 通过 add_edge 收敛到 score 节点（LangGraph 会自动执行一次）
    graph.add_edge("doc_worker", "score")
    graph.add_edge("code_worker", "score")
    graph.add_edge("sql_worker", "score")
    # 质量评估门
    graph.add_conditional_edges("score", should_reschedule, {
        "reschedule": "reschedule",
        "converge": END,
    })
    # 重调度环路
    graph.add_edge("reschedule", "dispatch")

    return graph.compile()

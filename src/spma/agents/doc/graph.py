"""Doc Agent 的 LangGraph StateGraph 定义。

节点: route(检索模式选择) → search(混合检索) → aggregate(累计去重) → assess(完备度判断)
条件边: 不够 → expand(线索扩展) → 回到search / 够了 → END
"""

import asyncio
from typing import Literal

from langgraph.graph import StateGraph, END

from spma.agents.doc.state import DocAgentState
from spma.agents.doc.retriever import route_retrieval_mode
from spma.agents.doc.completeness import assess_completeness
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand
from spma.retrieval.rrf_fusion import equal_weight_fusion


def build_doc_agent_graph(es_client, vector_store, embedder, llm, hyde_llm=None, weights_config=None):
    """构建 Doc Agent 的 LangGraph StateGraph。"""
    wc = weights_config or {}

    async def route_node(state: DocAgentState) -> dict:
        entities = state.get("entities", {})
        mode = route_retrieval_mode(entities)
        state["weight_mode"] = mode
        query = state.get("original_query", "")
        hyde_enabled = len(query) <= 30 and not entities.get("req_ids") and hyde_llm is not None
        state["hyde_enabled"] = hyde_enabled
        return state

    async def search_node(state: DocAgentState) -> dict:
        query = state.get("current_query", state.get("original_query", ""))
        mode = state.get("weight_mode", "semantic")
        entities = state.get("entities", {})

        es_filters = None
        if mode == "precise" and entities.get("req_ids"):
            es_filters = {"req_ids": entities["req_ids"]}

        es_future = es_client.search(query, top_k=20, filters=es_filters)
        query_embedding = await embedder.embed([query])
        vector_future = vector_store.search(embedding=query_embedding[0], top_k=20, table="chunk_embeddings")
        bm25_results, vector_results = await asyncio.gather(es_future, vector_future)

        hyde_results = []
        if state.get("hyde_enabled") and hyde_llm:
            try:
                hyde_text = await hyde_llm.generate(query)
                hyde_emb = await embedder.embed([hyde_text])
                hyde_results = await vector_store.search(embedding=hyde_emb[0], top_k=10, table="chunk_embeddings")
            except Exception:
                pass

        all_vector = list(vector_results) + hyde_results
        fused = equal_weight_fusion(source_a=bm25_results, source_b=all_vector, top_k=10, k=wc.get("rrf", {}).get("k", 60))
        state["bm25_candidates"] = bm25_results[:20]
        state["vector_candidates"] = all_vector[:20]
        state["fused_results"] = fused
        return state

    async def aggregate_node(state: DocAgentState) -> dict:
        prev = state.get("accumulated_results", [])
        current = state.get("fused_results", [])
        seen_ids = {r["chunk_id"] for r in prev}
        for r in current:
            if r["chunk_id"] not in seen_ids:
                prev.append(r)
                seen_ids.add(r["chunk_id"])
        state["accumulated_results"] = prev
        return state

    async def assess_node(state: DocAgentState) -> dict:
        results = state.get("accumulated_results", [])
        entities = state.get("entities", {})
        thresholds = wc.get("thresholds", {})
        outcome = await assess_completeness(
            results=results, entities=entities, llm=llm,
            min_results=thresholds.get("min_results_converge", 5),
            vector_threshold=thresholds.get("vector_similarity_converge", 0.85),
        )
        state["assessment"] = outcome.verdict
        state["convergence_reason"] = f"{outcome.level}:{outcome.reason}"
        return state

    async def expand_node(state: DocAgentState) -> dict:
        round_num = state.get("round", 1)
        original_query = state.get("original_query", "")
        results = state.get("accumulated_results", [])
        known_req_ids = set()
        for r in results:
            for rid in r.get("req_ids", []):
                known_req_ids.add(rid)
        if round_num <= 2:
            new_query = rule_based_expand(original_query, results, known_req_ids)
        else:
            new_query = await llm_based_expand(original_query, results, llm)
        state["current_query"] = new_query
        state["round"] = round_num + 1
        return state

    def should_continue(state: DocAgentState) -> Literal["expand", "END"]:
        assessment = state.get("assessment", "converge")
        round_num = state.get("round", 1)
        max_rounds = state.get("max_rounds", 3)
        if assessment == "converge" or round_num >= max_rounds:
            state["rounds_used"] = round_num
            state["final_results"] = state.get("accumulated_results", [])
            return "END"
        return "expand"

    graph = StateGraph(DocAgentState)
    graph.add_node("route", route_node)
    graph.add_node("search", search_node)
    graph.add_node("aggregate", aggregate_node)
    graph.add_node("assess", assess_node)
    graph.add_node("expand", expand_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "search")
    graph.add_edge("search", "aggregate")
    graph.add_edge("aggregate", "assess")
    graph.add_conditional_edges("assess", should_continue, {"expand": "expand", "END": END})
    graph.add_edge("expand", "search")
    return graph

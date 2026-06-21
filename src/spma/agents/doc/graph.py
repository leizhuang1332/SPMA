"""Doc Agent 的 LangGraph StateGraph 定义——方案三改造版。

节点: route → search → aggregate → assess ──→ expand → search
                                      └──→ END

改动范围：仅 search_node 替换为管道委托，其余节点完全不变。
"""

from typing import Literal
import logging

logger = logging.getLogger(__name__)

from langgraph.graph import StateGraph, END

from spma.agents.doc.state import DocAgentState
from spma.agents.doc.retriever import route_retrieval_mode
from spma.agents.doc.completeness import assess_completeness
from spma.agents.doc.clue_expander import rule_based_expand, llm_based_expand


def build_doc_agent_graph(
    es_client, vector_store, embedder, llm,
    hyde_llm=None, weights_config=None,
    progress=None,
):
    """构建 Doc Agent 的 LangGraph StateGraph——方案三深度集成 LlamaIndex。"""
    wc = weights_config or {}

    # ========== 方案三：初始化 LlamaIndex 管道 ==========
    from spma.agents.doc.llamaindex_pipeline import (
        AdvancedLlamaIndexPipeline,
        PipelineConfig,
    )

    pipeline_config = PipelineConfig(
        dsn=(
            vector_store._dsn
            if hasattr(vector_store, "_dsn")
            else "postgresql://spma:spma123@localhost:5433/spma"
        ),
        rrf_k=wc.get("rrf", {}).get("k", 60),
        rrf_bm25_weight=wc.get("weights", {})
        .get("hybrid", {})
        .get("bm25", 0.5),
        rrf_vector_weight=wc.get("weights", {})
        .get("hybrid", {})
        .get("vector", 0.5),
    )
    llama_pipeline = AdvancedLlamaIndexPipeline(
        es_client=es_client,
        config=pipeline_config,
    )
    llama_pipeline.initialize(embedder=embedder, hyde_llm=hyde_llm)

    # ========== 路由节点（不变）==========
    async def route_node(state: DocAgentState) -> dict:
        if progress:
            await progress.publish_step("doc_worker", "routing", "正在分析查询策略…")
        entities = state.get("entities", {})
        mode = route_retrieval_mode(entities)
        state["weight_mode"] = mode
        query = state.get("original_query", "")

        # 优先使用改写查询
        rewritten = state.get("rewritten_queries", [])
        if rewritten:
            state["current_query"] = rewritten[0]

        hyde_enabled = (
            len(query) <= 30
            and not entities.get("req_ids")
            and hyde_llm is not None
        )
        state["hyde_enabled"] = hyde_enabled
        return state

    # ========== 搜索节点（方案三改造：委托给管道）==========
    async def search_node(state: DocAgentState) -> dict:
        if progress:
            await progress.publish_step("doc_worker", "searching", "正在检索 ES + PGVector…")
        query = state.get("current_query", state.get("original_query", ""))
        entities = state.get("entities", {})
        mode = state.get("weight_mode", "hybrid")

        try:
            fused = await llama_pipeline.search(
                query=query,
                mode=mode,
                entities=entities,
                hyde_llm=hyde_llm if state.get("hyde_enabled") else None,
            )
        except Exception:
            logger.exception("search_node 检索失败，返回空结果")
            fused = []

        state["bm25_candidates"] = [
            r for r in fused
            if r.get("metadata", {}).get("retrieval_source") == "bm25"
        ][:20]
        state["vector_candidates"] = [
            r for r in fused
            if r.get("metadata", {}).get("retrieval_source") != "bm25"
        ][:20]
        state["fused_results"] = fused
        return state

    # ========== 以下节点完全不变 ==========

    async def aggregate_node(state: DocAgentState) -> dict:
        if progress:
            round_num = state.get("round", 1)
            fused = state.get("fused_results", [])
            await progress.publish_step("doc_worker", "aggregating",
                                        f"正在聚合第 {round_num} 轮结果…",
                                        stats={"found": len(fused) if fused else 0, "round": round_num})
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
        if progress:
            await progress.publish_step("doc_worker", "assessing", "正在评估检索完整性…")
        results = state.get("accumulated_results", [])
        entities = state.get("entities", {})
        thresholds = wc.get("thresholds", {})
        outcome = await assess_completeness(
            results=results,
            entities=entities,
            llm=llm,
            min_results=thresholds.get("min_results_converge", 5),
            vector_threshold=thresholds.get(
                "vector_similarity_converge", 0.85
            ),
        )
        state["assessment"] = outcome.verdict
        state["convergence_reason"] = f"{outcome.level}:{outcome.reason}"

        round_num = state.get("round", 1)
        max_rounds = state.get("max_rounds", 3)
        if outcome.verdict == "converge" or round_num >= max_rounds:
            state["rounds_used"] = round_num
            state["final_results"] = state.get("accumulated_results", [])

        return state

    async def expand_node(state: DocAgentState) -> dict:
        if progress:
            await progress.publish_step("doc_worker", "expanding", "正在扩展查询…")
        round_num = state.get("round", 1)
        original_query = state.get("original_query", "")
        results = state.get("accumulated_results", [])
        known_req_ids = set()
        for r in results:
            for rid in r.get("req_ids", []):
                known_req_ids.add(rid)
        if round_num <= 2:
            new_query = rule_based_expand(
                original_query, results, known_req_ids
            )
        else:
            new_query = await llm_based_expand(
                original_query, results, llm
            )
        state["current_query"] = new_query
        state["round"] = round_num + 1
        return state

    def should_continue(state: DocAgentState) -> Literal["expand", "END"]:
        if state.get("final_results") is not None:
            return "END"
        return "expand"

    # Graph 组装（不变）
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
    graph.add_conditional_edges(
        "assess", should_continue, {"expand": "expand", "END": END}
    )
    graph.add_edge("expand", "search")
    return graph.compile()

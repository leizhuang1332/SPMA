"""Code Agent 的 LangGraph StateGraph 定义。"""

from typing import Literal
from langgraph.graph import StateGraph, END
from spma.agents.code.state import CodeAgentState
from spma.agents.code.router import route_repos
from spma.agents.code.term_builder import build_search_terms
from spma.agents.code.completeness import assess_code_completeness
from spma.agents.code.ast_expander import expand_via_ast


def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 3,
    timeout_ms: int = 2000,
    progress=None,
) -> StateGraph:
    """Build Code Agent StateGraph with 4 nodes + conditional edge."""

    async def route_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "routing", "正在分析代码仓库…")
        entities = state.get("entities", {})
        route_result = await route_repos(entities, file_path_cache)
        state["candidate_repos"] = route_result["candidate_repos"]
        state["route_method"] = route_result["route_method"]
        state["route_confidence"] = route_result["route_confidence"]
        state["query"] = state.get("original_query", "")
        return state

    async def search_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "searching", "正在 ripgrep + AST 检索…")
        entities = state.get("entities", {})
        fallback_layer = state.get("fallback_layer", 0)
        search_terms = build_search_terms(entities)
        state["search_terms"] = search_terms

        if fallback_layer == 3 and llm is not None:
            try:
                query = state.get("query", "")
                prompt = f"为以下查询生成 3 个代码搜索关键词: {query}\n关键词:"
                resp_obj = await llm.ainvoke(prompt)
                resp = resp_obj.content
                new_terms = [t.strip() for t in resp.split(",") if t.strip()]
                search_terms["fuzzy_terms"] = list(set(
                    list(search_terms.get("fuzzy_terms", [])) + new_terms
                ))
            except Exception:
                pass

        candidate_repos = state.get("candidate_repos", [])
        ripgrep_results = await ripgrep_executor.search(search_terms, candidate_repos, fallback_layer)
        state["ripgrep_results"] = ripgrep_results

        tag_terms = search_terms.get("tag_terms", [])
        if tag_terms and candidate_repos:
            gitlog_results = await ripgrep_executor.search_gitlog(tag_terms, candidate_repos)
            if gitlog_results:
                state["ripgrep_results"] = ripgrep_results + gitlog_results

        return state

    async def assess_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "assessing", "正在评估检索完整性…")
        outcome = await assess_code_completeness(
            ripgrep_results=state.get("ripgrep_results", []),
            expanded_context=state.get("expanded_context", []),
            entities=state.get("entities", {}),
            call_depth=state.get("call_depth", 0),
            new_files_this_round=state.get("new_files_this_round", 0),
            fallback_layer=state.get("fallback_layer", 0),
            llm=llm,
        )
        state["assessment"] = outcome.verdict
        state["convergence_reason"] = f"{outcome.level}:{outcome.reason}"
        return state

    async def expand_node(state: CodeAgentState) -> dict:
        ripgrep_results = state.get("ripgrep_results", [])
        previous_expanded = state.get("expanded_context", [])
        new_expanded = await expand_via_ast(
            ripgrep_results=ripgrep_results,
            repo_paths=ripgrep_executor._repo_paths,
            ast_parser=ast_parser,
        )
        seen = {f["file_path"] for f in previous_expanded}
        added = 0
        for f in new_expanded:
            if f["file_path"] not in seen:
                previous_expanded.append(f)
                seen.add(f["file_path"])
                added += 1
        state["expanded_context"] = previous_expanded
        state["new_files_this_round"] = added
        state["call_depth"] = state.get("call_depth", 0) + 1
        state["round"] = state.get("round", 1) + 1
        if len(ripgrep_results) < 3 and state.get("fallback_layer", 0) < 3:
            state["fallback_layer"] = state.get("fallback_layer", 0) + 1
        return state

    def should_continue(state: CodeAgentState) -> Literal["expand", "END"]:
        assessment = state.get("assessment", "expand")
        round_num = state.get("round", 1)
        if assessment == "converge" or round_num >= max_rounds:
            state["rounds_used"] = round_num
            state["final_results"] = state.get("ripgrep_results", [])
            state["convergence_reason"] = state.get("convergence_reason", "max_rounds")
            return "END"
        return "expand"

    graph = StateGraph(CodeAgentState)
    graph.add_node("route", route_node)
    graph.add_node("search", search_node)
    graph.add_node("assess", assess_node)
    graph.add_node("expand", expand_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "search")
    graph.add_edge("search", "assess")
    graph.add_conditional_edges("assess", should_continue, {"expand": "expand", "END": END})
    graph.add_edge("expand", "search")
    return graph.compile()

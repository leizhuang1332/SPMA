"""Code Agent 的 LangGraph StateGraph 定义——v2: 3 节点薄包装。

v1: 4 节点内联（route / search / assess / expand）——循环耦合在 LangGraph 状态机
v2: 3 节点薄包装（route / explore / finalize）——多轮循环移至 CodeExplorer
"""
from typing import Literal
from langgraph.graph import StateGraph, END
from spma.agents.code.state import CodeAgentState
from spma.agents.code.router import route_repos
from spma.agents.code.explorer import CodeExplorer


def build_code_agent_graph(
    file_path_cache,
    ripgrep_executor,
    ast_parser,
    llm,
    max_rounds: int = 6,
    timeout_ms: int = 2000,
    progress=None,
    repo_registry=None,            # 新增（v2）：repo_registry 注入
    two_stage_threshold: int = 5,  # 新增（v2）：Stage 0 阈值
) -> StateGraph:
    """Build Code Agent StateGraph v2: 3 nodes + thin wrapper."""

    async def route_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "routing", "正在分析代码仓库…")
        entities = state.get("entities", {})
        query = state.get("original_query", "")
        route_result = await route_repos(
            entities=entities,
            file_path_cache=file_path_cache,
            query=query,
            repo_registry=repo_registry,
            llm=llm,
            two_stage_threshold=two_stage_threshold,
        )
        state["candidate_repos"] = route_result["candidate_repos"]
        state["route_method"] = route_result["route_method"]
        state["route_confidence"] = route_result["route_confidence"]
        state["query"] = query
        return state

    code_explorer = CodeExplorer(
        ripgrep_executor=ripgrep_executor,
        ast_parser=ast_parser,
        llm=llm,
        on_round_complete=_make_on_round_callback(progress),
        max_rounds=max_rounds,
    )

    async def explore_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "exploring", "正在多轮探索…")
        return await code_explorer.explore(state)

    async def finalize_node(state: CodeAgentState) -> dict:
        if progress:
            await progress.publish_step("code_worker", "finalizing", "正在汇总结果…")
        # CodeExplorer 已写回 ripgrep_results / expanded_context / convergence_reason / final_results
        # 此处仅做必要格式化
        return state

    graph = StateGraph(CodeAgentState)
    graph.add_node("route", route_node)
    graph.add_node("explore", explore_node)
    graph.add_node("finalize", finalize_node)
    graph.set_entry_point("route")
    graph.add_edge("route", "explore")
    graph.add_edge("explore", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def _make_on_round_callback(progress):
    """构造 ExplorerState 回调：每轮结束发可观测事件。"""
    async def on_round(es):
        if progress:
            await progress.publish_step(
                "code_worker", "round_complete",
                f"round={es.round} new_files={es.new_files_this_round} "
                f"converge={es.convergence.level if es.convergence else 'pending'}",
            )
    return on_round

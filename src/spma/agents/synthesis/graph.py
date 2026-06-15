"""Synthesis Agent 的 LangGraph StateGraph 定义。"""

from typing import Literal
from langgraph.graph import StateGraph, END
from spma.agents.synthesis.state import SynthesisAgentState
from spma.agents.synthesis.fusion import synthesize_fusion
from spma.agents.synthesis.generator import generate_draft_answer
from spma.agents.synthesis.auditor import audit_answer
from spma.agents.synthesis.transparency import generate_transparency_annotations


def build_synthesis_agent_graph(llm, audit_llm) -> StateGraph:
    async def fuse_node(state: SynthesisAgentState) -> dict:
        fused = synthesize_fusion(state.get("worker_outputs", []))
        state["fused_citations"] = fused
        return state

    async def generate_node(state: SynthesisAgentState) -> dict:
        draft = await generate_draft_answer(
            original_query=state.get("original_query", ""),
            fused_citations=state.get("fused_citations", []),
            worker_outputs=state.get("worker_outputs", []),
            llm=llm,
        )
        state["draft_answer"] = draft
        return state

    async def audit_node(state: SynthesisAgentState) -> dict:
        result = await audit_answer(
            draft_answer=state.get("draft_answer", ""),
            original_query=state.get("original_query", ""),
            fused_citations=state.get("fused_citations", []),
            llm=audit_llm,
        )
        worker_failures = [str(w.get("worker_type", "unknown")) for w in state.get("worker_outputs", []) if not w.get("citations")]
        annotations = generate_transparency_annotations(audit_result=result, worker_failures=worker_failures)

        # 在节点中递增 round（LangGraph 节点的返回值会被合并到 state，路由函数中的修改则不生效）
        next_round = state.get("round", 0) + 1
        state["round"] = next_round
        state["audit_result"] = {
            "verdict": result.verdict,
            "citation_coverage": result.citation_coverage,
            "contradictions": result.contradictions,
            "coverage_gaps": result.coverage_gaps,
            "unverified_claims": result.unverified_claims,
        }
        state["citation_coverage"] = result.citation_coverage
        state["contradictions"] = result.contradictions
        state["coverage_gaps"] = result.coverage_gaps
        state["annotations"] = annotations
        return state

    def should_continue(state: SynthesisAgentState) -> Literal["generate", "END"]:
        round_num = state.get("round", 0)
        max_rounds = state.get("max_rounds", 2)
        verdict = state.get("audit_result", {}).get("verdict", "pass")
        # 纯路由——不修改 state（条件边路由函数中的修改不会被 LangGraph 持久化）
        if verdict == "fix" and round_num < max_rounds:
            return "generate"
        # 终止：拼接 final_answer
        draft = state.get("draft_answer", "")
        annotations = state.get("annotations", [])
        annotation_text = "\n\n---\n" + "\n".join(f"{a['icon']} **{a['message']}**: {a.get('details', '')}" for a in annotations) if annotations else ""
        state["final_answer"] = draft + annotation_text
        state["convergence_reason"] = verdict
        return "END"

    graph = StateGraph(SynthesisAgentState)
    graph.add_node("fuse", fuse_node)
    graph.add_node("generate", generate_node)
    graph.add_node("audit", audit_node)
    graph.set_entry_point("fuse")
    graph.add_edge("fuse", "generate")
    graph.add_edge("generate", "audit")
    graph.add_conditional_edges("audit", should_continue, {"generate": "generate", "END": END})
    return graph.compile()

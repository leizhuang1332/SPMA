"""LLM 生成初稿——Sonnet 根据融合结果 + 用户问题生成 Markdown 回答。"""

from spma.agents.synthesis.prompts import GENERATION_PROMPT


async def generate_draft_answer(original_query: str, fused_citations: list[dict], worker_outputs: list[dict], llm) -> str:
    doc_results = _format_results([c for c in fused_citations if c.get("source_type") == "prd"], "文档")
    code_results = _format_results([c for c in fused_citations if c.get("source_type") == "code"], "代码")
    sql_results = _format_results([c for c in fused_citations if c.get("source_type") == "sql"], "数据库")
    worker_stats = _format_worker_stats(worker_outputs)
    prompt = GENERATION_PROMPT.format(
        original_query=original_query,
        doc_results=doc_results,
        code_results=code_results,
        sql_results=sql_results,
        worker_stats=worker_stats,
    )
    print("="*50)
    print(f"GENERATION_PROMPT: {prompt}")
    print("="*50)
    resp_obj = await llm.ainvoke(prompt)
    return resp_obj.content


def _format_results(citations: list[dict], label: str) -> str:
    if not citations:
        return f"[来自{label}] 无结果"
    lines = [f"[来自{label}]"]
    for i, c in enumerate(citations):
        snippet = c.get("snippet", c.get("content", ""))[:300]
        source_ref = c.get("source_path") or c.get("source_id", "?")
        lines.append(f"{i + 1}. {source_ref}\n> {snippet}")
    return "\n".join(lines)


def _format_worker_stats(worker_outputs: list[dict]) -> str:
    """根据 worker_outputs 生成各 Worker 的检索统计信息，用于降级兜底。

    当 RRF 融合后 fused_citations 为空时，LLM 可据此了解各 Worker 的
    执行状态，避免因无检索结果而完全无法回答。
    """
    if not worker_outputs:
        return "无 Worker 执行记录"
    lines = ["[Worker 执行统计]"]
    for w in worker_outputs:
        wtype = w.get("worker_type", "unknown")
        count = w.get("result_count", 0)
        confidence = w.get("confidence", 0)
        error = w.get("error", "")
        has_match = w.get("has_exact_match", False)
        status_parts = [f"结果数={count}"]
        if confidence > 0:
            status_parts.append(f"置信度={confidence:.0%}")
        if has_match:
            status_parts.append("精确匹配")
        if error:
            status_parts.append(f"错误={error}")
        lines.append(f"- {wtype}: {', '.join(status_parts)}")
    return "\n".join(lines)

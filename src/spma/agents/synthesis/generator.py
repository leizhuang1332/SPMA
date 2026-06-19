"""LLM 生成初稿——Sonnet 根据融合结果 + 用户问题生成 Markdown 回答。"""

from spma.agents.synthesis.prompts import GENERATION_PROMPT


async def generate_draft_answer(original_query: str, fused_citations: list[dict], worker_outputs: list[dict], llm) -> str:
    doc_results = _format_results([c for c in fused_citations if c.get("source_type") == "prd"], "文档")
    sql_results = _format_results([c for c in fused_citations if c.get("source_type") == "sql"], "数据库")
    prompt = GENERATION_PROMPT.format(original_query=original_query, doc_results=doc_results, sql_results=sql_results)
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
        source_id = c.get("source_id", c.get("chunk_id", "?"))
        lines.append(f"{i + 1}. [{source_id}] {snippet}")
    return "\n".join(lines)

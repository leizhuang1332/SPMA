"""线索扩展——R2 规则扩展 + R3 LLM 扩展。"""

import re
import logging
from collections import Counter

logger = logging.getLogger(__name__)


def rule_based_expand(original_query: str, results: list[dict], known_req_ids: set[str]) -> str:
    expansion_terms: list[str] = []

    for r in results[:5]:
        for rid in r.get("req_ids", []):
            if rid not in known_req_ids:
                expansion_terms.append(rid)

    all_terms: list[str] = []
    for r in results[:5]:
        content = r.get("content", r.get("snippet", ""))
        words = re.findall(r'[一-鿿\w]{2,}', content)
        all_terms.extend(words)

    term_counts = Counter(all_terms)
    frequent_terms = [term for term, count in term_counts.items() if count >= 2 and len(term) >= 2]
    expansion_terms.extend(frequent_terms[:5])

    seen = set()
    unique_terms = []
    for t in expansion_terms:
        if t.lower() not in seen and t not in original_query:
            seen.add(t.lower())
            unique_terms.append(t)

    if not unique_terms:
        return original_query
    return original_query + " " + " ".join(unique_terms[:8])


async def llm_based_expand(original_query: str, results: list[dict], llm) -> str:
    snippets = "\n".join(
        f"- {r.get('content', r.get('snippet', ''))[:200]}" for r in results[:5]
    )
    prompt = f"""根据以下检索结果和用户问题，生成 2-3 个扩展搜索方向（用换行分隔）。

用户问题: {original_query}

已有检索结果:
{snippets}

扩展搜索方向（每个方向一行，直接写关键词/短语，不用编号）:"""

    try:
        resp_obj = await llm.ainvoke(prompt)
        resp = resp_obj.content
        directions = [line.strip() for line in resp.strip().split("\n") if line.strip() and len(line.strip()) > 2]
        expanded = original_query + " " + " ".join(directions[:3])
        return expanded
    except Exception as e:
        logger.warning(f"LLM 线索扩展失败: {e}，使用原始 query")
        return original_query

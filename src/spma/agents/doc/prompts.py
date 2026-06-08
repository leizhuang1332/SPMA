"""Doc Agent LLM Prompt 模板。"""

COMPLETENESS_CHECK_PROMPT = """根据以下检索结果，判断信息是否足以回答用户问题。

检索结果摘要:
{snippets}

用户可能关注的实体: {entities_json}

只输出 JSON: {{"assessment": "sufficient" 或 "insufficient", "reason": "判断理由"}}"""

HYDE_PROMPT = """根据用户的问题，写一段假设性的文档内容（200-300字），模拟文档中可能如何描述相关信息。
只输出文档内容，不要标注或解释。

用户问题: {query}

假设的文档内容:"""

EXPANSION_PROMPT = """根据以下检索结果和用户问题，生成 2-3 个扩展搜索方向（用换行分隔）。

用户问题: {query}

已有检索结果:
{snippets}

扩展搜索方向（每个方向一行，直接写关键词/短语，不用编号）:"""

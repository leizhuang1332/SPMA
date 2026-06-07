"""Code Agent 的 LLM Prompt 模板。"""

COMPLETENESS_PROMPT = """判断以下代码搜索结果是否足以回答用户问题。

用户问题: {query}
代码搜索结果: {results}
调用链展开结果: {expanded_context}

输出 JSON: {"sufficient": true/false, "confidence": 0.0-1.0, "missing": ["缺失的方面"], "reasoning": "..."}
"""

CODE_TERM_TRANSLATION_PROMPT = """将以下中文业务术语翻译为可能的英文代码标识符。
输出 JSON: ["english_term_1", "english_term_2", ...]

中文术语: {chinese_term}
"""

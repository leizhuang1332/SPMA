"""Doc Agent 的 LLM Prompt 模板——完备度判断 Prompt。"""

COMPLETENESS_PROMPT = """判断以下 PRD 文档检索结果是否足以回答用户问题。

用户问题: {query}
检索结果: {results}

判断标准:
1. 是否找到了与问题直接相关的 PRD 片段？
2. 是否覆盖了问题的所有方面？
3. 如果有需求 ID，是否精确匹配到了对应的 PRD 文档？

输出 JSON: {"sufficient": true/false, "confidence": 0.0-1.0, "missing": ["缺失的方面"], "reasoning": "..."}
"""

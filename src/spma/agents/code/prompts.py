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

REFINE_TERMS_PROMPT = """基于以下上轮探索结果，重组更精准的代码搜索关键词和文件匹配模式。

用户查询: {query}
已有 expanded_context: {expanded_context_count} 个文件
已有 ripgrep_results: {ripgrep_results_count} 个匹配

要求：
1. 根据用户查询意图和目标语言（如 Java/Go/Python/TypeScript），推断应该匹配的文件类型/路径
2. glob_patterns 必须是 ripgrep --files 接受的合法 glob 格式
3. 多语言场景下可同时输出多个 pattern（如同时找 src 和 test）

输出 JSON:
{{
  "exact_terms": ["精确匹配词1", "精确匹配词2"],
  "fuzzy_terms": ["模糊匹配词1"],
  "tag_terms": ["req_id 或 author:xxx"],
  "glob_patterns": ["**/*Controller.java", "**/test_*.py"]
}}
"""

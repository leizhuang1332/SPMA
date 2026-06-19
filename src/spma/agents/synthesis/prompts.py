"""Synthesis Agent LLM Prompt 模板。"""

GENERATION_PROMPT = """你是一个企业知识助手。根据以下检索结果，回答用户问题。

用户问题: {original_query}

检索结果:
{doc_results}
{code_results}
{sql_results}

{worker_stats}

要求:
1. 用 Markdown 格式组织回答
2. 每条陈述必须标注引用来源 [源类型: 标识符]
3. 区分"确定的事实"和"推测的结论"
4. 如果跨源信息存在矛盾，显式标注
5. 如果有未能回答的部分，在末尾列出"""

AUDIT_PROMPT = """你是一个严谨的审计员。检查刚才生成的回答:
{audit_target}

检查项目:
1. 引用完整性: 每条陈述都有引用支撑吗？
2. 跨源一致性: Doc/SQL 的信息有矛盾吗？
3. 覆盖度: 用户原始问题 "{original_query}" 的每个方面都被回答了吗？

输出 JSON:
{{"citation_coverage": 0.xx, "unverified_claims": ["陈述1缺少引用", ...], "contradictions": [{{"claim_a": "...", "claim_b": "...", "source_a": "...", "source_b": "..."}}], "coverage_gaps": ["未回答的方面", ...], "verdict": "pass" | "fix" | "contradiction" | "gap"}}"""

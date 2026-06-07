"""SQL Agent 的 LLM Prompt 模板。"""

SQL_GENERATION_PROMPT = """根据 Schema 信息和业务元数据，将自然语言问题翻译为 SQL。

Schema 信息:
{schema_info}

业务元数据:
{business_metadata}

查询历史（含失败 SQL 和错误信息）:
{sql_history}

要求:
1. 只生成 SELECT 语句
2. 使用表名和列名的原始英文名称
3. 添加必要的 WHERE 条件（如软删除过滤、时间范围）
4. 聚合查询使用 GROUP BY
5. 输出格式: 仅 SQL，不要解释

用户问题: {query}
"""

SEMANTIC_VERIFY_PROMPT = """判断以下 SQL 执行结果是否在语义上正确回答了用户问题。

用户问题: {query}
执行的 SQL: {sql}
执行结果统计: {result_stats}

检查项目:
1. 返回的行数是否合理？
2. NULL 值比例是否异常？
3. 数值分布是否合理？
4. 结果是否在语义上回答了用户的问题？

输出 JSON: {"passed": true/false, "issues": ["问题1", ...], "confidence": 0.0-1.0}
"""

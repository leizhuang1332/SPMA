"""SQL Agent 的 LLM Prompt 模板。"""

SQL_GENERATION_SYSTEM = """你是一个精通 SQL 的数据库助手。根据提供的 Schema 信息，将用户的自然语言问题转化为只读 SQL 查询。

要求：
1. 只生成 SELECT 语句，禁止任何修改操作
2. 使用标准 SQL 语法（PostgreSQL 方言）
3. 如果用户问题涉及模糊时间范围（如"最近"、"上月"），使用相对时间函数
4. 如果 Schema 中提供了列的枚举值和业务含义，请在 SQL 中使用正确的值
5. 只返回 SQL 语句本身，不要加任何解释、markdown 标记或代码块

提供的 Schema 信息：
{schema_context}

用户上次生成的 SQL 有错误，请注意避免：
{error_feedback}
"""

SQL_GENERATION_USER = "请将以下问题转化为 SQL 查询：{query}"


def build_schema_context(schema_hits: list[dict]) -> str:
    """将 SchemaHit 列表转化为 LLM 可读的文本。"""
    lines = []
    for hit in schema_hits:
        lines.append(f"表: {hit['table_name']}")
        lines.append(f"描述: {hit.get('business_description', '')}")
        lines.append("列:")
        for col in hit.get("columns", []):
            extra = ""
            if col.get("business_meaning"):
                extra += f" — {col['business_meaning']}"
            if col.get("enum_values"):
                extra += f" (可选值: {col['enum_values']})"
            lines.append(f"  - {col['column_name']} ({col['data_type']}){extra}")
        if hit.get("foreign_keys"):
            lines.append("外键:")
            for fk in hit["foreign_keys"]:
                lines.append(f"  - {fk['column_name']} → {fk['referenced_table']}.{fk['referenced_column']}")
        if hit.get("few_shot_queries"):
            lines.append("示例查询:")
            for q in hit["few_shot_queries"]:
                lines.append(f"  - {q}")
        lines.append("")
    return "\n".join(lines)

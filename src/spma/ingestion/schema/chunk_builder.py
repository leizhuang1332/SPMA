"""SchemaChunk 构造器——将自省结果转化为可用于 embedding 的文本。"""


def build_business_description(
    table_name: str,
    columns: list[dict],
    foreign_keys: list[dict],
    table_comment: str = "",
) -> str:
    """构造表的业务描述文本（用于 BGE-M3 embedding）。

    关键: 不嵌入 DDL，只嵌入业务含义，避免共享列名导致向量相似度虚高。
    """
    lines = [f"{table_name} 表: {table_comment or f'{table_name} 表'}。"]
    lines.append("列:")

    for col in columns:
        parts = [f"  - {col['column_name']} ({col['data_type']})"]
        if col.get("comment"):
            parts.append(f": {col['comment']}")
        lines.append("".join(parts))

    if foreign_keys:
        lines.append("外键:")
        for fk in foreign_keys:
            lines.append(f"  - {fk['column_name']} → {fk['referenced_table']}.{fk['referenced_column']}")

    return "\n".join(lines)


def build_ddl(table_name: str, columns: list[dict]) -> str:
    """从自省结果构造 DDL 文本。"""
    col_defs = []
    for col in columns:
        nullable = "" if col["is_nullable"] else " NOT NULL"
        col_defs.append(f"  {col['column_name']} {col['data_type']}{nullable}")
    return f"CREATE TABLE {table_name} (\n" + ",\n".join(col_defs) + "\n);"

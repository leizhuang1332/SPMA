"""Schema 自省器——从 information_schema 提取数据库 Schema。"""

import psycopg


def introspect_schema(connection_string: str) -> dict[str, dict]:
    """读取数据库中所有用户表的 Schema 信息。

    Returns:
        {table_name: {columns, foreign_keys}}
    """
    conn = psycopg.connect(connection_string)
    schema = {}

    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            tables = [row[0] for row in cur.fetchall()]

            for table_name in tables:
                cur.execute("""
                    SELECT
                        c.column_name,
                        c.data_type,
                        c.is_nullable,
                        pg_catalog.col_description(
                            (SELECT c.oid FROM pg_catalog.pg_class c
                             WHERE c.relname = %s), c.ordinal_position
                        ) AS comment
                    FROM information_schema.columns c
                    WHERE c.table_schema = 'public' AND c.table_name = %s
                    ORDER BY c.ordinal_position
                """, (table_name, table_name))
                columns = [
                    {
                        "column_name": row[0],
                        "data_type": row[1],
                        "is_nullable": row[2] == "YES",
                        "comment": row[3],
                        "business_meaning": None,
                        "enum_values": None,
                    }
                    for row in cur.fetchall()
                ]

                cur.execute("""
                    SELECT
                        kcu.column_name,
                        ccu.table_name AS referenced_table,
                        ccu.column_name AS referenced_column
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.key_column_usage kcu
                        ON tc.constraint_name = kcu.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON tc.constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                        AND tc.table_schema = 'public'
                        AND tc.table_name = %s
                """, (table_name,))
                foreign_keys = [
                    {
                        "column_name": row[0],
                        "referenced_table": row[1],
                        "referenced_column": row[2],
                    }
                    for row in cur.fetchall()
                ]

                schema[table_name] = {
                    "columns": columns,
                    "foreign_keys": foreign_keys,
                }
    finally:
        conn.close()

    return schema

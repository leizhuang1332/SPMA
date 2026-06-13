"""LLM SQL 生成器——注入业务元数据 + few-shot 示例 + 上轮错误反馈。

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

from spma.agents.sql.prompts import (
    SQL_GENERATION_SYSTEM,
    SQL_GENERATION_USER,
    build_schema_context,
)
from spma.agents.sql.state import SchemaHit


def build_generation_prompt(
    query: str,
    schema_hits: list[SchemaHit],
    error_feedback: str = "",
) -> tuple[str, str]:
    """构造 SQL 生成的完整 Prompt。

    Returns:
        (system_prompt, user_prompt)
    """
    schema_context = build_schema_context(schema_hits) if schema_hits else "（无 Schema 信息可用）"
    system = SQL_GENERATION_SYSTEM.format(
        schema_context=schema_context,
        error_feedback=error_feedback or "（无错误，这是第一轮生成）",
    )
    user = SQL_GENERATION_USER.format(query=query)
    return system, user


async def generate_sql(
    query: str,
    schema_hits: list[SchemaHit],
    error_feedback: str = "",
    llm_client=None,
) -> str:
    """调用 LLM 生成 SQL。

    Args:
        query: 用户自然语言问题
        schema_hits: Schema RAG 检索结果
        error_feedback: 上轮错误信息
        llm_client: LLM 客户端实例（注入，便于测试）

    Returns:
        生成的 SQL 字符串（已清洗 markdown 标记）
    """
    system, user = build_generation_prompt(query, schema_hits, error_feedback)

    # 如果没有注入 LLM 客户端，使用默认
    if llm_client is None:
        from spma.llm import chat
        llm_client = chat

    response = await llm_client(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )

    # 清洗输出
    sql = response.strip()
    # 去掉 markdown 代码块
    if sql.startswith("```"):
        lines = sql.split("\n")
        # 去掉第一行 (```sql) 和最后一行 (```)
        sql = "\n".join(lines[1:-1]).strip()
    return sql

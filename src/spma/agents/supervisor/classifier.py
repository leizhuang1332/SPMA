"""Supervisor 意图分类器——LLM 结构化分类 + 规则兜底。"""

import json
import logging
from spma.models.classification import ClassificationResult

logger = logging.getLogger(__name__)

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "sources": {
            "type": "array",
            "items": {"type": "string", "enum": ["doc", "code", "sql"]},
            "minItems": 1, "maxItems": 3,
        },
        "is_cross_source": {"type": "boolean"},
        "query_type": {"type": "string", "enum": ["search", "data_query", "trace", "explain"]},
        "entities": {
            "type": "object",
            "properties": {
                "module": {"type": ["string", "null"]},
                "req_ids": {"type": "array", "items": {"type": "string"}},
                "time_range": {"type": ["string", "null"]},
                "version": {"type": ["string", "null"]},
                "table_names": {"type": "array", "items": {"type": "string"}},
                "column_names": {"type": "array", "items": {"type": "string"}},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "group_by": {"type": ["string", "null"]},
                "code_refs": {"type": "array", "items": {"type": "string"}},
                "person": {"type": ["string", "null"]},
                "doc_types": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    "required": ["sources", "is_cross_source", "query_type", "entities"],
}

CLASSIFICATION_PROMPT = """你是一个企业级查询路由器和分析师。你需要同时完成两项任务：
1. 判断用户问题需要查询哪些数据源
2. 从问题中抽取结构化的检索实体

# === 数据源定义 ===
- doc: PRD文档、产品需求、功能规格、需求变更记录、设计文档
- code: 代码实现、函数、类、方法、文件路径、bug修复、架构实现
- sql: 业务数据查询、统计报表、指标分析、数据量/频率/趋势

# === 分类规则 ===
1. 含需求ID格式 [REQ-XXXXX] 或 REQ-XXXXX -> sources 至少包含 "doc"
2. 含表名、列名、SQL关键词 -> sources 至少包含 "sql"
3. 含统计词(多少/数量/占比/趋势/排行/TOP) -> sources 至少包含 "sql"
4. 含文件路径(*.py/*.java/*.go/*.ts)、函数名、类名、代码关键词 -> sources 至少包含 "code"
5. 含"影响"/"对应"/"关联"/"改了哪些"/"涉及"等跨域关系词 -> is_cross_source=true
6. 极短模糊查询(<=8字)且无明确指向 -> sources=["doc","code","sql"], query_type="search"
7. 问"为什么"/"怎么做"/"逻辑" -> query_type="explain"

# === 实体抽取规则 ===
- req_ids: 匹配 REQ-\\d+ 或 "需求XXX" 格式
- code_refs: 匹配文件路径(*.py/*.java/*.go/*.ts)、函数名(下划线/驼峰)、类名(大写开头)
- table_names: 匹配已知表名 {{known_tables}}，中文表名转英文表名
- module: 匹配功能域: 认证/支付/订单/用户/库存/消息/搜索/报表/管理后台
- person: 匹配人名模式
- 未找到的字段: 设为 null 或空列表，不要编造

用户问题: {user_query}
对话历史: {conversation_history}"""


async def classify_and_extract(
    query: str,
    llm,
    conversation_history: str = "",
    known_tables: list[str] | None = None,
) -> ClassificationResult:
    """LLM 分类+实体抽取——单次 Haiku 调用。"""
    tables_str = ", ".join(known_tables[:50]) if known_tables else "通过上下文推断"
    prompt = CLASSIFICATION_PROMPT.format(
        user_query=query,
        conversation_history=conversation_history or "无",
    ).replace("{known_tables}", tables_str)

    try:
        # json_mode: 让模型直接输出 JSON，不依赖 function calling
        # DeepSeek V4 对 function calling 支持有限，json_mode 兼容性更好
        structured_llm = llm.with_structured_output(CLASSIFY_SCHEMA, method="json_mode")
        raw_result = await structured_llm.ainvoke(prompt)

        sources = raw_result.get("sources", ["doc", "code", "sql"])
        entities = raw_result.get("entities", {})
        for key in ["req_ids", "table_names", "column_names", "metrics", "code_refs", "doc_types"]:
            if key not in entities or entities[key] is None:
                entities[key] = []
        return ClassificationResult(
            sources=sources,
            is_cross_source=raw_result.get("is_cross_source", len(sources) > 1),
            query_type=raw_result.get("query_type", "search"),
            entities=entities,
        )
    except Exception as e:
        logger.warning(f"LLM 分类失败: {e}")
        return ClassificationResult(
            sources=["doc", "code", "sql"],
            is_cross_source=True,
            query_type="search",
            entities={},
        )

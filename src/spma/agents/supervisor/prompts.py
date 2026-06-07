"""Supervisor Agent 的 LLM Prompt 模板。

设计依据: SPMA-design-01 §5.1 分类 Prompt, §6.1 抽取 Prompt
"""

CLASSIFICATION_PROMPT = """你是一个查询路由器。分析用户问题，输出 JSON。

数据源定义:
- doc: PRD 文档、产品需求、功能规格、需求变更
- code: 代码实现、函数、类、文件路径、bug、架构
- sql: 业务数据、统计、报表、指标查询

分类规则:
- 含需求ID [REQ-XXXXX] → 至少包含 doc
- 含表名/列名/数据/统计/多少 → 至少包含 sql
- 含文件路径/函数名/代码/类名/bug → 至少包含 code
- "X影响Y"、"X对应哪个Z" → 跨源查询，标记 is_cross_source=true
- 模糊查询无法判断 → 默认三源全查，标记 query_type="search"

用户问题: {user_query}
"""

ENTITY_EXTRACTION_PROMPT = """从用户问题中抽取以下实体。找不到的字段设为 null 或空列表。

实体: module, req_ids, time_range, version, table_names, column_names, metrics, group_by, code_refs, person, doc_types

用户问题: {user_query}
"""

QUERY_EXPANSION_PROMPT = """为以下用户查询生成 3-5 个相关的搜索关键词或术语（仅输出关键词列表，用逗号分隔）。
查询: {query}
关键词:"""

QUERY_DECOMPOSE_PROMPT = """将以下复杂查询分解为 2-4 个独立的子查询。
查询: {query}
以 JSON 输出: [{"query": "子查询1", "target": "doc"}, ...]
"""

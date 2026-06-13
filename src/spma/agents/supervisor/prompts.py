"""Supervisor Agent 的 LLM Prompt 模板。

设计依据: SPMA-design-01 §5.1 分类 Prompt, §6.1 抽取 Prompt
"""

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
- table_names: 匹配已知表名 {known_tables}，中文表名转英文表名
- module: 匹配功能域: 认证/支付/订单/用户/库存/消息/搜索/报表/管理后台
- person: 匹配人名模式
- 未找到的字段: 设为 null 或空列表，不要编造

用户问题: {user_query}
对话历史: {conversation_history}"""

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

"""Supervisor 规则兜底——LLM 分类后逐条检查，遗漏则补刀。"""

import re
from spma.models.classification import ClassificationResult


def apply_rules(query: str, llm_result: ClassificationResult) -> ClassificationResult:
    sources = list(llm_result.get("sources", []))

    # Rule 1: 统计词 / 表相关 -> 补 sql
    if re.search(r"多少|数量|占比|趋势|排行|TOP|统计|报表|汇总|表结构|表名|表\b", query) and "sql" not in sources:
        sources.append("sql")

    # Rule 2: REQ-XXXXX -> 补 doc
    if re.search(r'REQ-\d{3,5}', query, re.IGNORECASE) and "doc" not in sources:
        sources.append("doc")

    # Rule 3: 代码模式 -> 补 code
    if re.search(r'\.(?:py|java|go|ts|js)\b|def\s+\w+|class\s+\w+|异常|报错|bug|实现|源码|代码|函数', query) and "code" not in sources:
        sources.append("code")

    # Rule 4: 极短模糊查询(<=8字) -> 三源全查
    if len(query.strip()) <= 8 and not sources:
        sources = ["doc", "code", "sql"]

    # Rule 5: 业务过程/模块 -> 补 doc + code
    if re.search(r'流程|模块', query):
        if "doc" not in sources:
            sources.append("doc")
        if "code" not in sources:
            sources.append("code")

    return ClassificationResult(
        sources=sources,
        is_cross_source=len(sources) > 1,
        query_type=llm_result.get("query_type", "search"),
        entities=llm_result.get("entities", {}),
    )

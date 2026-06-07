"""确认闸门规则引擎——对高风险 SQL 在执行前要求用户确认。

触发规则:
- 涉及财务指标: SUM/AVG + 金额/营收相关列名
- >=3 JOIN
- 全表扫描: COUNT(*) 或 SELECT * 无 WHERE
- 大时间范围: INTERVAL 含 year/month 级别

设计依据: SPMA-design-04 §3.4 用户确认闸门
"""

import re

FINANCIAL_COLUMNS = {
    "amount", "price", "revenue", "salary", "income", "cost",
    "profit", "fee", "payment", "balance", "total", "金额",
    "营收", "收入", "工资", "成本", "利润", "费用",
}

FINANCIAL_AGGREGATES = {"SUM", "AVG", "MAX", "MIN"}


def evaluate_confirmation_risk(sql: str) -> dict:
    """评估 SQL 是否需要用户确认。"""
    result = {
        "requires_user_confirmation": False,
        "reasons": [],
        "risk_level": "low",
        "tables_involved": [],
        "estimated_rows": None,
    }

    sql_upper = sql.upper()
    sql_lower = sql.lower()

    # 1. 检测财务指标
    has_financial_agg = any(agg in sql_upper for agg in FINANCIAL_AGGREGATES)
    has_financial_col = any(col in sql_lower for col in FINANCIAL_COLUMNS)
    if has_financial_agg and has_financial_col:
        result["reasons"].append("涉及财务指标聚合")
        result["risk_level"] = "medium"
        result["requires_user_confirmation"] = True

    # 2. 检测 >=3 JOIN
    join_count = len(re.findall(r"\bJOIN\b", sql_upper))
    if join_count >= 3:
        result["reasons"].append(f"跨 {join_count} 个表 JOIN，查询复杂度高")
        if result["risk_level"] != "high":
            result["risk_level"] = "medium"
        result["requires_user_confirmation"] = True

    # 3. 检测全表扫描
    has_where = "WHERE" in sql_upper
    has_count_star = "COUNT(*)" in sql_upper
    has_select_star = re.search(r"SELECT\s+\*", sql_upper) is not None
    if (has_count_star or has_select_star) and not has_where:
        result["reasons"].append("全表扫描——无 WHERE 条件，可能很慢或数据量大")
        result["risk_level"] = "medium"
        result["requires_user_confirmation"] = True

    # 4. 检测大时间范围
    if re.search(r"INTERVAL\s+'?\d+\s+(year|month)", sql, re.IGNORECASE):
        result["reasons"].append("查询涉及年级别/月级别时间跨度，数据量可能很大")
        result["risk_level"] = "medium"
        result["requires_user_confirmation"] = True

    # 提取表名
    table_matches = re.findall(r"\bFROM\s+(\w+)", sql_upper)
    join_matches = re.findall(r"\bJOIN\s+(\w+)", sql_upper)
    result["tables_involved"] = list(set(table_matches + join_matches))

    return result

"""SQL Guard 五层校验——非协商安全项。

Layer 1: SQLGlot 语法校验
Layer 2: DDL/DML 拦截 (DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER/CREATE/GRANT/EXECUTE)
Layer 3: 表/列存在性验证（含 Levenshtein 模糊纠错）
Layer 4: 性能保护（缺失WHERE/≥3 JOIN/笛卡尔积/缺失LIMIT）

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

import sqlglot
from sqlglot import errors as sqlglot_errors

from spma.agents.sql.state import GuardResult


FORBIDDEN_OPERATIONS = {
    "delete", "update", "drop", "insert", "truncate",
    "alter", "create", "grant", "execute", "revoke",
}


def validate_syntax(sql: str) -> GuardResult:
    """L1: SQLGlot 语法校验。"""
    result = GuardResult(
        passed=True,
        syntax_errors=[],
        forbidden_operations=[],
        table_existence_errors=[],
        performance_warnings=[],
        risk_level="low",
        requires_user_confirmation=False,
    )
    # 检测中文标点（语法错误，而非静默替换）
    chinese_punctuation = {"，": ",", "（": "(", "）": ")", "；": ";", "。": ".", "“": "\"", "”": "\""}
    for chinese_char, english_char in chinese_punctuation.items():
        if chinese_char in sql:
            result["passed"] = False
            result["syntax_errors"].append(f"检测到中文标点: {chinese_char!r}，请替换为 {english_char!r}")
            result["risk_level"] = "blocked"
    if not result["passed"]:
        return result
    try:
        parsed = sqlglot.parse_one(sql)
        if parsed is None:
            result["passed"] = False
            result["syntax_errors"].append("SQLGlot 解析失败：无法解析为有效 SQL")
            result["risk_level"] = "blocked"
    except sqlglot_errors.ParseError as e:
        result["passed"] = False
        errors_list = str(e).split("\n")
        result["syntax_errors"].extend(errors_list)
        result["risk_level"] = "blocked"
    except Exception as e:
        result["passed"] = False
        result["syntax_errors"].append(f"SQL 解析异常: {str(e)}")
        result["risk_level"] = "blocked"
    return result


def validate_no_ddl_dml(sql: str) -> GuardResult:
    """L2: 检测禁止的 DDL/DML 操作。"""
    result = GuardResult(
        passed=True,
        syntax_errors=[],
        forbidden_operations=[],
        table_existence_errors=[],
        performance_warnings=[],
        risk_level="low",
        requires_user_confirmation=False,
    )
    try:
        parsed = sqlglot.parse_one(sql)
        if parsed is None:
            return result
        # 检查根节点类型
        root_key = parsed.key.lower() if hasattr(parsed, "key") else ""
        if root_key in FORBIDDEN_OPERATIONS:
            result["passed"] = False
            result["forbidden_operations"].append(root_key.upper())
            result["risk_level"] = "blocked"
        # 递归遍历 AST 检查子语句中是否有禁止操作
        for node in parsed.dfs():
            key = node.key.lower() if hasattr(node, "key") else ""
            if key in FORBIDDEN_OPERATIONS and key.upper() not in result["forbidden_operations"]:
                result["passed"] = False
                result["forbidden_operations"].append(key.upper())
                result["risk_level"] = "blocked"
    except Exception:
        # 语法错误已在 L1 处理，L2 不重复报错
        pass
    return result

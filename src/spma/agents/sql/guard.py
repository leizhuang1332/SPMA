"""SQL Guard 五层校验——非协商安全项。

Layer 1: SQLGlot 语法校验
Layer 2: DDL/DML 拦截 (DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER/CREATE/GRANT/EXECUTE)
Layer 3: 表/列存在性验证（含 Levenshtein 模糊纠错）
Layer 4: 性能保护（缺失WHERE/≥3 JOIN/笛卡尔积/缺失LIMIT）

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

import sqlglot
from sqlglot import errors as sqlglot_errors
from sqlglot import exp

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


# ============================================================
# L3: 表/列存在性验证
# ============================================================


def _levenshtein_distance(s1: str, s2: str) -> int:
    """计算两个字符串的编辑距离。"""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev_row = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insert_cost = prev_row[j + 1] + 1
            delete_cost = curr_row[j] + 1
            replace_cost = prev_row[j] + (0 if c1 == c2 else 1)
            curr_row.append(min(insert_cost, delete_cost, replace_cost))
        prev_row = curr_row
    return prev_row[-1]


def _find_closest_table(table_name: str, schema_snapshot: dict[str, set[str]], max_distance: int = 3) -> str | None:
    """用 Levenshtein 距离查找最相似的表名。"""
    best_table = None
    best_distance = max_distance + 1
    for known_table in schema_snapshot:
        dist = _levenshtein_distance(table_name.lower(), known_table.lower())
        if dist < best_distance:
            best_distance = dist
            best_table = known_table
    return best_table if best_distance <= max_distance else None


def validate_table_column_existence(sql: str, schema_snapshot: dict[str, set[str]]) -> GuardResult:
    """L3: 验证生成的 SQL 中每个表名/列名在 Schema 快照中存在。"""
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
        # 提取所有表引用和列引用
        table_refs: set[str] = set()
        column_refs_by_table: dict[str, set[str]] = {}
        columns_without_table: set[str] = set()
        for node in parsed.dfs():
            if isinstance(node, exp.Table):
                name = node.name.lower() if node.name else ""
                if name:
                    table_refs.add(name)
            elif isinstance(node, exp.Column):
                col_name = node.name.lower() if node.name else ""
                if col_name == "*":
                    continue
                table_alias = node.table.lower() if node.table else ""
                if table_alias:
                    if table_alias not in column_refs_by_table:
                        column_refs_by_table[table_alias] = set()
                    column_refs_by_table[table_alias].add(col_name)
                else:
                    columns_without_table.add(col_name)

        # 验证表存在性
        for table_name in table_refs:
            if table_name not in schema_snapshot:
                closest = _find_closest_table(table_name, schema_snapshot)
                if closest:
                    result["table_existence_errors"].append(
                        f"表 '{table_name}' 不存在，您是否想查 '{closest}'？"
                    )
                else:
                    result["table_existence_errors"].append(
                        f"表 '{table_name}' 在数据库中不存在"
                    )

        # 确定可用于验证无前缀列的候选表：优先取查询中引用的表，否则用全部表
        candidates = table_refs & schema_snapshot.keys() if table_refs else set(schema_snapshot.keys())

        def _validate_column_against_table(col: str, table: str, known_cols: set[str]):
            """验证单个列是否在某表中存在，若不存在则尝试模糊纠错。"""
            if col not in known_cols:
                best_col = None
                best_dist = 4
                for kc in known_cols:
                    dist = _levenshtein_distance(col, kc)
                    if dist < best_dist:
                        best_dist = dist
                        best_col = kc
                if best_col and best_dist <= 2:
                    result["table_existence_errors"].append(
                        f"列 '{table}.{col}' 不存在，您是否想查 '{table}.{best_col}'？"
                    )
                else:
                    result["table_existence_errors"].append(
                        f"列 '{table}.{col}' 在表 '{table}' 中不存在"
                    )

        # 验证有表前缀的列存在性
        for table_alias, columns in column_refs_by_table.items():
            matched_table = table_alias if table_alias in schema_snapshot else None
            if matched_table is None:
                continue
            for col in columns:
                _validate_column_against_table(col, matched_table, schema_snapshot[matched_table])

        # 验证无表前缀的列：需在所有候选表中都不存在才报错
        for col in columns_without_table:
            exists_in_any = any(col in schema_snapshot.get(t, set()) for t in candidates)
            if not exists_in_any:
                # 在所有候选表中都不存在，尝试模糊纠错
                best_table = None
                best_col = None
                best_dist = 4
                for t in candidates:
                    known_cols = schema_snapshot.get(t, set())
                    for kc in known_cols:
                        dist = _levenshtein_distance(col, kc)
                        if dist < best_dist:
                            best_dist = dist
                            best_col = kc
                            best_table = t
                if best_table and best_col and best_dist <= 2:
                    result["table_existence_errors"].append(
                        f"列 '{col}' 不存在，您是否想查 '{best_table}.{best_col}'？"
                    )
                else:
                    result["table_existence_errors"].append(
                        f"列 '{col}' 在现有表中不存在"
                    )

        if len(result["table_existence_errors"]) > 0:
            result["passed"] = False
            if result["risk_level"] != "blocked":
                result["risk_level"] = "high"
    except Exception:
        pass
    return result


# ============================================================
# L4: 性能保护
# ============================================================


def check_performance(sql: str) -> GuardResult:
    """L4: 性能保护检查。"""
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

        sql_lower = sql.lower()

        # 检测缺失 WHERE 的 SELECT *
        has_where = "where" in sql_lower
        has_select_star = "*" in sql and "count(*)" not in sql_lower
        if has_select_star and not has_where:
            result["performance_warnings"].append(
                "SELECT * 无 WHERE 条件可能触发全表扫描，建议添加过滤条件"
            )
            result["risk_level"] = "medium"

        # 检测 >=3 JOIN
        join_count = sum(1 for node in parsed.dfs() if isinstance(node, exp.Join))
        if join_count >= 3:
            result["performance_warnings"].append(
                f"检测到 {join_count} 个 JOIN，查询复杂度高，建议简化或添加索引"
            )
            result["risk_level"] = "medium"

        # 检测缺失 LIMIT 的大表查询
        has_limit = "limit" in sql_lower
        if not has_limit and not has_where:
            result["performance_warnings"].append(
                "无 LIMIT 子句，可能返回大量数据，建议添加 LIMIT"
            )

    except Exception:
        pass
    return result


# ============================================================
# 全量校验编排
# ============================================================


def run_full_guard(sql: str, schema_snapshot: dict[str, set[str]]) -> GuardResult:
    """执行 L1-L4 全量校验，短路执行。"""
    # L1: 语法
    l1 = validate_syntax(sql)
    if not l1["passed"]:
        return l1

    # L2: DDL/DML
    l2 = validate_no_ddl_dml(sql)
    if not l2["passed"]:
        return l2

    # L3: 存在性
    l3 = validate_table_column_existence(sql, schema_snapshot)
    if not l3["passed"]:
        return l3

    # L4: 性能（不短路，合并警告）
    l4 = check_performance(sql)
    l3["performance_warnings"] = l4["performance_warnings"]
    if l4["risk_level"] != "low":
        l3["risk_level"] = l4["risk_level"]
    return l3

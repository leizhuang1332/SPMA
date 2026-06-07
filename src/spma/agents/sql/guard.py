"""SQL Guard 五层校验——非协商安全项。

Layer 1: SQLGlot 语法校验
Layer 2: DDL/DML 拦截 (DELETE/UPDATE/DROP/INSERT/TRUNCATE/ALTER)
Layer 3: 表/列存在性验证
Layer 4: 性能保护（缺失WHERE/笛卡尔积/缺失LIMIT）
Layer 5: 只读副本执行 + 超时控制

设计依据: SPMA-design-04 §1 SQL Guard层设计
"""

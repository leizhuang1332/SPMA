"""SQL Schema 摄入主流程。

information_schema 自省 → DDL + 列注释提取
→ 业务元数据注入（列注释/枚举值/外键/常见查询）
→ BGE-M3 嵌入 → PGVector

设计依据: SPMA-design-05 §3 SQL Schema摄入管道
"""

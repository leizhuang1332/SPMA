"""PRD 文档摄入主流程。

Confluence Webhook → Docling 解析 HTML → 递归语义分块
→ BGE-M3 嵌入 → PGVector + 元数据表 upsert

设计依据: SPMA-design-05 §1 PRD文档摄入管道
"""

"""SPMA Agent 模块。

5 个独立 Agent，每个作为 LangGraph 子图运行:
- Supervisor Agent: 编排中枢——意图分类、实体抽取、查询改写、多轮编排循环
- Doc Agent: PRD 文档检索——BM25+向量混合检索→完备度判断→线索扩展重搜
- Code Agent: 代码检索——ripgrep 实时搜索→完备度判断→调用链展开重搜
- SQL Agent: Text-to-SQL 执行——Schema RAG→LLM SQL生成→Guard→执行→语义验证
- Synthesis Agent: 审计融合——RRF融合→LLM生成初稿→引用完整性/跨源一致性/覆盖度检查

设计依据: SPMA-design-07 第一节 架构概述
"""

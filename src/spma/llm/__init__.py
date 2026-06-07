"""LLM 抽象层——统一 Claude API + 本地 Qwen3-8B 的调用接口。

模型分层:
- 高速路径 (<500ms): Claude Haiku → 意图分类/实体抽取/完备度判断/语义验证
- 质量路径 (<2s): Claude Sonnet → 回答生成/SQL 生成/复杂推理
- 降级路径 (本地): Qwen3-8B(vLLM) → 全部 LLM 不可用时的兜底

设计依据: SPMA-technology-selection §3 LLM模型选型
"""

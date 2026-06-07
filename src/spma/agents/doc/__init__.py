"""Doc Agent — PRD文档检索Agent。

检索Agent。BM25+向量混合检索 → 完备度判断 → 不够则线索扩展重搜 → 够了返回结果。

收敛契约: ≤3轮, 超时2s
设计依据: SPMA-design-02
"""

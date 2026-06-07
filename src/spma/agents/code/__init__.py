"""Code Agent — 代码检索Agent。

检索Agent。ripgrep实时搜索 → 完备度判断 → 不够则调用链展开重搜 → 够了返回结果。

收敛契约: ≤3轮, 超时2s
设计依据: SPMA-design-03
"""

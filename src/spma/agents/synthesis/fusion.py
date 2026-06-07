"""加权 RRF 融合算法——将多个 Worker 的引用结果合并排序。

公式: weighted_RRF(d) = Σ w_i / (k + rank_i(d))

设计依据: API-04 §6 RRF融合算法接口
"""

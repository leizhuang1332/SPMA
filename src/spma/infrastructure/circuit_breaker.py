"""熔断器（v2 启用，v1 用超时+重试）。

三态模型: CLOSED(正常) → OPEN(熔断, 连续5次失败, 持续30s)
         → HALF_OPEN(探测3次) → CLOSED / 重新OPEN

设计依据: SPMA-design-06 §6 熔断器设计
"""

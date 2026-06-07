"""可观测性模块——三层架构。

Layer 1: Langfuse — LLM 调用追踪 + Agent 循环追踪 + Token 成本 + Prompt 版本管理
Layer 2: OpenTelemetry — 全链路分布式追踪 (API→Supervisor→Worker→DB)
Layer 3: Grafana + Prometheus — 基础设施指标 + GPU 利用率 + 告警

设计依据: SPMA-technology-selection §9 可观测性选型
"""

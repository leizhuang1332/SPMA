"""Pydantic Settings——从环境变量 + YAML + DB Flags 加载配置。

分层: env vars (最高优先) → K8s ConfigMap → config/spma.yaml → DB feature_flags → 代码默认值
"""

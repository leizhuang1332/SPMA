"""配置加载模块。

配置来源优先级:
1. 环境变量（最高优先级）—— 数据库连接、API Key、Secrets
2. K8s ConfigMap —— 非敏感配置
3. YAML 配置文件 —— 功能配置、SLO、告警
4. 数据库 feature_flags 表 —— 运行时动态开关
5. 代码默认值 —— 兜底
"""

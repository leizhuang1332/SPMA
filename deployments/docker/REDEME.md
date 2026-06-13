# 创建的文件
|文件 |	用途|
|--|--|
|docker-compose.yml	| 主编排文件|
| .env.example	| 环境变量模板，复制为 .env 使用|
|init-db.sql	| PostgreSQL 初始化（创建库、扩展、元数据表）|
|otel-collector-config.yaml	| OpenTelemetry Collector 配置|
|prometheus.yml	| Promethues 抓取配置|

# 使用方式

```
cd deployments/docker

# 1. 配置环境变量
cp .env.example .env
# 编辑 .env，填写 ANTHROPIC_API_KEY

# 2. 启动核心基础设施（PostgreSQL + Redis）
docker compose up -d

# 3. 启动应用服务
docker compose --profile app up -d

# 4. 按需叠加可选服务
docker compose --profile es up -d          # Elasticsearch + IK 分词器
docker compose --profile gpu up -d         # vLLM 本地推理（需 GPU）
docker compose --profile observability up -d  # OTEL + Prometheus
docker compose --profile langfuse up -d    # Langfuse LLM 追踪

# 一键全量启动
docker compose --profile app --profile es --profile gpu --profile observability --profile langfuse up -d
```

# 服务端口一览

|服务 |	端口|
|--|--|
|PostgreSQL (pgvector)	| 5432|
|Redis	| 6379|
|API Gateway	| 8000|
|Agent Service	| 9000|
|Elasticsearch	| 9200|
|vLLM	| 8001|
|OTEL Collector (gRPC)	| 4317|
|Prometheus	| 9090|
|Langfuse	| 3000|

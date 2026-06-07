# SPMA — 企业级多源RAG智能问答系统

Supervisor-Powered Multi-Agent RAG 系统。

## 快速开始

### 环境要求
- Python 3.13+
- uv

### 安装

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 安装依赖
uv sync

# 安装开发依赖
uv sync --extra dev
```

### 运行

```bash
# 启动 API 服务
uv run spma-api

# 启动数据摄入
uv run spma-ingest
```

### 测试

```bash
uv run pytest
```

## 项目结构

参见 [项目目录结构设计](docs/superpowers/specs/2026-06-07-project-structure-design.md)

## 设计文档

- [全局概览](docs/designs/SPMA-design-00-global-overview.md)
- [5 Agent 架构](docs/designs/SPMA-design-07-agent-architecture.md)
- [技术选型](docs/SPMA-technology-selection.md)

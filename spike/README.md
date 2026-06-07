# SPMA Phase 0: Convergence Judgment Spike

验证 LLM 能否可靠判断检索结果"够了"。

## 快速开始

```bash
cd spike
uv sync
```

## 流程

1. 数据准备: corpus → retrieval → simulation → annotation
2. Prompt 工程: 3 方案 × 5 折 CV → 选优 → FP 分析 → 改进
3. 最终评估: 5 折 CV → 分层分析 → Go/No-Go 报告

详见设计文档: `docs/superpowers/specs/2026-06-07-phase0-convergence-spike-design.md`

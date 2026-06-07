"""SPMA 测试套件。

三层测试结构:
- unit/: 单元测试（mock, <5s 全量, CI 每次 commit）
- integration/: 集成测试（testcontainers, <2min, CI 每个 PR）
- eval/: 评估脚本（真实 LLM + 标注数据集, 手动触发）
- e2e/: 端到端测试（真实 LLM + 完整环境, 发布前触发）
"""

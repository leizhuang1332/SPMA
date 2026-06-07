# PRD: Phase 0 — 收敛判断 Spike

> **版本:** v1.0 | **日期:** 2026-06-07 | **状态:** PRD 完成
> **父文档:** [PRD-00 主概述](PRD-00-master-overview.md)
> **工期:** 0.5 人·月 | **优先级:** 🔴 最高（架构基石，gating item）

---

## 一、阶段目标

**验证 LLM 能否可靠判断检索结果"够了"** ——这是整个 5 Agent 架构的基石。如果 LLM 完备度判断的精确率不达标（< 80%），所有 Agent 的"多轮自主循环"设计都需要回退到 Plan B（纯确定性收敛 + 轮次上限）。

### 通关标准（Gating Criteria）

| 指标 | 目标 | 说明 |
|------|------|------|
| **LLM 完备度判断精确率** | **≥ 80%** | LLM 说"够了"时，至少 80% 概率真的够了 |
| 召回率 | 不做硬性要求 | 宁可多搜一轮也不少搜 |

**如果未通过 → 启用 Plan B：** 放弃 LLM 完备度判断，改用纯确定性收敛 + 轮次上限。Agent 在 max_rounds 内反复搜索直到命中确定性条件，去掉"自主判断"能力。

---

## 二、输入与依赖

### 2.1 前置依赖

| 依赖项 | 状态 | 说明 |
|--------|------|------|
| 设计文档 | ✅ 完成 | SPMA-design-00~07 |
| 100 条真实用户问题 | ❌ 需采集 | 从 Shadowing + 测试查询中收集 |
| 人工标注的"正确完备集合" | ❌ 需标注 | 为每条 query 标注 golden docs/code/tables |
| Claude Haiku API | ❌ 需申请 | 完备度判断用，~$0.001/次 |
| 标注工具 | ❌ 需准备 | Label Studio 或简单 CSV/JSON 标注界面 |

### 2.2 人员

| 角色 | 人数 | 时间 |
|------|------|------|
| 算法/NLP 工程师 | 1 人 | 0.5 月 |

---

## 三、任务拆解

### Task 0.1: 数据采集 — 收集 100 条真实用户问题（1 周）

**目标：** 建立覆盖三类数据源、多种查询类型的评估数据集。

**数据来源：**
1. **Shadowing 观察（60 条）：** 坐在 PM 和开发旁边，记录他们实际提出的问题
2. **历史查询日志（20 条）：** 从现有 Confluence/Git/DB 查询记录中提取
3. **人工构造边界 Case（20 条）：** 覆盖模糊查询、跨源查询、精确 ID 查询、短查询等

**标注维度：**
```json
{
  "query": "用户登录模块的PRD改了哪些内容？影响了哪些代码文件和数据库表？",
  "query_type": "cross_source",
  "golden_docs": ["doc_001:chunk_3", "doc_001:chunk_5"],        // 应该返回的文档片段
  "golden_code": ["src/auth/oauth.py", "src/auth/login.py"],    // 应该返回的代码文件
  "golden_sql_tables": ["users", "user_sessions"],              // 应该返回的数据库表
  "required_entities": {                                         // 关键实体
    "module": "用户登录",
    "req_ids": ["REQ-2024-0187"]
  },
  "sufficiency_threshold": 5,                                    // 多少条结果算"够了"
  "notes": "典型跨源溯源查询"
}
```

**数据分布要求：**
| 查询类型 | 数量 | 说明 |
|---------|------|------|
| 单源 Doc 查询 | 25 | 纯文档检索 |
| 单源 Code 查询 | 20 | 纯代码检索 |
| 单源 SQL 查询 | 20 | 纯数据查询 |
| 跨源双源查询 | 20 | Doc+Code / Doc+SQL / Code+SQL |
| 跨源三源查询 | 15 | Doc+Code+SQL |

**交付物：**
- `data/spike_eval_dataset.json` — 100 条标注数据
- `data/spike_annotation_guide.md` — 标注规范文档

---

### Task 0.2: 实验环境搭建（3 天）

**目标：** 搭建最小实验环境，能跑 LLM 完备度判断 + 计算精确率。

**技术栈：**
- Python 3.11+
- Claude Haiku API（完备度判断）
- JSON 日志记录

**核心脚本结构：**
```python
# spike_convergence_eval.py
def evaluate_convergence_accuracy(dataset: list[dict]) -> dict:
    """
    对数据集中的每条 query 跑完备度判断，计算精确率。
    
    流程:
    1. 对每条 query，模拟 Agent 第1轮检索结果
    2. 调用 Haiku 判断是否"信息充足"
    3. 对比 golden 标注，计算精确率
    """
    results = {
        "total": 0,
        "llm_said_sufficient": 0,     # LLM 说够了
        "llm_said_insufficient": 0,   # LLM 说不够
        "true_positive": 0,           # LLM说够了且真的够了
        "false_positive": 0,          # LLM说够了但实际不够 ← 关键指标
        "true_negative": 0,           # LLM说不够且真的不够
        "false_negative": 0,          # LLM说不够但实际够了
    }
    # ... 评估逻辑
    return results
```

**交付物：**
- `spike/convergence_eval.py` — 评估脚本
- `spike/prompts/completeness_judge.md` — 完备度判断 Prompt
- `spike/config.yaml` — Haiku API 配置

---

### Task 0.3: Prompt 工程 — 完备度判断 Prompt（1 周）

**目标：** 设计和迭代完备度判断的 LLM Prompt，达到最佳精确率。

**三种 Prompt 方案对比：**

| 方案 | 思路 | 预期精确率 | 预期延迟 |
|------|------|-----------|---------|
| **A: 简洁判断** | 直接问"这些结果够不够回答用户问题？" | ~75% | ~200ms |
| **B: 结构化检查** | 逐项检查：实体覆盖、数量充足、时间范围 | ~82% | ~350ms |
| **C: 多维度评分** | 对每个维度打分，总分≥阈值则充足 | ~85% | ~500ms |

**Prompt 模板（方案 B，推荐起步方案）：**

```
你是一个检索质量评估器。判断当前检索结果是否足以回答用户的问题。

用户问题: {query}

检索到的文档/代码/数据:
{retrieved_items}

请逐项检查:
1. 关键实体覆盖: 用户问到的需求ID、文件名、表名是否都检索到了？
2. 数量充足性: 返回的结果数量是否足够（文档≥5条、代码≥3条、SQL表≥1个）？
3. 时间范围: 如果用户指定了时间范围，结果是否在范围内？
4. 语义覆盖: 用户问题的每个子问题是否都有对应的检索结果？

输出 JSON:
{
  "entity_coverage": {"covered": [...], "missing": [...]},
  "count_sufficient": true/false,
  "time_range_ok": true/false,
  "semantic_coverage": "full" | "partial" | "insufficient",
  "verdict": "sufficient" | "insufficient",
  "confidence": 0.0-1.0,
  "missing_info": "如果 insufficient，说明缺少什么信息"
}
```

**迭代流程：**
1. 在 30 条 dev set 上测试三种 Prompt 方案
2. 选出精确率最高的方案
3. 分析 false positive 案例（LLM 说够了但实际不够）
4. 针对性地加入更多检查项或示例
5. 在 70 条 test set 上最终评估

**交付物：**
- `spike/prompts/completeness_judge_v1.md` — 各版本 Prompt
- `spike/prompts/ablation_results.csv` — Prompt 消融实验结果

---

### Task 0.4: 精确率评估与分析（1 周）

**目标：** 在完整 100 条数据集上跑最终评估，产出分析报告。

**评估矩阵：**

```
                          实际够了    实际不够
LLM 判断"够了"             TP          FP ← 这项必须 ≤ 20%
LLM 判断"不够"             FN          TN
```

**精确率 = TP / (TP + FP)**
**目标: ≥ 80%**

**分层分析（必做）：**

| 分层维度 | 分析问题 |
|---------|---------|
| 按 query_type | 哪种查询类型精确率最低？ |
| 按数据源 | Doc/Code/SQL 哪个源的完备度判断最难？ |
| 按查询长度 | 短查询 vs 长查询的精确率差异？ |
| 按实体丰富度 | rich/partial/bare 三级的精确率？ |
| 按结果数量 | 结果多的时候 LLM 是否倾向"够了"？ |

**FP 案例分析（必做）：**
- 每个 FP 案例记录：query、检索结果、LLM 判断理由、为什么实际不够
- 分类：a) 实体遗漏 b) 数量不足 c) 语义理解错误 d) 其他
- 输出 FP 案例集 → 决定是否可修复（Prompt 调整）还是结构性缺陷（放弃 LLM 判断）

**交付物：**
- `spike/evaluation_report.md` — 最终评估报告（含精确率、分层分析、FP 案例分析、Plan B 建议）

---

## 四、阶段输出与交付物

| 交付物 | 路径 | 格式 |
|--------|------|------|
| 标注数据集 | `data/spike_eval_dataset.json` | JSON |
| 标注规范 | `data/spike_annotation_guide.md` | Markdown |
| 评估脚本 | `spike/convergence_eval.py` | Python |
| 完备度判断 Prompt | `spike/prompts/completeness_judge.md` | Markdown |
| 消融实验结果 | `spike/prompts/ablation_results.csv` | CSV |
| **最终评估报告** | `spike/evaluation_report.md` | Markdown |

---

## 五、验收标准

### 5.1 通关条件（必须满足）

- [ ] **LLM 完备度判断精确率 ≥ 80%**（在 100 条标注数据上）
- [ ] 分层分析完成，明确了各类查询的精确率差异
- [ ] FP 案例分析完成，明确了失败模式和改进方向（或确认结构性缺陷）

### 5.2 Go/No-Go 决策

| 结果 | 决策 | 后续行动 |
|------|------|---------|
| 精确率 ≥ 85% | ✅ **Go** — 强烈信心进入 Phase 1 | 开始实现 Agent 循环 + LLM 完备度判断 |
| 精确率 80%-85% | ✅ **Go** — 有条件进入 Phase 1 | Agent 循环中增加确定性收敛的权重，LLM 判断作为辅助 |
| 精确率 70%-80% | ⚠️ **Risk** — 需评审 | 分析 FP 案例是否可通过 Prompt 改进修复；若可修复则重新评估 |
| 精确率 < 70% | ❌ **No-Go** — 启用 Plan B | 放弃 LLM 完备度判断，改用纯确定性收敛 + 轮次上限 |

### 5.3 即使 No-Go 也交付

Plan B 设计文档：`spike/plan_b_design.md`
- 纯确定性收敛规则定义
- 各 Agent 的 max_rounds 配置
- 与 LLM 判断方案的 diff（性能、成本、准确率差异）

---

## 六、风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| 100 条标注质量不高（标注者理解不一致）| 中 | 双人标注 20 条计算 IAA；≥ 0.8 才通过；标注规范明确 |
| Haiku API 不可用或延迟过高 | 低 | 预留 Qwen3-8B 本地替代方案（但结果不作为正式评估） |
| Prompt 工程陷入局部最优 | 中 | 固定 dev/test split，在 dev 上迭代、test 上一次评估 |
| 数据覆盖不均衡 | 低 | 按 query_type 分层采样（见 Task 0.1 的分布要求） |

---

## 七、里程碑与时间表

| 周 | 里程碑 | 关键任务 |
|-----|--------|---------|
| **Week 1** | 数据就绪 | Task 0.1（数据采集+标注）+ Task 0.2（环境搭建） |
| **Week 2** | Prompt 定型 | Task 0.3（Prompt 工程+消融实验）+ Task 0.4 前半（dev set 评估） |
| **Week 2 末** | **Go/No-Go 决策** | Task 0.4（test set 最终评估 + 分析报告） |

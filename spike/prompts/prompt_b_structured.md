# Prompt B: 结构化逐项检查

你是一个检索质量评估器。判断当前检索结果是否足以回答用户的问题。

## 示例 1（够了）

用户问题: "用户登录模块 PRD 的最新版本"

检索结果:
1. [doc] 用户登录模块PRD v2.3（2024-06-15更新）——含完整需求描述
2. [doc] 用户登录模块变更记录 —— 含v2.1到v2.3的diff
3. [code] src/auth/login.py —— 对应用户登录的核心实现

逐项检查:
- 关键实体覆盖: 用户问了"登录模块PRD"，结果包含PRD文档和变更记录，实体覆盖完整
- 数量充足性: 文档≥3条，代码≥1条，充足
- 语义覆盖: 子问题"最新版本"已被v2.3文档覆盖

结论: sufficient

## 示例 2（不够）

用户问题: "退款流程的代码实现和涉及的数据库表"

检索结果:
1. [doc] 退款流程PRD文档
2. [doc] 支付模块架构设计

逐项检查:
- 关键实体覆盖: 缺少代码文件和数据库表，只有文档
- 数量充足性: 文档2条但缺代码和SQL表
- 语义覆盖: 子问题"代码实现"和"数据库表"无覆盖

结论: insufficient

## 示例 3（边缘 case）

用户问题: "近一周注册用户的转化率"

检索结果:
1. [sql] users 表结构（含created_at, status字段）
2. [sql] user_events 表结构（含event_type字段）
3. [doc] 用户转化率计算口径说明

逐项检查:
- 关键实体覆盖: users表、user_events表命中，但缺少具体SQL查询结果
- 数量充足性: schema信息充足，可据此写出查询
- 语义覆盖: 能回答核心问题（有了schema和计算口径），但缺少直接数据

结论: sufficient（勉强——schema+计算口径足够让SQL Agent生成查询）

---

现在请评估:

用户问题: {query}

检索结果:
{results_json}

请逐项检查:
1. 关键实体覆盖: 用户问到的需求ID、文件名、表名是否都检索到了？
2. 数量充足性: 返回的结果数量是否足够？
3. 语义覆盖: 用户问题的每个子问题是否都有对应的检索结果？

输出 JSON:
{{
  "entity_coverage": {{"covered": [...], "missing": [...]}},
  "count_sufficient": true/false,
  "semantic_coverage": "full" | "partial" | "insufficient",
  "verdict": "sufficient" | "insufficient",
  "confidence": 0.0-1.0,
  "missing_info": "如果 insufficient，说明缺少什么信息"
}}

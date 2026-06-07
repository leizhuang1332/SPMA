# Query 数据目录

## 文件说明

- `raw_queries_shadowing.txt` — Shadowing 观察采集（50条），直接从 PM/开发对话中记录，一行一条
- `raw_queries_historical.txt` — 历史日志提取（30条），从 Confluence/Git/DB 日志提取
- `raw_queries_synthetic.txt` — 人工构造边界 case（20条），按分类矩阵补齐

## 分类矩阵

|                | 短查询 (<15字)  | 中查询 (15-50字) | 长查询 (>50字) |
|----------------|----------------|------------------|----------------|
| 单源 Doc       | ≥1条           | ≥1条             | ≥1条           |
| 单源 Code      | ≥1条           | ≥1条             | ≥1条           |
| 单源 SQL       | ≥1条           | ≥1条             | ≥1条           |
| 跨源双源       | ≥1条           | ≥1条             | ≥1条           |
| 跨源三源       | ≥1条           | ≥1条             | ≥1条           |

## 边界 case 类型（必须覆盖）

- 精确 ID 查询: "REQ-2024-0187 相关的代码文件"
- 模糊自然语言: "退款咋做的"
- 时间范围: "上周改了哪些跟登录有关的代码"
- 否定/反事实: "有哪些模块没有对应的 PRD 文档"

## approved_100_queries.json 格式

```json
[
  {
    "id": "q001",
    "text": "用户登录模块的PRD改了哪些内容？影响了哪些代码文件和数据库表？",
    "query_type": "cross_source",
    "source_types": ["doc", "code", "sql"],
    "source": "shadowing",
    "collected_at": "2026-06-10",
    "notes": ""
  }
]
```

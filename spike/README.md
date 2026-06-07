# SPMA Phase 0: Convergence Judgment Spike

验证 LLM 能否可靠判断检索结果"够了"。

## 环境准备

```bash
cd spike
uv sync
```

配置 `config.yaml` 中的 Anthropic API key（通过环境变量 `ANTHROPIC_API_KEY`）。

## 数据准备

### 1. 语料收集

将三类数据源导出为 JSON 放入 `data/corpus/`:
- `docs_sample.json` — ~200页 Confluence 文档 chunks
- `code_sample.json` — ~300个代码文件
- `sql_schema_sample.json` — 完整 table DDL

### 2. Query 收集

将 query 放入 `data/queries/`:
- `raw_queries_shadowing.txt` — 50条
- `raw_queries_historical.txt` — 30条
- `raw_queries_synthetic.txt` — 20条

审核后整理为 `data/queries/approved_100_queries.json`。

### 3. 构建索引

```bash
cd spike
python -c "
from retrieval.corpus_loader import CorpusLoader
from retrieval.indexer import HybridIndexer

loader = CorpusLoader('data/corpus')
all_docs = []
for docs in loader.load_all().values():
    all_docs.extend(docs)

indexer = HybridIndexer()
indexer.index(all_docs)
indexer.save('data/index')
print(f'索引构建完成: {len(all_docs)} 个文档')
"
```

### 4. 生成判断点 & 标注

```bash
# 运行多轮模拟器生成判断点
python -c "
import json
from simulation.multi_round_simulator import MultiRoundSimulator
from retrieval.corpus_loader import CorpusLoader
from retrieval.indexer import HybridIndexer
from retrieval.searcher import HybridSearcher

# 加载索引
indexer = HybridIndexer()
indexer.load('data/index')
searcher = HybridSearcher(indexer)

# 加载 queries 和 golden results
with open('data/queries/approved_100_queries.json') as f:
    queries = json.load(f)
with open('data/golden_eval_dataset.json') as f:
    golden = json.load(f)

# 构建 golden 映射
golden_map = {}
for item in golden:
    qid = item['query_id']
    if qid not in golden_map:
        golden_map[qid] = []
    golden_map[qid].extend(item.get('golden_doc_ids', []))
    golden_map[qid].extend(item.get('golden_code_ids', []))
    golden_map[qid].extend(item.get('golden_sql_ids', []))

# 模拟
simulator = MultiRoundSimulator(searcher, golden_map)
outputs = simulator.simulate_batch(queries)
points = simulator.to_judgment_points(outputs)

with open('data/judgment_points_stage1.json', 'w') as f:
    json.dump(points, f, ensure_ascii=False, indent=2)

print(f'生成 {len(points)} 个判断点，待标注')
"
```

然后进行双人标注，结果写入 `data/golden_eval_dataset.json`。

### 5. IAA 校验

```bash
python -c "
from annotation.iaa import compute_iaa, print_iaa_report
result = compute_iaa('data/golden_eval_dataset.json')
print_iaa_report(result)
"
```

### 6. 消融实验

```bash
python -m spike.eval.convergence_eval --mode ablation
```

### 7. FP 分析 & 改进 Prompt

基于消融结果改进 Prompt 后，保存为 `prompts/prompt_final.md`。

### 8. 最终评估

```bash
python -m spike.eval.convergence_eval --mode final --prompt-version final
```

## 目录结构

```
spike/
├── config.yaml              # 配置文件
├── pyproject.toml           # 依赖
├── README.md                # 本文件
├── data/
│   ├── corpus/              # 检索语料 (JSON)
│   ├── queries/             # 用户查询
│   ├── golden_eval_dataset.json  # 标注后的数据集
│   └── annotation_rubric.md      # 标注规范
├── retrieval/               # 检索管线
├── simulation/              # 多轮模拟器
├── prompts/                 # Prompt 模板
├── eval/                    # 评估脚本
├── annotation/              # 标注工具
├── tests/                   # 测试
└── reports/                 # 评估报告
```

## 设计文档

[Phase 0 收敛判断 Spike 设计](../../docs/superpowers/specs/2026-06-07-phase0-convergence-spike-design.md)

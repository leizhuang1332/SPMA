# tests/e2e/test_sql_e2e.py
"""E2E 测试——用真实 LLM 跑 eval dataset，计算 Execution Accuracy。"""
import json
import pytest
import sqlglot

pytestmark = pytest.mark.e2e


def load_eval_dataset():
    with open("tests/eval/sql_eval_dataset.json") as f:
        return json.load(f)


def evaluate_execution_accuracy(generated_sql: str, golden_sql: str) -> bool:
    """简化版 Execution Accuracy: 比较 SQL 结构语义。"""
    try:
        gen_ast = sqlglot.parse_one(generated_sql)
        gold_ast = sqlglot.parse_one(golden_sql)
        gen_select = str(gen_ast.find(sqlglot.exp.Select)) if gen_ast else ""
        gold_select = str(gold_ast.find(sqlglot.exp.Select)) if gold_ast else ""
        return gen_select.lower().replace(" ", "") == gold_select.lower().replace(" ", "")
    except Exception:
        return generated_sql.lower().strip() == golden_sql.lower().strip()


@pytest.mark.parametrize("case", load_eval_dataset())
async def test_sql_e2e(case):
    """对每条 golden query 跑端到端 Agent 循环。"""
    pytest.skip("E2E tests require real LLM API and PostgreSQL — run manually in Slice 4+")

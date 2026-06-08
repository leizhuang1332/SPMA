"""Doc + SQL → Synthesis 跨源 E2E 测试。"""

import pytest


@pytest.mark.e2e
class TestCrossSourceE2E:
    async def test_doc_sql_synthesis_basic(self, synthesis_agent):
        """Doc + SQL 结果 → Synthesis 生成融合回答。"""
        doc_output = {
            "worker_type": "doc",
            "citations": [{
                "chunk_id": "cross-doc-001",
                "snippet": "用户登录模块支持用户名密码和手机验证码两种方式。",
                "source_type": "prd",
                "source_id": "confluence:test-001",
            }]
        }
        sql_output = {
            "worker_type": "sql",
            "citations": [{
                "chunk_id": "cross-sql-001",
                "snippet": "users 表包含 username, password_hash, phone 字段",
                "source_type": "sql",
                "source_id": "public.users",
            }]
        }

        result = await synthesis_agent.synthesize(
            original_query="用户登录有哪些方式？",
            worker_outputs=[doc_output, sql_output],
        )

        assert result["final_answer"] is not None
        assert len(result["final_answer"]) > 50
        assert result["convergence_reason"] in ("pass", "fix", "contradiction", "gap")

    async def test_cross_source_contradiction_detection(self, synthesis_agent):
        """Doc 说 3 步，SQL 显示 2 步 → 应检测到矛盾。"""
        doc_output = {
            "worker_type": "doc",
            "citations": [{
                "chunk_id": "contra-doc-001",
                "snippet": "支付流程包含三步：下单、支付确认、发货。",
                "source_type": "prd",
                "source_id": "confluence:pay",
            }]
        }
        sql_output = {
            "worker_type": "sql",
            "citations": [{
                "chunk_id": "contra-sql-001",
                "snippet": "orders 表的状态字段只有两种：pending、completed。",
                "source_type": "sql",
                "source_id": "public.orders",
            }]
        }

        result = await synthesis_agent.synthesize(
            original_query="支付流程有几步骤？",
            worker_outputs=[doc_output, sql_output],
        )

        annotations = result.get("annotations", [])
        contradiction_found = any("矛盾" in a.get("message", "") or a.get("icon") == "⚡" for a in annotations)
        assert contradiction_found or result["convergence_reason"] == "contradiction"

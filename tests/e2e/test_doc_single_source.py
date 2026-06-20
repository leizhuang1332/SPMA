"""Doc Agent 单源 E2E 测试——需要真实 ES + PGVector 服务。"""

import pytest


@pytest.mark.e2e
class TestDocAgentE2E:
    async def test_precise_search_by_req_id(self, doc_agent, test_es_client):
        """通过 req_id 精确检索——100% 命中。"""
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-req-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "source_path": "https://confluence.example.com/display/SPACE/Login+Module",
                "req_ids": ["REQ-TEST-001"],
                "content": "## 用户登录模块\n用户登录需要用户名和密码。[REQ-TEST-001]",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "登录模块 PRD",
            }
        ])

        result = await doc_agent.search(query="REQ-TEST-001", entities={"req_ids": ["REQ-TEST-001"]})
        assert result.get("has_exact_match") or len(result.get("final_results", [])) >= 1
        final_results = result.get("final_results", [])
        if final_results:
            assert final_results[0].get("source_path") == "https://confluence.example.com/display/SPACE/Login+Module"

    async def test_semantic_search_short_query(self, doc_agent, test_es_client):
        """短语义查询——应返回相关结果。"""
        await test_es_client.index_chunks([
            {
                "chunk_id": "test-sem-001",
                "source_id": "confluence:test-001",
                "source_type": "confluence",
                "source_path": "https://confluence.example.com/display/SPACE/Payment+Flow",
                "req_ids": [],
                "content": "## 支付流程\n支付流程包括用户下单、第三方支付回调、订单状态更新三个步骤。",
                "doc_type": "prd",
                "version": "v1.0",
                "updated_at": "2026-06-01T00:00:00Z",
                "chunk_index": 0,
                "page_title": "支付流程 PRD",
            }
        ])
        result = await doc_agent.search(query="支付流程", entities={"req_ids": [], "module": None})
        assert len(result.get("final_results", [])) >= 1
        final_results = result.get("final_results", [])
        if final_results:
            assert "source_path" in final_results[0]

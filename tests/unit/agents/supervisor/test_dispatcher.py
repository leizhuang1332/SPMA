"""dispatcher 工具函数测试——normalize_citations 和 extract_discovered_entities。"""

import pytest
from spma.agents.supervisor.dispatcher import (
    normalize_citations,
    WORKER_TYPE_TO_SOURCE_TYPE,
    extract_discovered_entities,
)


class TestNormalizeCitations:
    """测试 normalize_citations——Worker 边界统一注入合约 source_type。"""

    def test_doc_worker_gets_prd_source_type(self):
        citations = [
            {"chunk_id": "d1", "content": "PRD 内容"},
            {"chunk_id": "d2", "content": "更多 PRD"},
        ]
        result = normalize_citations("doc", citations)
        assert result is citations  # 原地修改
        assert all(c["source_type"] == "prd" for c in result)

    def test_code_worker_gets_code_source_type(self):
        citations = [
            {"file_path": "main.py", "match_text": "def foo"},
        ]
        result = normalize_citations("code", citations)
        assert all(c["source_type"] == "code" for c in result)

    def test_sql_worker_gets_sql_source_type(self):
        citations = [
            {"chunk_id": "s1", "content": "SELECT * FROM orders"},
        ]
        result = normalize_citations("sql", citations)
        assert all(c["source_type"] == "sql" for c in result)

    def test_unknown_worker_type_falls_back_to_worker_type_string(self):
        citations = [{"chunk_id": "x1", "content": "unknown"}]
        result = normalize_citations("custom_worker", citations)
        assert all(c["source_type"] == "custom_worker" for c in result)

    def test_empty_citations_returns_empty_list(self):
        result = normalize_citations("doc", [])
        assert result == []

    def test_preserves_existing_fields(self):
        """normalize_citations 不覆盖其他字段。"""
        citation = {
            "chunk_id": "d1",
            "content": "完整内容",
            "score": 0.95,
            "metadata": {"source_type": "markdown_dir"},
        }
        result = normalize_citations("doc", [citation])
        assert result[0]["chunk_id"] == "d1"
        assert result[0]["content"] == "完整内容"
        assert result[0]["score"] == 0.95
        # 原始 handler 信息保留在 metadata 中
        assert result[0]["metadata"]["source_type"] == "markdown_dir"
        # 顶层 source_type 被正确设置
        assert result[0]["source_type"] == "prd"

    def test_contract_mapping_is_complete(self):
        """合约映射表覆盖所有已知 worker 类型。"""
        assert WORKER_TYPE_TO_SOURCE_TYPE == {"doc": "prd", "code": "code", "sql": "sql"}


class TestExtractDiscoveredEntities:
    """测试 extract_discovered_entities——跨源实体桥接。"""

    def test_merges_discovered_entities_across_workers(self):
        outputs = [
            {"discovered_entities": {"req_ids": ["REQ-1", "REQ-2"], "table_names": ["orders"]}},
            {"discovered_entities": {"req_ids": ["REQ-2", "REQ-3"], "code_refs": ["main.py"]}},
        ]
        result = extract_discovered_entities(outputs)
        assert set(result["req_ids"]) == {"REQ-1", "REQ-2", "REQ-3"}
        assert result["table_names"] == ["orders"]
        assert result["code_refs"] == ["main.py"]

    def test_empty_worker_outputs(self):
        assert extract_discovered_entities([]) == {}

    def test_none_discovered_entities(self):
        assert extract_discovered_entities([{"discovered_entities": None}]) == {}

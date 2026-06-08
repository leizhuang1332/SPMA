import pytest
from spma.agents.synthesis.fusion import synthesize_fusion, DEFAULT_WORKER_WEIGHTS


class TestSynthesizeFusion:
    def test_sql_weighted_higher_than_doc(self):
        doc_output = {"citations": [
            {"chunk_id": "d1", "snippet": "doc chunk 1", "source_type": "prd"},
            {"chunk_id": "d2", "snippet": "doc chunk 2", "source_type": "prd"},
        ]}
        sql_output = {"citations": [
            {"chunk_id": "s1", "snippet": "sql result", "source_type": "sql"},
        ]}
        result = synthesize_fusion([doc_output, sql_output])
        assert len(result) == 3

    def test_fusion_deduplicates_by_chunk_id(self):
        shared = {"chunk_id": "shared", "snippet": "shared", "source_type": "prd"}
        result = synthesize_fusion([{"citations": [shared]}, {"citations": [shared]}])
        assert len(result) == 1

    def test_empty_worker_outputs(self):
        assert synthesize_fusion([]) == []

    def test_single_worker_fallback(self):
        doc_output = {"citations": [{"chunk_id": "d1", "snippet": "only source", "source_type": "prd"}]}
        result = synthesize_fusion([doc_output])
        assert len(result) == 1
        assert result[0]["chunk_id"] == "d1"

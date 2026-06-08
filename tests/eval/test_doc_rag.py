"""Doc Agent RAG 质量评估——Recall@10, MRR。"""

import pytest


@pytest.mark.eval
class TestDocRAGQuality:
    ANNOTATED_QUERIES: list[tuple] = []

    async def test_recall_at_10(self, doc_agent):
        """Recall@10 >= 0.88。"""
        if not self.ANNOTATED_QUERIES:
            pytest.skip("No annotated queries configured")
        total_hits = 0
        total_expected = 0
        for query, entities, expected_ids in self.ANNOTATED_QUERIES:
            result = await doc_agent.search(query=query, entities=entities)
            returned_ids = {r["chunk_id"] for r in result["final_results"][:10]}
            hits = len(returned_ids & set(expected_ids))
            total_hits += hits
            total_expected += len(expected_ids)
        recall = total_hits / total_expected if total_expected > 0 else 0
        assert recall >= 0.88, f"Recall@10 = {recall:.3f} < 0.88"

    async def test_mrr(self, doc_agent):
        """MRR >= 0.80。"""
        if not self.ANNOTATED_QUERIES:
            pytest.skip("No annotated queries configured")
        reciprocal_ranks = []
        for query, entities, expected_ids in self.ANNOTATED_QUERIES:
            result = await doc_agent.search(query=query, entities=entities)
            ranks = [i + 1 for i, r in enumerate(result["final_results"][:10]) if r["chunk_id"] in expected_ids]
            rr = 1 / min(ranks) if ranks else 0
            reciprocal_ranks.append(rr)
        mrr = sum(reciprocal_ranks) / len(reciprocal_ranks)
        assert mrr >= 0.80, f"MRR = {mrr:.3f} < 0.80"

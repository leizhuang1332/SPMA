# tests/unit/ingestion/test_chunker.py
import pytest
from spma.ingestion.chunkers.semantic_chunker import SemanticChunker, estimate_tokens


class TestSemanticChunker:
    def test_chunk_by_headers(self):
        """按 ## 标题切分文档。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = """## 第一节
这是第一节的内容，描述了用户登录模块的基本流程。

## 第二节
这是第二节的内容，描述了支付模块的设计方案。包括订单状态流转和退款机制。"""

        chunks = chunker.split(text)
        assert len(chunks) >= 2
        assert any("第一节" in c.content for c in chunks)
        assert any("第二节" in c.content for c in chunks)

    def test_chunk_preserves_metadata(self):
        """chunk 保留 source_id 和 req_ids。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = "## 登录模块\n用户输入用户名和密码后，系统验证身份。"

        chunks = chunker.split(
            text,
            source_id="confluence:123",
            source_type="confluence",
            req_ids=["REQ-001", "REQ-002"],
        )

        for chunk in chunks:
            assert chunk.source_id == "confluence:123"
            assert chunk.source_type == "confluence"
            assert set(chunk.req_ids) == {"REQ-001", "REQ-002"}

    def test_chunk_within_token_limit(self):
        """每个 chunk 不超过 chunk_size_tokens。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        long_text = "用户登录流程描述。\n" * 200

        chunks = chunker.split(long_text)
        for chunk in chunks:
            assert estimate_tokens(chunk.content) <= 550

    def test_short_text_returns_single_chunk(self):
        """短文本不切片，返回单 chunk。"""
        chunker = SemanticChunker(chunk_size_tokens=500, overlap_tokens=50)
        text = "这是一个简短的 PRD 描述。"

        chunks = chunker.split(text)
        assert len(chunks) == 1
        assert chunks[0].content == text

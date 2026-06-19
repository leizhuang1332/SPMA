"""generator 模块测试——generate_draft_answer 和 _format_worker_stats。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from spma.agents.synthesis.generator import generate_draft_answer, _format_results, _format_worker_stats


class TestFormatResults:
    """测试 _format_results——检索结果格式化。"""

    def test_empty_citations_returns_no_result_message(self):
        result = _format_results([], "文档")
        assert result == "[来自文档] 无结果"

    def test_formats_citations_with_source_id(self):
        citations = [
            {"chunk_id": "d1", "source_id": "doc-123:0", "content": "需求内容片段"},
        ]
        result = _format_results(citations, "文档")
        assert "[来自文档]" in result
        assert "doc-123:0" in result
        assert "需求内容片段" in result

    def test_truncates_snippet_to_300_chars(self):
        citations = [
            {"chunk_id": "d1", "content": "X" * 500},
        ]
        result = _format_results(citations, "文档")
        snippet_in_result = [line for line in result.split("\n") if "X" in line][0]
        assert len(snippet_in_result) <= 310  # 300 + 序号前缀

    def test_uses_snippet_field_when_present(self):
        citations = [
            {"chunk_id": "d1", "snippet": "短摘要", "content": "长内容" * 50},
        ]
        result = _format_results(citations, "文档")
        assert "短摘要" in result
        assert "长内容" not in result  # snippet 优先

    def test_uses_chunk_id_as_fallback_source_id(self):
        citations = [
            {"chunk_id": "fallback-id", "content": "内容"},
        ]
        result = _format_results(citations, "文档")
        assert "fallback-id" in result


class TestFormatWorkerStats:
    """测试 _format_worker_stats——Worker 执行统计降级信息。"""

    def test_empty_worker_outputs(self):
        assert _format_worker_stats([]) == "无 Worker 执行记录"

    def test_formats_single_worker(self):
        stats = _format_worker_stats([
            {"worker_type": "doc", "result_count": 5, "confidence": 0.8},
        ])
        assert "doc" in stats
        assert "结果数=5" in stats
        assert "置信度=80%" in stats

    def test_formats_multiple_workers(self):
        stats = _format_worker_stats([
            {"worker_type": "doc", "result_count": 3, "confidence": 0.8},
            {"worker_type": "code", "result_count": 10, "confidence": 0.7},
        ])
        assert "doc" in stats
        assert "code" in stats
        assert stats.count("结果数=") == 2

    def test_handles_missing_confidence(self):
        stats = _format_worker_stats([
            {"worker_type": "doc", "result_count": 0, "confidence": 0},
        ])
        assert "doc" in stats
        assert "置信度=" not in stats  # confidence=0 时不显示

    def test_shows_exact_match_flag(self):
        stats = _format_worker_stats([
            {"worker_type": "doc", "result_count": 1, "confidence": 0.9, "has_exact_match": True},
        ])
        assert "精确匹配" in stats

    def test_shows_error_message(self):
        stats = _format_worker_stats([
            {"worker_type": "sql", "result_count": 0, "confidence": 0, "error": "sql_worker_not_implemented"},
        ])
        assert "sql_worker_not_implemented" in stats

    def test_none_worker_outputs_missing_fields(self):
        """缺失字段不崩溃。"""
        stats = _format_worker_stats([{"worker_type": "doc"}])
        assert "doc" in stats
        assert "结果数=0" in stats


class TestGenerateDraftAnswer:
    """测试 generate_draft_answer——端到端答案生成。"""

    @pytest.mark.asyncio
    async def test_generates_answer_with_all_source_types(self):
        """验证三个 source_type 的检索结果都正确传入 prompt。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "# 测试答案\n\n这是测试内容。"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        fused_citations = [
            {"chunk_id": "d1", "source_type": "prd", "content": "PRD内容A"},
            {"chunk_id": "c1", "source_type": "code", "content": "代码内容B", "file_path": "main.py"},
            {"chunk_id": "s1", "source_type": "sql", "content": "SQL结果C"},
        ]
        worker_outputs = [
            {"worker_type": "doc", "result_count": 1, "confidence": 0.8},
            {"worker_type": "code", "result_count": 1, "confidence": 0.7},
            {"worker_type": "sql", "result_count": 1, "confidence": 0.9},
        ]

        result = await generate_draft_answer(
            original_query="测试查询",
            fused_citations=fused_citations,
            worker_outputs=worker_outputs,
            llm=mock_llm,
        )

        assert result == "# 测试答案\n\n这是测试内容。"
        mock_llm.ainvoke.assert_called_once()
        prompt = mock_llm.ainvoke.call_args[0][0]
        # 验证三个检索结果都出现在 prompt 中
        assert "PRD内容A" in prompt
        assert "代码内容B" in prompt
        assert "SQL结果C" in prompt
        # 验证 prompt 包含源类型标签
        assert "[来自文档]" in prompt
        assert "[来自代码]" in prompt
        assert "[来自数据库]" in prompt
        # 验证 worker 统计信息存在
        assert "[Worker 执行统计]" in prompt
        assert "doc" in prompt
        assert "code" in prompt

    @pytest.mark.asyncio
    async def test_empty_citations_does_not_crash(self):
        """验证空检索结果不崩溃（降级兜底）。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "无法回答该问题。"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        result = await generate_draft_answer(
            original_query="不可能的查询",
            fused_citations=[],
            worker_outputs=[
                {"worker_type": "doc", "result_count": 0, "confidence": 0},
            ],
            llm=mock_llm,
        )

        assert result == "无法回答该问题。"
        prompt = mock_llm.ainvoke.call_args[0][0]
        assert "无结果" in prompt
        assert "[Worker 执行统计]" in prompt

    @pytest.mark.asyncio
    async def test_worker_outputs_used_for_stats(self):
        """验证 worker_outputs 参数真正被使用来生成统计信息。"""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "答案"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        await generate_draft_answer(
            original_query="测试",
            fused_citations=[{"chunk_id": "d1", "source_type": "prd", "content": "内容"}],
            worker_outputs=[
                {"worker_type": "doc", "result_count": 5, "confidence": 0.9, "has_exact_match": True},
                {"worker_type": "code", "result_count": 0, "confidence": 0, "error": "timeout"},
            ],
            llm=mock_llm,
        )

        prompt = mock_llm.ainvoke.call_args[0][0]
        # 验证 worker_outputs 信息确实出现在 prompt 中
        assert "结果数=5" in prompt
        assert "置信度=90%" in prompt
        assert "精确匹配" in prompt
        assert "timeout" in prompt

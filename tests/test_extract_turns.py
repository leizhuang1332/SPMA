"""extract_turns 模块单元测试。"""
import pytest
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage


class TestMergeTurns:
    """测试 _merge_turns 轮次合并逻辑。"""

    def test_single_turn(self):
        from spma.api.extract_turns import _merge_turns
        messages = [
            HumanMessage(content="订单表有哪些字段?"),
            AIMessage(content="订单表包含 id, status, amount 等字段。"),
        ]
        turns = _merge_turns(messages)
        assert len(turns) == 1
        assert turns[0]["query_text"] == "订单表有哪些字段?"
        assert "id, status, amount" in turns[0]["answer"]
        assert turns[0]["tool_calls"] == []

    def test_multi_turn(self):
        from spma.api.extract_turns import _merge_turns
        messages = [
            HumanMessage(content="第一问"),
            AIMessage(content="第一答"),
            HumanMessage(content="第二问"),
            AIMessage(content="第二答"),
        ]
        turns = _merge_turns(messages)
        assert len(turns) == 2
        assert turns[0]["query_text"] == "第一问"
        assert turns[0]["answer"] == "第一答"
        assert turns[1]["query_text"] == "第二问"
        assert turns[1]["answer"] == "第二答"

    def test_skips_tool_messages(self):
        from spma.api.extract_turns import _merge_turns
        messages = [
            HumanMessage(content="查订单"),
            AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "orders"}, "id": "1"}]),
            ToolMessage(content='{"rows": 42}', tool_call_id="1"),
            AIMessage(content="查到 42 条记录"),
        ]
        turns = _merge_turns(messages)
        assert len(turns) == 1
        assert turns[0]["query_text"] == "查订单"
        assert "42" in turns[0]["answer"]
        assert len(turns[0]["tool_calls"]) == 1
        assert turns[0]["tool_calls"][0]["name"] == "search"

    def test_merges_consecutive_ai_messages(self):
        from spma.api.extract_turns import _merge_turns
        messages = [
            HumanMessage(content="问题"),
            AIMessage(content="第一部分"),
            AIMessage(content="第二部分"),
        ]
        turns = _merge_turns(messages)
        assert len(turns) == 1
        assert turns[0]["answer"] == "第一部分第二部分"

    def test_empty_messages(self):
        from spma.api.extract_turns import _merge_turns
        turns = _merge_turns([])
        assert turns == []

    def test_unknown_message_type_skipped(self):
        from spma.api.extract_turns import _merge_turns
        from langchain_core.messages import SystemMessage
        messages = [
            HumanMessage(content="问题"),
            SystemMessage(content="系统提示"),
            AIMessage(content="答案"),
        ]
        turns = _merge_turns(messages)
        assert len(turns) == 1
        assert turns[0]["query_text"] == "问题"
        assert turns[0]["answer"] == "答案"


class TestFormatHistory:
    """测试 format_history 历史文本格式化。"""

    def test_format_history_basic(self):
        from spma.api.extract_turns import format_history
        messages = [HumanMessage(content="订单表字段?"), AIMessage(content="id, status, amount")]
        result = format_history(messages)
        assert "用户: 订单表字段?" in result
        assert "AI: id, status, amount" in result

    def test_format_history_empty(self):
        from spma.api.extract_turns import format_history
        result = format_history([])
        assert result == "无"


class TestSafeContent:
    """测试 _safe_content 内容提取。"""

    def test_string_content(self):
        from spma.api.extract_turns import _safe_content
        msg = HumanMessage(content="hello")
        assert _safe_content(msg) == "hello"

    def test_list_content(self):
        from spma.api.extract_turns import _safe_content
        msg = AIMessage(content=[{"text": "part1"}, {"text": "part2"}])
        assert _safe_content(msg) == "part1part2"

    def test_none_content(self):
        from spma.api.extract_turns import _safe_content
        # AIMessage(content=None) is rejected by Pydantic validation in LangChain >= 1.3.
        # Test that _safe_content handles an object without a content attribute gracefully.
        class FakeMsg:
            pass
        msg = FakeMsg()
        assert _safe_content(msg) == ""

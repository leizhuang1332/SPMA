"""extract_turns — 从 LangGraph checkpoint 中提取对话轮次并转换为前端可用结构。"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage

if TYPE_CHECKING:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

logger = logging.getLogger(__name__)
ALLOWED_MESSAGE_TYPES = {"HumanMessage", "AIMessage", "AIMessageChunk", "ToolMessage"}


async def extract_turns(
    session_id: str,
    checkpointer: AsyncPostgresSaver,
    limit: int = 20,
    offset: int = 0,
) -> dict | None:
    """从 LangGraph checkpoint 提取分页对话轮次。

    优先通过 aget_tuple 查询最新 checkpoint，失败时遍历所有 checkpoint 降级。
    返回 {"turns": [...], "total": int, "offset": int, "limit": int} 或 None。
    """
    config = {"configurable": {"thread_id": session_id}}
    messages = await _get_messages_primary(checkpointer, config)
    if messages is None:
        messages = await _get_messages_fallback(checkpointer, config)
    if messages is None:
        return None
    turns = _merge_turns(messages)
    total = len(turns)
    return {
        "turns": turns[offset:offset + limit],
        "total": total,
        "offset": offset,
        "limit": limit,
    }


async def _get_messages_primary(checkpointer, config) -> list | None:
    """优先路径：aget_tuple 直查最新 checkpoint。"""
    try:
        checkpoint_tuple = await checkpointer.aget_tuple(config)
        if checkpoint_tuple is None:
            return None
        channel_values = checkpoint_tuple.checkpoint.get("channel_values", {})
        return list(channel_values.get("messages", []))
    except Exception as e:
        logger.warning("aget_tuple 查询 checkpoint 失败: %s", e)
        return None


async def _get_messages_fallback(checkpointer, config) -> list | None:
    """降级路径：遍历所有 checkpoint，取 messages 最长的。"""
    try:
        best = None
        max_len = 0
        async for cp in checkpointer.alist(config, limit=100):
            channel_values = cp.checkpoint.get("channel_values", {})
            msgs = channel_values.get("messages", [])
            if len(msgs) > max_len:
                max_len = len(msgs)
                best = msgs
        return list(best) if best else None
    except Exception as e:
        logger.error("遍历 checkpoint 失败: %s", e)
        return None


def _merge_turns(messages: list) -> list[dict]:
    """累积模式合并消息为对话轮次。

    HumanMessage → flush 上一轮并新建 turn。
    AIMessage → 累积 content 和 tool_calls 到当前 turn。
    ToolMessage → 跳过。
    多个连续 AIMessage 合并为一条 assistant 响应。
    """
    turns: list[dict] = []
    current_turn: dict | None = None

    for msg in messages:
        msg_type = type(msg).__name__

        if msg_type not in ALLOWED_MESSAGE_TYPES:
            logger.debug("跳过未知消息类型: %s", msg_type)
            continue

        if isinstance(msg, HumanMessage):
            if current_turn is not None:
                turns.append(current_turn)
            current_turn = {
                "query_text": _safe_content(msg),
                "answer": "",
                "tool_calls": [],
            }

        elif isinstance(msg, AIMessage):
            if current_turn is None:
                current_turn = {"query_text": "", "answer": "", "tool_calls": []}
            content = _safe_content(msg)
            if content:
                current_turn["answer"] += content
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    if isinstance(tc, dict):
                        current_turn["tool_calls"].append({
                            "id": tc.get("id", ""),
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                        })
                    else:
                        current_turn["tool_calls"].append({
                            "id": getattr(tc, "id", ""),
                            "name": getattr(tc, "name", ""),
                            "args": getattr(tc, "args", {}),
                        })

    if current_turn is not None and (current_turn["query_text"] or current_turn["answer"]):
        turns.append(current_turn)

    return turns


def _safe_content(msg) -> str:
    """安全提取消息 content，兼容 str 和 list 类型。"""
    content = getattr(msg, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(part.get("text", ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    return str(content) if content else ""


def format_history(messages: list, max_turns: int = 10) -> str:
    """从 messages 列表构建分类器 Prompt 用的历史文本。

    取最近 max_turns 条消息，格式化为 "用户: ...\\nAI: ..." 文本。
    """
    lines = []
    for msg in messages[-max_turns * 2:]:
        if isinstance(msg, HumanMessage):
            lines.append(f"用户: {_safe_content(msg)}")
        elif isinstance(msg, AIMessage):
            lines.append(f"AI: {_safe_content(msg)[:200]}")
    return "\n".join(lines) if lines else "无"

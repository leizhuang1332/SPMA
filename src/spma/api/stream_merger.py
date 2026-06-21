# src/spma/api/stream_merger.py
"""双通道 SSE 流合并器——graph.astream() + Redis Pub/Sub → 统一 SSE 输出。

核心设计：
- 两个 asyncio.Task 并行消费两个通道，输出到 asyncio.Queue
- 30s 无事件时自动发 keepalive 心跳
- _SENTINEL 标记通道结束
- progress 通道提前结束时不影响 data 通道继续工作
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as time_module
from collections.abc import AsyncGenerator
from typing import Any

from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)

# 通道结束标记
_SENTINEL = object()


class StreamMerger:
    """双通道 SSE 流合并器。

    用法:
        merger = StreamMerger(
            graph=compiled_graph,
            input_state={"messages": [...], "original_query": "..."},
            config={"configurable": {"thread_id": "session-1"}},
            redis_client=redis,
            query_id="query-123",
        )
        async for sse_event in merger.run():
            yield sse_event  # {"event": "worker_start", "data": "..."}
    """

    def __init__(
        self,
        graph,
        input_state: dict,
        config: dict,
        redis_client: Any | None,
        query_id: str,
    ):
        self.graph = graph
        self.input_state = input_state
        self.config = config
        self.redis = redis_client
        self.query_id = query_id
        self.queue: asyncio.Queue = asyncio.Queue()
        self._all_worker_outputs: list[dict] = []
        self._start_time = time_module.time()

    async def run(self) -> AsyncGenerator[dict, None]:
        """启动双通道，yield 统一的 SSE event dict。"""
        graph_task = asyncio.create_task(self._consume_graph_stream())
        progress_task = asyncio.create_task(self._consume_progress_stream())

        tasks_done = 0
        try:
            while tasks_done < 2:
                try:
                    event = await asyncio.wait_for(self.queue.get(), timeout=30.0)
                    if event is _SENTINEL:
                        tasks_done += 1
                        # Graph 通道完成时，向 Redis channel 发布 shutdown 消息，
                        # 让 progress task 的 pubsub.listen() 立即收到消息并退出，
                        # 避免阻塞等待 60-120s 直到 Redis socket timeout。
                        if tasks_done == 1 and self.redis is not None:
                            try:
                                await self.redis.publish(
                                    f"spma:progress:{self.query_id}",
                                    json.dumps({"event_type": "_shutdown"}),
                                )
                            except Exception:
                                pass
                    else:
                        yield event
                except asyncio.TimeoutError:
                    yield {
                        "event": "keepalive",
                        "data": "{}",
                    }
        finally:
            # 确保 task 被取消
            for t in (graph_task, progress_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except asyncio.CancelledError:
                        pass

        # 两通道都结束后 yield done
        yield self._build_done_event()

    async def _consume_graph_stream(self) -> None:
        """消费 graph.astream() → 写入队列。"""
        try:
            async for mode, data in self.graph.astream(
                self.input_state,
                self.config,
                stream_mode=["messages", "updates"],
            ):
                if mode == "updates":
                    for node_name, payload in data.items():
                        if node_name in ("doc_worker", "code_worker", "sql_worker"):
                            wos = payload.get("worker_outputs", [])
                            if isinstance(wos, list):
                                self._all_worker_outputs.extend(wos)

                        event = self._map_node_to_sse(node_name, payload)
                        if event:
                            await self.queue.put(event)

                elif mode == "messages":
                    msg, metadata = data
                    node_name = metadata.get("langgraph_node", "")
                    is_synthesis = (
                        node_name == "synthesis"
                        or node_name.startswith("synthesis:")
                        or node_name == "generate"
                    )
                    if is_synthesis and isinstance(msg, AIMessage) and msg.content:
                        content = msg.content
                        if isinstance(content, str) and content:
                            await self.queue.put({
                                "event": "synthesis",
                                "data": json.dumps({"chunk": content, "citations": [], "elapsed_ms": 0}, ensure_ascii=False),
                            })
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Graph stream 异常")
            await self.queue.put({
                "event": "error",
                "data": json.dumps({"code": "INTERNAL", "message": str(e), "retryable": False}, ensure_ascii=False),
            })
        finally:
            await self.queue.put(_SENTINEL)

    async def _consume_progress_stream(self) -> None:
        """订阅 Redis Pub/Sub → 解析 ProgressEvent → 写入队列。"""
        if self.redis is None:
            await self.queue.put(_SENTINEL)
            return

        pubsub = self.redis.pubsub()
        channel = f"spma:progress:{self.query_id}"

        try:
            await pubsub.subscribe(channel)
            async for msg in pubsub.listen():
                if msg["type"] != "message":
                    continue
                try:
                    data = json.loads(msg["data"])
                except (json.JSONDecodeError, KeyError):
                    continue

                event_type = data.get("event_type", "")
                if event_type == "_shutdown":
                    break  # graph 已完成，退出 listen 循环 → finally 发送 SENTINEL
                if event_type == "worker_start":
                    await self.queue.put({
                        "event": "worker_start",
                        "data": json.dumps({
                            "worker": self._normalize_node_name(data.get("node", "")),
                            "timestamp": data.get("timestamp", ""),
                        }, ensure_ascii=False),
                    })
                elif event_type == "worker_progress":
                    await self.queue.put({
                        "event": "worker_progress",
                        "data": json.dumps({
                            "worker": self._normalize_node_name(data.get("node", "")),
                            "step": data.get("step", ""),
                            "message": data.get("message", ""),
                            "stats": data.get("stats"),
                            "elapsed_ms": 0,
                        }, ensure_ascii=False),
                    })
                elif event_type == "thinking":
                    await self.queue.put({
                        "event": "thinking",
                        "data": json.dumps({
                            "node": data.get("node", ""),
                            "chunk": data.get("thinking_chunk", ""),
                            "timestamp": data.get("timestamp", ""),
                        }, ensure_ascii=False),
                    })
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Progress stream 异常，进度通道关闭", exc_info=True)
        finally:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                pass
            await self.queue.put(_SENTINEL)

    def _map_node_to_sse(self, node_name: str, payload: dict) -> dict | None:
        """将 graph node 输出映射为 SSE event dict。"""
        if node_name == "classify":
            classification = payload.get("classification", {})
            if hasattr(classification, "sources"):
                sources = [s.value if hasattr(s, "value") else s for s in classification.sources]
            else:
                sources = classification.get("sources", [])
            return {
                "event": "classification",
                "data": json.dumps({
                    "sources": sources,
                    "is_cross_source": len(sources) > 1,
                    "entities": payload.get("entities", {}),
                    "completeness": classification.get("completeness", "complete") if isinstance(classification, dict) else "complete",
                    "elapsed_ms": 0,
                }, ensure_ascii=False, default=str),
            }

        if node_name in ("doc_worker", "code_worker", "sql_worker"):
            outputs = payload.get("worker_outputs", [])
            if isinstance(outputs, list) and outputs:
                wo = outputs[0]
                return {
                    "event": "worker_result",
                    "data": json.dumps({
                        "worker": node_name.replace("_worker", ""),
                        "result_count": wo.get("result_count", 0),
                        "retrieval_method": wo.get("retrieval_method", ""),
                        "elapsed_ms": wo.get("elapsed_ms", 0),
                    }, ensure_ascii=False),
                }

        return None

    def _normalize_node_name(self, node: str) -> str:
        """将后端 node name (doc_worker) 转为前端 worker name (doc)。"""
        return node.replace("_worker", "")

    def _build_done_event(self) -> dict:
        total_latency = int((time_module.time() - self._start_time) * 1000)
        sources = self._extract_sources()
        return {
            "event": "done",
            "data": json.dumps({
                "query_id": self.query_id,
                "latency_ms": total_latency,
                "degradation": None,
                "suggested_followups": [],
                "sources": sources,
            }, ensure_ascii=False, default=str),
        }

    def _extract_sources(self) -> list[dict]:
        """从累积的 worker outputs 中提取 sources。"""
        try:
            from spma.api.routes.query import _extract_sources_from_worker_outputs
            return _extract_sources_from_worker_outputs(self._all_worker_outputs)
        except ImportError:
            return []

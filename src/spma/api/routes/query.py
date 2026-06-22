"""查询端点——POST /api/v1/query + /api/v1/sql/query。

设计依据: API-01 §2 核心端点
"""

import asyncio
import json
import logging
import time as time_module
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter()


class QueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    conversation_history: str | None = None
    sources_hint: list[str] | None = None  # ["doc", "code", "sql"]


class SqlQueryRequest(BaseModel):
    query: str
    session_id: str | None = None
    auto_confirm: bool = False


class QueryResponse(BaseModel):
    query_id: str
    status: str
    answer: str | None = None
    annotations: list[dict] | None = None
    worker_results: list[dict] | None = None
    quality_report: dict | None = None
    token_usage: dict | None = None
    latency_ms: int | None = None


@router.post("/api/v1/query")
async def general_query(req: QueryRequest):
    """通用查询端点——完整 Agent 编排 (Supervisor -> Workers -> Synthesis)。

    Slice 1 演示: 直接分类 + 合成，后续 Slice 接入完整 LangGraph 图。
    """
    query_id = str(uuid.uuid4())
    start_time = time.time()
    session_id = req.session_id or f"session_{query_id[:8]}"

    # ---- 0. 会话自动管理 ----
    if req.session_id:
        try:
            from spma.api.dependencies import get_session_store
            store = get_session_store()
            if not await store.session_exists(req.session_id):
                title = req.query[:10] if req.query else None
                await store.create_session(title=title, session_id=req.session_id)
            # 首轮查询自动设置标题（取前 10 字符）
            session = await store.get_session(req.session_id)
            if session and not session.get("title") and req.query:
                await store.update_session_title(req.session_id, req.query[:10])
        except RuntimeError:
            pass  # db_pool 未初始化时静默降级

    # ---- 1. 分类 + 实体抽取 ----
    from spma.agents.supervisor.classifier_fallback import classify_with_fallback
    from spma.agents.supervisor.entity_extractor import extract_entities
    from spma.llm import get_langchain_client

    llm = get_langchain_client(role="generation")
    classification = await classify_with_fallback(
        query=req.query,
        primary_llm=llm,
        conversation_history=req.conversation_history or "",
    )
    entities = await extract_entities(req.query, classification)

    # ---- 2. Token 预算管理 ----
    from spma.llm.token_budget import TokenBudgetManager

    sources = req.sources_hint or classification.get("sources", ["doc"])
    budget_mgr = TokenBudgetManager(
        query_type=classification.get("query_type", "search"),
        num_sources=len(sources),
    )

    # ---- 3. 查询改写 ----
    from spma.agents.supervisor.query_rewriter import rewrite_queries

    rewritten = await rewrite_queries(
        query=req.query,
        classification=classification,
        entities=entities,
        llm=llm,
        synonym_map=None,  # API 层面暂不从数据库获取
        conversation_history=req.conversation_history or "",
    )

    # ---- 4. 派发 Worker (并行) ----
    import asyncio

    from spma.agents.supervisor.dispatcher import build_dispatches

    # 初始化检索基础设施（es_client/vector_store/embedder 由各 worker 共享）
    es_client = None
    vector_store = None
    embedder = None
    try:
        from spma.retrieval.es_client import ESClient
        es_client = ESClient()
    except Exception as e:
        logger.warning("ES 客户端初始化失败: %s", e)

    try:
        from spma.retrieval.vector_store import PGVectorStore
        vector_store = PGVectorStore()
    except Exception as e:
        logger.warning("PGVector 客户端初始化失败: %s", e)

    try:
        from spma.retrieval.embedder import BGEM3Embedder
        embedder = await BGEM3Embedder.create()
    except Exception as e:
        logger.warning("Embedder 初始化失败: %s", e)

    dispatches = build_dispatches(
        classification=classification,
        entities=entities,
        rewritten_queries=rewritten,
        query_id=query_id,
    )

    worker_outputs: list[dict] = []
    worker_tasks = []

    for dispatch in dispatches:
        print("="*50)
        print(f"dispatch: {dispatch}")
        print("="*50)
        # LangGraph Send 对象: .node = worker 节点名, .arg = WorkerDispatch dict
        dispatch_arg = dispatch.arg if hasattr(dispatch, 'arg') else dispatch
        agent_type = dispatch_arg.get("agent_type", "doc")

        if not budget_mgr.track_call("workers"):
            worker_outputs.append({
                "worker_type": agent_type,
                "result_count": 0,
                "confidence": 0,
                "has_exact_match": False,
                "error": "token_budget_exhausted",
            })
            continue

        async def _run_worker(d=dispatch_arg, at=agent_type) -> dict:
            rewritten_query = d.get("rewritten_query", d.get("original_query", req.query))
            try:
                if at == "doc":
                    from spma.agents.doc.graph import build_doc_agent_graph
                    g = build_doc_agent_graph(
                        es_client=es_client,
                        vector_store=vector_store,
                        embedder=embedder,
                        llm=llm,
                    )
                    result = await g.ainvoke({
                        "original_query": req.query,
                        "rewritten_queries": [rewritten_query],
                        "retriever": None,
                        "query_id": query_id,
                        "entities": entities,
                    })
                    from spma.agents.supervisor.dispatcher import normalize_citations
                    citations = result.get("final_results", [])
                    return {
                        "worker_type": at,
                        "result_count": len(citations),
                        "citations": normalize_citations(at, citations),
                        "confidence": result.get("confidence", 0.8),
                        "has_exact_match": result.get("has_exact_match", False),
                        "rounds_used": result.get("rounds_used", 1),
                    }
                elif at == "code":
                    from spma.api.dependencies import (
                        get_file_path_cache,
                        get_ripgrep_executor,
                        get_ast_parser,
                    )
                    from spma.agents.code.graph import build_code_agent_graph

                    try:
                        file_path_cache = get_file_path_cache()
                        ripgrep_executor = get_ripgrep_executor()
                        ast_parser = get_ast_parser()
                    except RuntimeError as e:
                        logger.warning("Code Agent 依赖未初始化，跳过 code worker: %s", e)
                        return {
                            "worker_type": at,
                            "result_count": 0,
                            "citations": [],
                            "confidence": 0,
                            "has_exact_match": False,
                            "rounds_used": 0,
                            "error": f"worker_not_ready:{str(e)[:100]}",
                        }

                    g = build_code_agent_graph(
                        file_path_cache=file_path_cache,
                        ripgrep_executor=ripgrep_executor,
                        ast_parser=ast_parser,
                        llm=llm,
                    )
                    result = await g.ainvoke({
                        "original_query": req.query,
                        "rewritten_queries": [rewritten_query],
                        "query_id": query_id,
                    })
                    from spma.agents.supervisor.dispatcher import normalize_citations
                    citations = result.get("ripgrep_results", [])
                    return {
                        "worker_type": at,
                        "result_count": len(citations),
                        "citations": normalize_citations(at, citations),
                        "confidence": 0.7,
                        "has_exact_match": result.get("fallback_layer", 99) == 0,
                        "rounds_used": result.get("rounds_used", 1),
                    }
            except Exception as e:
                logger.warning(f"Worker '{at}' 执行失败: {e}")
                return {
                    "worker_type": at,
                    "result_count": 0,
                    "citations": [],
                    "confidence": 0,
                    "has_exact_match": False,
                    "rounds_used": 0,
                    "error": f"worker_failed:{str(e)[:100]}",
                }

            if at == "sql":
                return {
                    "worker_type": at,
                    "result_count": 0,
                    "citations": [],
                    "confidence": 0,
                    "has_exact_match": False,
                    "error": "sql_worker_not_implemented",
                }
            return {
                "worker_type": at,
                "result_count": 0,
                "citations": [],
                "confidence": 0,
                "error": f"unknown_worker_type:{at}",
            }

        worker_tasks.append(_run_worker())

    if worker_tasks:
        results = await asyncio.gather(*worker_tasks, return_exceptions=True)
        for r in results:
            print("="*50)
            print(f"worker_output: {r}")
            print("="*50)
            if isinstance(r, Exception):
                worker_outputs.append({"error": str(r), "result_count": 0})
            else:
                worker_outputs.append(r)

    # ---- 5. 质量评估 ----
    from spma.agents.supervisor.quality import evaluate_workers

    quality_result = evaluate_workers(
        worker_outputs,
        classification.get("query_type", "search"),
        threshold=0.6,
    )
    # ---- 6. 合成 (Synthesis Agent) ----
    try:
        from spma.agents.synthesis.graph import build_synthesis_agent_graph

        synthesis_graph = build_synthesis_agent_graph(llm=llm, audit_llm=llm)
        synthesis_result = await synthesis_graph.ainvoke({
            "original_query": req.query,
            "worker_outputs": worker_outputs,
            "max_rounds": 2,
            "round": 0,
        })
        print("="*50)
        print(f"synthesis_result: {synthesis_result}")
        print("="*50)
    except Exception as e:
        logger.warning(f"Synthesis agent 失败: {e}")
        synthesis_result = {
            "final_answer": f"[Slice 1 预览] 针对查询 '{req.query}' 已分类为 {classification.get('sources', [])} 源，"
                            f"Worker 输出 {len(worker_outputs)} 条结果。完整合成等待后续 Slice 实现。",
            "annotations": [],
            "convergence_reason": "synthesis_not_implemented",
        }

    total_latency = int((time.time() - start_time) * 1000)
    print(f"total_latency: {total_latency}")
    # ---- 7. Trace 日志 ----
    try:
        from spma.observability.trace_logger import AgentTraceLogger

        try:
            from spma.api.dependencies import get_db_pool
            db_pool = get_db_pool()
            trace_logger = AgentTraceLogger(db_pool=db_pool)
        except RuntimeError:
            trace_logger = AgentTraceLogger()
        combined_state = {
            "session_id": session_id,
            "original_query": req.query,
            "classification": classification,
            "entities": entities,
            "worker_outputs": worker_outputs,
            "quality_scores": quality_result.get("scores", {}),
            "reschedule_count": 0,
            "total_llm_calls": sum(w.get("rounds_used", 0) for w in worker_outputs),
            "total_tokens": 0,
            "convergence_reason": synthesis_result.get("convergence_reason", ""),
            "answer": synthesis_result.get("final_answer", ""),
            "latency_ms": total_latency,
        }
        await trace_logger.log_query(query_id, combined_state)
        await trace_logger.log_round(query_id, "supervisor", 1, {
            "action": "classify+dispatch+synthesis",
            "results": worker_outputs,
            "assessment": quality_result.get("summary", ""),
            "confidence": min((s or 0) for s in quality_result.get("scores", {}).values()) if quality_result.get("scores") else 0,
            "latency_ms": total_latency,
            "llm_calls": sum(w.get("rounds_used", 0) for w in worker_outputs),
        })
    except Exception as e:
        logger.warning(f"Trace 日志记录失败: {e}")

    # ---- 7b. 内存模式：追加 turn 到 session store ----
    try:
        from spma.api.dependencies import get_session_store
        store = get_session_store()
        if not store._use_db:
            turn = {
                "query_id": query_id,
                "session_id": session_id,
                "query_text": req.query,
                "answer": synthesis_result.get("final_answer", ""),
                "sources": [
                    c for w in worker_outputs
                    for c in (w.get("citations", []) if isinstance(w, dict) else [])
                    if isinstance(c, dict)
                ],
                "classification": classification,
                "degradation": None,
                "sql_executed": None,
                "latency_ms": total_latency,
                "user_feedback": "none",
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            store.add_turn_memory(session_id, turn)
    except Exception as e:
        logger.warning(f"内存 SessionStore 写入失败: {e}")

    return QueryResponse(
        query_id=query_id,
        status="completed",
        answer=synthesis_result.get("final_answer", ""),
        annotations=synthesis_result.get("annotations", []),
        worker_results=worker_outputs,
        quality_report={
            "scores": quality_result.get("scores", {}),
            "issues": quality_result.get("issues", []),
            "confidence": quality_result.get("confidence", 0),
        },
        token_usage={
            "budget": budget_mgr.budget,
            "used": budget_mgr.used,
            "remaining": {k: budget_mgr.remaining(k) for k in budget_mgr.budget},
        },
        latency_ms=total_latency,
    )


@router.post("/api/v1/sql/query")
async def sql_query(req: SqlQueryRequest):
    """SQL Agent 查询端点——自然语言 → SQL 执行。

    Slice 1 Mock: 使用硬编码 Schema + SQLite 内存库。
    """
    from spma.agents.sql.state import SQLAgentState
    from spma.agents.sql.graph import (
        set_schema_snapshot,
        generate_node,
        guard_node,
        execute_node,
        verify_node,
    )
    from spma.agents.sql.convergence import check_convergence

    # Mock Schema 快照
    set_schema_snapshot({
        "orders": {"id", "status", "amount", "user_id", "created_at"},
        "users": {"id", "name", "email", "created_at"},
        "products": {"id", "name", "price", "category"},
    })

    # 初始化 Mock 执行器
    from spma.agents.sql.executor import MockExecutor
    schema_sql = """
        CREATE TABLE orders (id INTEGER, status TEXT, amount REAL, user_id INTEGER, created_at TEXT);
        CREATE TABLE users (id INTEGER, name TEXT, email TEXT, created_at TEXT);
        CREATE TABLE products (id INTEGER, name TEXT, price REAL, category TEXT);
    """
    sample_data = {
        "orders": [
            (1, "paid", 100.0, 1, "2026-06-01"),
            (2, "pending", 50.0, 2, "2026-06-02"),
            (3, "paid", 200.0, 1, "2026-06-03"),
            (4, "cancelled", 30.0, 2, "2026-06-04"),
        ],
        "users": [
            (1, "Alice", "alice@example.com", "2026-01-01"),
            (2, "Bob", "bob@example.com", "2026-02-01"),
        ],
        "products": [
            (1, "Widget", 50.0, "widgets"),
            (2, "Gadget", 100.0, "gadgets"),
        ],
    }
    executor = MockExecutor(schema_sql, sample_data)

    # 构建初始状态
    import time
    state = SQLAgentState(
        query=req.query,
        original_query=req.query,
        current_round=0,
        max_rounds=5,
        timeout_ms=3000,
        sql_history=[],
        start_time=time.time(),
        _executor=executor,
    )

    # 运行 Agent 循环
    for _ in range(state["max_rounds"]):
        # generate
        await generate_node(state)

        # guard
        guard_node(state)
        guard_result = state.get("guard_result")
        if guard_result and not guard_result.get("passed", True):
            return {
                "status": "blocked",
                "guard_result": {
                    "passed": False,
                    "forbidden_operations": guard_result.get("forbidden_operations", []),
                    "syntax_errors": guard_result.get("syntax_errors", []),
                    "table_existence_errors": guard_result.get("table_existence_errors", []),
                    "risk_level": guard_result.get("risk_level", "blocked"),
                },
            }

        # execute
        await execute_node(state)

        # verify
        verify_node(state)

        # 检查是否收敛
        converged, reason = check_convergence(state)
        if converged:
            break

    execution_result = state.get("execution_result", {})

    return {
        "status": "completed",
        "sql": state.get("generated_sql", ""),
        "result": {
            "columns": execution_result.get("columns", []),
            "rows": execution_result.get("rows", []),
            "row_count": execution_result.get("row_count", 0),
            "execution_time_ms": execution_result.get("execution_time_ms", 0),
            "replica_lag_ms": execution_result.get("replica_lag_ms", 0),
            "data_snapshot_at": execution_result.get("data_snapshot_at", ""),
        },
        "rounds": state.get("current_round", 1),
        "quality_report": {
            "issues": [],
            "confidence": 1.0,
        },
    }


class ConfirmRequest(BaseModel):
    confirmation_token: str
    action: str  # "execute" | "modify"
    modified_query: str | None = None


@router.post("/api/v1/sql/query/confirm")
async def sql_query_confirm(req: ConfirmRequest):
    """确认闸门端点——用户确认后继续执行。"""
    from spma.infrastructure.state_store import confirmation_store

    entry = await confirmation_store.load(req.confirmation_token)
    if entry is None:
        return {
            "status": "error",
            "error": "confirmation_token_expired",
            "message": "确认令牌已过期（有效期3分钟），请重新提交查询",
            "original_query": None,
        }

    if req.action == "modify":
        original_query = entry["original_query"]
        await confirmation_store.delete(req.confirmation_token)
        return {
            "status": "completed",
            "message": f"查询已修改，请用新查询重新发起请求: {req.modified_query or original_query}",
        }

    # action == "execute": 继续执行（Slice 3 实现完整流程）
    saved_state = entry["state"]
    confirmation_store.delete(req.confirmation_token)

    return {
        "status": "completed",
        "message": "确认后执行完成（Slice 3 完整实现待接入 Agent 循环）",
    }


class QueryStreamRequest(BaseModel):
    query: str
    session_id: str
    sources_hint: list[str] | None = None


@router.post("/api/v1/query/stream")
async def query_stream(req: QueryStreamRequest, request: Request):
    """SSE 流式查询端点——StreamMerger 双通道（graph + progress）→ 统一 SSE 输出。

    conversation_history 由后端从 checkpoint 的 messages 中自动获取。
    """
    from spma.api.dependencies import get_session_store, get_query_graph
    from spma.api.stream_merger import StreamMerger
    from spma.api.progress import ProgressPublisher

    # Route-level: ensure session exists (lazy-create on first query)
    store = get_session_store()
    if not await store.session_exists(req.session_id):
        title = req.query[:10] if req.query else None
        await store.create_session(title=title, session_id=req.session_id)

    query_id = str(uuid.uuid4())

    # Auto-set session title on first query
    try:
        session = await store.get_session(req.session_id)
        if session and not session.get("title") and req.query:
            await store.update_session_title(req.session_id, req.query[:10])
    except Exception:
        pass

    graph = get_query_graph()
    config = {"configurable": {"thread_id": req.session_id}}

    # Get Redis client if available
    redis_client = None
    try:
        from spma.api.dependencies import get_redis_client
        redis_client = get_redis_client()
    except RuntimeError:
        pass

    progress = ProgressPublisher(redis_client, query_id)

    # Set in asyncio task context so LangGraph nodes can access it
    # without it being serialized into checkpoints (ProgressPublisher
    # contains a Redis client and is not msgpack-serializable).
    from spma.api.progress import set_current_progress
    set_current_progress(progress)

    input_state = {
        "messages": [HumanMessage(content=req.query)],
        "original_query": req.query,
        "session_id": req.session_id,
        "sources_hint": req.sources_hint,
    }

    merger = StreamMerger(
        graph=graph,
        input_state=input_state,
        config=config,
        redis_client=redis_client,
        query_id=query_id,
    )

    async def event_gen() -> AsyncGenerator[dict, None]:
        try:
            async for sse_event in merger.run():
                yield sse_event
        except asyncio.CancelledError:
            yield {
                "event": "error",
                "data": json.dumps({"code": "CANCELLED", "message": "客户端取消请求"}, ensure_ascii=False),
            }
        except Exception as e:
            logger.exception("Query stream error for session %s", req.session_id)
            yield {
                "event": "error",
                "data": json.dumps({"code": "INTERNAL", "message": str(e)}, ensure_ascii=False),
            }

    return EventSourceResponse(event_gen())


# ---- citation → Source 转换 ----
# 后端 worker_type 与前端 source_type 的映射
_WORKER_TYPE_TO_FRONTEND_SOURCE: dict[str, str] = {"doc": "doc", "code": "code", "sql": "sql"}
# Citation.source_type 是合约层标记（"prd" → 前端 "doc"）
_CITATION_SOURCE_TYPE_MAP: dict[str, str] = {"prd": "doc", "code": "code", "sql": "sql"}


def _citation_to_source(citation: dict, worker_type: str) -> dict:
    """将后端 Citation 转换为前端 Source 格式。

    Citation 字段 (来自 normalize_citations):
      - source_type: "prd" | "code" | "sql"
      - source_id: str
      - snippet: str
      - relevance_score: float (可选)
      - metadata: dict (可选)
      - worker_rank: int (由 fusion 层注入)
      - rrf_score: float (由 fusion 层注入)

    Source 字段 (前端期望):
      - source_type: "doc" | "code" | "sql"
      - content: str
      - metadata: {title, file_path, table_name, ...}
      - relevance_score: float
      - retrieval_method: "exact" | "grep" | "semantic" | "hybrid" | "cache"
    """
    raw_source_type = citation.get("source_type", worker_type)
    frontend_source_type = _CITATION_SOURCE_TYPE_MAP.get(raw_source_type, "doc")

    # 构建前端 metadata
    cite_meta = citation.get("metadata", {}) or {}
    source_metadata: dict = {}

    if frontend_source_type == "doc":
        source_metadata["title"] = cite_meta.get("title") or citation.get("source_id", "")
        source_metadata["source_url"] = cite_meta.get("source_url", "")
        source_metadata["doc_type"] = cite_meta.get("doc_type", "")
        source_metadata["version"] = cite_meta.get("version", "")
        source_metadata["updated_at"] = cite_meta.get("updated_at", "")
        retrieval_method = citation.get("retrieval_method", "hybrid")
    elif frontend_source_type == "code":
        source_metadata["file_path"] = cite_meta.get("file_path") or citation.get("source_id", "")
        source_metadata["line_start"] = cite_meta.get("line_start")
        source_metadata["line_end"] = cite_meta.get("line_end")
        source_metadata["function_name"] = cite_meta.get("function_name", "")
        source_metadata["class_name"] = cite_meta.get("class_name", "")
        source_metadata["language"] = cite_meta.get("language", "")
        source_metadata["repo"] = cite_meta.get("repo", "")
        source_metadata["commit_hash"] = cite_meta.get("commit_hash", "")
        source_metadata["author"] = cite_meta.get("author", "")
        retrieval_method = citation.get("retrieval_method", "grep")
    elif frontend_source_type == "sql":
        source_metadata["table_name"] = cite_meta.get("table_name") or citation.get("source_id", "")
        source_metadata["column_name"] = cite_meta.get("column_name", "")
        source_metadata["data_type"] = cite_meta.get("data_type", "")
        retrieval_method = citation.get("retrieval_method", "cache")
    else:
        retrieval_method = "hybrid"

    # 合并 cite_meta 中未被显式处理的字段（仅排除已在 source_metadata 中设置过的 key）
    for k, v in cite_meta.items():
        if k not in source_metadata:
            source_metadata[k] = v

    # 计算 relevance_score：优先使用 rrf_score（融合后），其次 relevance_score
    relevance = citation.get("rrf_score") or citation.get("relevance_score", 0.5)
    if isinstance(relevance, (int, float)):
        relevance = min(max(float(relevance), 0.0), 1.0)

    return {
        "source_type": frontend_source_type,
        "content": citation.get("snippet", ""),
        "metadata": source_metadata,
        "relevance_score": relevance,
        "retrieval_method": retrieval_method,
    }


def _extract_sources_from_worker_outputs(worker_outputs: list[dict]) -> list[dict]:
    """从 worker outputs 中提取所有 citations 并转换为前端 Source 格式。"""
    sources: list[dict] = []
    seen_ids: set[str] = set()

    for wo in worker_outputs:
        worker_type = wo.get("worker_type", "doc")
        citations = wo.get("citations", [])
        if not isinstance(citations, list):
            continue
        for c in citations:
            if not isinstance(c, dict):
                continue
            source = _citation_to_source(c, worker_type)
            source_id = source.get("metadata", {}).get("title") or source.get("metadata", {}).get("file_path") or source.get("content", "")[:80]
            dedup_key = f"{source['source_type']}:{source_id}"
            if dedup_key not in seen_ids:
                seen_ids.add(dedup_key)
                sources.append(source)

    # 按 relevance_score 降序排列
    sources.sort(key=lambda s: s.get("relevance_score", 0), reverse=True)
    return sources


def _map_node_to_event(node_name: str, payload: dict, query_id: str) -> dict | None:
    print("=" * 50)
    print(f"_map_node_to_event: node_name: {node_name}, payload: {payload}")
    print("=" * 50)
    """将 graph node 完成事件映射为 SSE event dict。"""
    mapping = {
        "classify": "classification",
        "doc_worker": "worker_result",
        "code_worker": "worker_result",
        "sql_worker": "worker_result",
    }
    event_type = mapping.get(node_name)
    if event_type is None:
        return None

    event_data = {"node": node_name, "query_id": query_id}

    if node_name == "classify":
        classification = payload.get("classification", {})
        event_data.update({
            "sources": classification.get("sources", []),
            "is_cross_source": classification.get("is_cross_source", False),
            "entities": classification.get("entities", {}),
            "elapsed_ms": 0,
        })
    elif node_name.endswith("_worker"):
        worker_outputs = payload.get("worker_outputs", [])
        if worker_outputs:
            wo = worker_outputs[0]
            worker_type = wo.get("worker_type", node_name.replace("_worker", ""))
            # 提取 top 5 citations 作为 top_sources 推送到前端
            citations = wo.get("citations", [])
            if isinstance(citations, list) and citations:
                top_sources = [_citation_to_source(c, worker_type) for c in citations[:5] if isinstance(c, dict)]
            else:
                top_sources = []
            event_data.update({
                "worker": worker_type,
                "result_count": wo.get("result_count", 0),
                "retrieval_method": "hybrid",
                "elapsed_ms": 0,
                "top_sources": top_sources,
            })

    return {"event": event_type, "data": json.dumps(event_data, ensure_ascii=False, default=str)}

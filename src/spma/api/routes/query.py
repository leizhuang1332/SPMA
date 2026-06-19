"""查询端点——POST /api/v1/query + /api/v1/sql/query。

设计依据: API-01 §2 核心端点
"""

import logging
import time
import uuid

from fastapi import APIRouter
from pydantic import BaseModel

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
                    return {
                        "worker_type": at,
                        "result_count": len(result.get("final_results", [])),
                        "citations": result.get("final_results", []),
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
                    return {
                        "worker_type": at,
                        "result_count": len(result.get("ripgrep_results", [])),
                        "citations": result.get("ripgrep_results", []),
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

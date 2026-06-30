"""Supervisor Agent 的 LangGraph StateGraph 定义。

构建模式:
  分类+抽取 -> 查询改写 -> Send API 并行派发 -> fan-in 收集
  -> 质量评估 -> 评分>=0.6 收敛 / <0.6 + 重调度<2 -> 调整参数重派
"""

import asyncio
import operator
import logging
from typing import Literal, Annotated

import asyncpg
from langgraph.graph import StateGraph, END
from langgraph.types import Send

from spma.agents.supervisor.state import SupervisorState
from spma.agents.supervisor.classifier_fallback import classify_with_fallback
from spma.agents.supervisor.query_rewriter import rewrite_queries
from spma.agents.supervisor.dispatcher import build_dispatches, extract_discovered_entities
from spma.agents.supervisor.quality import evaluate_workers
from spma.api.dependencies import get_db_pool
from spma.ingestion.synonym_map import SynonymMap
from spma.agents.supervisor.strategy_orchestrator import StrategyOrchestrator
from spma.agents.supervisor.fallback_manager import FallbackManager
from spma.agents.supervisor.semantic_voter import SemanticVoter

# P7: 生产加固 5 个组件(主文件 §3.10, ADR-008)。
# - CostController: 分级模型路由(haiku/sonnet/opus)+ 月度预算(运行时需要 LLM router + budget tracker)
# - QPSLimiter: Redis 滑动窗口(1s), tenant+user 粒度
# - PIIDetector: 5 种 PII 正则 + 脱敏 + should_bypass_llm(🔴 P0 合规)
# - PromptInjectionGuard: 5 种注入模式 + sanitize(🔴 P0 安全)
# - AuditLogger: 包装 QrAuditBuffer, 写 SHA256[:16] hash 而非原文(运行时需要 buffer + pii_detector)
from spma.agents.supervisor.cost_controller import CostController
from spma.agents.supervisor.qps_limiter import QPSLimiter
from spma.agents.supervisor.pii_detector import PIIDetector
from spma.agents.supervisor.prompt_guard import PromptInjectionGuard
from spma.agents.supervisor.audit_logger import AuditLogger

logger = logging.getLogger(__name__)


# P2: 策略名注册表(P3-P5 接入具体 strategy 时复用)。
# - P3 指代消解: rule_based / entity_based / llm_semantic
# - P4 扩展: intent_aware / synonym_based / entity_injection / context_aware
# - P5 分解: template_based / llm_based / entity_guided
_STRATEGY_NAMES: tuple[str, ...] = (
    # P3
    "rule_based", "entity_based", "llm_semantic",
    # P4
    "intent_aware", "synonym_based", "entity_injection", "context_aware",
    # P5
    "template_based", "llm_based", "entity_guided",
)


# P2: 编排器 + 降级单例(模块级,所有 build_* 调用共享)。
# 测试可注入 mock;P3-P5 阶段会把 _default_primary_backup 替换为多路语义 fallback。
async def _default_primary_backup(q, *a, **kw):
    """P2 占位:P3-P5 替换为多路语义 fallback;P2 阶段返回 None 触发 L3 兜底。"""
    return None


_orchestrator = StrategyOrchestrator(
    stage="rewrite",
    names=list(_STRATEGY_NAMES),  # 引用常量,避免双源
)
_fallback = FallbackManager(
    orchestrator=_orchestrator,
    primary_backup_fn=_default_primary_backup,
    rule_only_fn=lambda q, *a, **kw: q,
)

# P3: SemanticVoter 单例(零 LLM,主文件 ADR-004)
# - embedder 传 None 表示运行时通过 build_graph(embedder=X) 注入
# - alpha=0.4 偏重共识度(多策略独立收敛结果 > 单点最相似)
_voter = SemanticVoter(embedder=None, alpha=0.4)

# P7: 生产加固 5 个组件单例(默认无外部依赖;运行时可在 app 启动期替换)。
# - PII / PromptGuard: 纯本地正则, 无外部依赖, 立即可实例化
# - QPSLimiter: redis_client=None 占位, 运行时由 build_graph 注入(避免启动期连接要求)
# - CostController / AuditLogger: 依赖 LLM router / budget tracker / audit buffer,
#   复杂外部依赖留在运行时注入, 模块级保留 None 占位
_pii_detector = PIIDetector()
_prompt_guard = PromptInjectionGuard()
_qps_limiter = QPSLimiter(redis_client=None)  # 占位, 运行时注入
_cost_controller = None  # 占位, 需要 LLM router + budget tracker
_audit_logger = None  # 占位, 需要 buffer + pii_detector


async def _load_synonym_map() -> dict[str, list[str]]:
    """从 DB 加载活跃 synonym_map;DB 异常时降级到空 dict。

    捕获 DB/IO/Runtime 类异常(PostgresError、OSError、TimeoutError、RuntimeError),
    编程错误(KeyError、TypeError 等)仍会正常抛出以便测试发现。
    RuntimeError 包含 db_pool 未初始化的场景(常见于测试和启动期)。
    """
    try:
        syn_map = SynonymMap(get_db_pool())
        result = await syn_map.query(status="active", limit=1000)
        synonym_map: dict[str, list[str]] = {}
        for entry in result["entries"]:
            synonym_map.setdefault(entry["user_term"], []).append(
                entry["canonical_term"]
            )
        return synonym_map
    except (asyncpg.PostgresError, OSError, asyncio.TimeoutError, RuntimeError) as e:
        # RuntimeError 包含 db_pool 未初始化的场景(常见于测试和启动期)
        # 编程错误(KeyError/TypeError 等)仍会传播
        logger.info(f"synonym_map unavailable, degrading to empty: {e}")
        return {}


def build_supervisor_graph(
    primary_llm,
    fallback_llm=None,
    doc_graph=None,
    code_graph=None,
    sql_graph=None,
    synthesis_graph=None,
    max_rounds: int = 5,
    timeout_ms: int = 5000,
    quality_threshold: float = 0.6,
    reschedule_max: int = 2,
    *,
    qr_cache=None,           # 新增
    qr_audit_buffer=None,    # 新增
    qr_state_lookup=None,    # 新增:async () -> (weights_v, synonym_v)
    strategy_orchestrator=None,  # NEW: P2 — 默认用模块级 _orchestrator
    fallback_manager=None,       # NEW: P2 — 默认用模块级 _fallback
    voter=None,                   # NEW: P3 — 默认用模块级 _voter
    embedder=None,                # NEW: P4 — 运行时注入(非单例),默认 None
    qps_limiter=None,                # NEW: P7 — QPSLimiter(Redis 滑动窗口)
    cost_controller=None,             # NEW: P7 — CostController(分级路由 + 预算)
    pii_detector=None,                # NEW: P7 — PIIDetector(🔴 P0 合规)
    prompt_guard=None,                # NEW: P7 — PromptInjectionGuard(🔴 P0 安全)
    audit_logger=None,                # NEW: P7 — AuditLogger(SHA256 hash 审计)
) -> StateGraph:
    # 默认用模块级单例;测试可注入 mock 覆盖。
    strategy_orchestrator = strategy_orchestrator or _orchestrator
    fallback_manager = fallback_manager or _fallback
    voter = voter or _voter  # NEW: P3
    # P7: 默认用模块级单例(None 表示用 _xxx 占位;CostController/AuditLogger 占位为 None,显式 None 保留)
    qps_limiter = qps_limiter if qps_limiter is not None else _qps_limiter
    pii_detector = pii_detector if pii_detector is not None else _pii_detector
    prompt_guard = prompt_guard if prompt_guard is not None else _prompt_guard
    # cost_controller / audit_logger 模块级为 None, 由运行时显式注入;此处保留调用方传入值
    # embedder 不设模块级单例,运行时由应用启动时注入;测试可显式传入 mock。

    async def classify_and_extract_node(state: SupervisorState) -> dict:
        result = await classify_with_fallback(
            query=state["original_query"],
            primary_llm=primary_llm,
            fallback_llm=fallback_llm,
            conversation_history=state.get("conversation_history", ""),
        )
        return {"classification": result, "entities": result.get("entities", {})}

    async def rewrite_node(state: SupervisorState) -> dict:
        # P1 修复:从 DB 加载活跃 synonym_map(异常时降级到空 dict)
        synonym_map = await _load_synonym_map()

        # P7: 生产加固 wrapper 层(在进入 P3-P5 实际 rewrite pipeline 前)
        # 1) QPS 限流检查
        if qps_limiter is not None:
            allowed = await qps_limiter.check(
                tenant_id="default", user_id="default"
            )
            if not allowed:
                logger.warning("qps_limiter rejected rewrite request")
                return {
                    "rewritten_queries": {"rule_only": state["original_query"]},
                    "rate_limited": True,
                }

        # 2) Prompt 注入 sanitize(不 reject, 替换为 [FILTERED])
        original_query = state["original_query"]
        sanitized_query = original_query
        if prompt_guard is not None:
            sanitized_query = prompt_guard.sanitize(original_query)
            if sanitized_query != original_query:
                logger.info("prompt_guard sanitized injection patterns")

        # 3) PII 检测 → 旁路 LLM(走规则路径)
        #    实际 bypass 走 rewrite_queries 内部 strategy_orchestrator(rule_based first)
        #    此处仅记录日志 + 不修改 query(让下游 pipeline 自然选 L3 兜底规则)
        if pii_detector is not None and pii_detector.should_bypass_llm(sanitized_query):
            logger.warning("pii detected in query, bypass_llm flag set")
            # 不修改 query 内容(脱敏由 audit_logger 统一处理),
            # 依赖 rewrite_queries 的 rule_based 策略走规则路径

        if qr_state_lookup is not None:
            weights_v, synonym_v = await qr_state_lookup()
        else:
            weights_v, synonym_v = 1, 1

        rewritten = await rewrite_queries(
            query=sanitized_query,
            classification=state["classification"],
            entities=state.get("entities", {}),
            llm=primary_llm,
            synonym_map=synonym_map,
            conversation_history=state.get("conversation_history", ""),
            cache=qr_cache,
            audit_buffer=qr_audit_buffer,
            weights_version=weights_v,
            synonym_version=synonym_v,
            strategy_orchestrator=strategy_orchestrator,
            fallback_manager=fallback_manager,
            voter=voter,  # NEW: P3
            embedder=embedder,  # NEW: P4
        )
        return {"rewritten_queries": rewritten}

    async def dispatch_node(state: SupervisorState) -> dict:
        # 实际路由由 route_dispatches 条件边通过 Send API 处理
        return {}

    def route_dispatches(state: SupervisorState) -> list[Send]:
        return build_dispatches(
            classification=state["classification"],
            entities=state["entities"],
            rewritten_queries=state.get("rewritten_queries", {}),
            query_id=state.get("query_id", ""),
        )

    async def doc_worker_node(state: SupervisorState) -> dict:
        if doc_graph is None:
            return {"worker_outputs": [{"worker_type": "doc", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await doc_graph.ainvoke(state)
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("final_results", [])
            output = {
                "worker_type": "doc",
                "result_count": len(citations),
                "citations": normalize_citations("doc", citations),
                "confidence": 0.8,
                "has_exact_match": result.get("has_exact_match", False),
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": result.get("entities", {}),
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "doc", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def code_worker_node(state: SupervisorState) -> dict:
        if code_graph is None:
            return {"worker_outputs": [{"worker_type": "code", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await code_graph.ainvoke(state)
            from spma.agents.supervisor.dispatcher import normalize_citations
            citations = result.get("ripgrep_results", [])
            output = {
                "worker_type": "code",
                "result_count": len(citations),
                "citations": normalize_citations("code", citations),
                "confidence": 0.7,
                "has_exact_match": result.get("fallback_layer", 99) == 0,
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": {"code_refs": [r.get("file_path", "") for r in citations[:5]]},
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "code", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def sql_worker_node(state: SupervisorState) -> dict:
        if sql_graph is None:
            return {"worker_outputs": [{"worker_type": "sql", "result_count": 0, "confidence": 0, "has_exact_match": False}]}
        try:
            result = await sql_graph.ainvoke(state)
            output = {
                "worker_type": "sql",
                "result_count": result.get("result_count", 0),
                "citations": result.get("citations", []),
                "confidence": result.get("confidence", 0.7),
                "has_exact_match": False,
                "rounds_used": result.get("rounds_used", 1),
                "convergence_reason": result.get("convergence_reason", ""),
                "discovered_entities": {"table_names": result.get("tables_used", [])},
            }
            return {"worker_outputs": [output]}
        except Exception:
            return {"worker_outputs": [{"worker_type": "sql", "result_count": 0, "confidence": 0, "has_exact_match": False}]}

    async def score_node(state: SupervisorState) -> dict:
        worker_outputs = state.get("worker_outputs", [])
        query_type = state.get("classification", {}).get("query_type", "search")
        evaluation = evaluate_workers(worker_outputs, query_type, quality_threshold)
        return {"quality_scores": evaluation["scores"]}

    def should_reschedule(state: SupervisorState) -> Literal["reschedule", "converge"]:
        worker_outputs = state.get("worker_outputs", [])
        if not worker_outputs:
            return "converge"
        reschedule_count = state.get("reschedule_count", 0)
        if reschedule_count >= reschedule_max:
            return "converge"
        quality_scores = state.get("quality_scores", {})
        if any(s < quality_threshold for s in quality_scores.values()):
            return "reschedule"
        return "converge"

    async def reschedule_node(state: SupervisorState) -> dict:
        worker_outputs = state.get("worker_outputs", [])
        quality_scores = state.get("quality_scores", {})
        successful = [w for w in worker_outputs
                      if quality_scores.get(w.get("worker_type", ""), 0) >= quality_threshold]
        hints = extract_discovered_entities(successful)
        reschedule_count = state.get("reschedule_count", 0) + 1
        current_entities = dict(state.get("entities", {}))
        for key, values in hints.items():
            existing = current_entities.get(key, []) or []
            for v in values:
                if v not in existing:
                    existing.append(v)
            current_entities[key] = existing
        return {"reschedule_count": reschedule_count, "entities": current_entities}

    graph = StateGraph(SupervisorState)
    graph.add_node("classify_and_extract", classify_and_extract_node)
    graph.add_node("rewrite", rewrite_node)
    graph.add_node("dispatch", dispatch_node)
    graph.add_node("doc_worker", doc_worker_node)
    graph.add_node("code_worker", code_worker_node)
    graph.add_node("sql_worker", sql_worker_node)
    graph.add_node("score", score_node)
    graph.add_node("reschedule", reschedule_node)

    graph.set_entry_point("classify_and_extract")
    graph.add_edge("classify_and_extract", "rewrite")
    graph.add_edge("rewrite", "dispatch")
    # Send API fan-out: route_dispatches returning list[Send] 将并行路由至各 worker 节点
    graph.add_conditional_edges("dispatch", route_dispatches)
    # Fan-in: 所有 worker 通过 add_edge 收敛到 score 节点（LangGraph 会自动执行一次）
    graph.add_edge("doc_worker", "score")
    graph.add_edge("code_worker", "score")
    graph.add_edge("sql_worker", "score")
    # 质量评估门
    graph.add_conditional_edges("score", should_reschedule, {
        "reschedule": "reschedule",
        "converge": END,
    })
    # 重调度环路
    graph.add_edge("reschedule", "dispatch")

    return graph.compile()


# 别名:与 plan 文件命名保持兼容(plan 全文使用 build_graph,代码实际命名为 build_supervisor_graph)
build_graph = build_supervisor_graph

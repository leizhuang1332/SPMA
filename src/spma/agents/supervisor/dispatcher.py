"""Supervisor 派发器——构造 WorkerDispatch -> LangGraph Send API 并行派发。"""

from langgraph.types import Send
from spma.models.worker_output import WorkerDispatch


def build_dispatches(
    classification: dict,
    entities: dict,
    rewritten_queries: dict[str, str],
    query_id: str,
    max_rounds_map: dict[str, int] | None = None,
    timeout_ms_map: dict[str, int] | None = None,
) -> list[Send]:
    sources = classification.get("sources", [])
    max_rounds = max_rounds_map or {"doc": 3, "code": 3, "sql": 5}
    timeouts = timeout_ms_map or {"doc": 2000, "code": 2000, "sql": 3000}

    dispatches: list[Send] = []
    for source in sources:
        dispatch: WorkerDispatch = {
            "task_id": f"{query_id}-{source}",
            "query_id": query_id,
            "agent_type": source,
            "original_query": rewritten_queries.get(source, rewritten_queries.get("original", "")),
            "rewritten_query": rewritten_queries.get(source, ""),
            "entities": entities,
            "max_rounds": max_rounds.get(source, 3),
            "timeout_ms": timeouts.get(source, 2000),
        }
        dispatches.append(Send(f"{source}_worker", dispatch))
    return dispatches


def extract_discovered_entities(worker_outputs: list[dict]) -> dict:
    hints: dict[str, list[str]] = {"req_ids": [], "table_names": [], "code_refs": []}
    for output in worker_outputs:
        discovered = output.get("discovered_entities", {}) or {}
        for key in hints:
            values = discovered.get(key, []) or []
            for v in values:
                if v not in hints[key]:
                    hints[key].append(v)
    return {k: v for k, v in hints.items() if v}

"""Supervisor 重调度——从成功Worker提取实体注入失败Worker。"""

import logging

logger = logging.getLogger(__name__)


async def build_reschedule_hints(
    worker_outputs: list[dict],
    quality_scores: dict[str, float],
    threshold: float = 0.6,
) -> dict:
    """从成功 Worker 提取 discovered_entities，注入失败 Worker。"""
    from spma.agents.supervisor.dispatcher import extract_discovered_entities
    successful = [w for w in worker_outputs
                  if quality_scores.get(w.get("worker_type", ""), 0) >= threshold]
    hints = extract_discovered_entities(successful)
    logger.info(f"重调度 hints: {hints}")
    return hints

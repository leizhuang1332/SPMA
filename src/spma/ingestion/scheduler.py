"""APScheduler 摄入调度——cron/interval 定时任务。

独立进程: uv run spma-ingest
"""

import asyncio
import logging
import os
import signal
import threading
import yaml
from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)


def main():
    """入口: uv run spma-ingest"""
    config_path = os.environ.get("SPMA_CONFIG_PATH",
        os.path.join(os.path.dirname(__file__), "..", "..", "config", "spma.yaml"))
    try:
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}
    except Exception:
        logger.warning(f"无法读取配置文件: {config_path}")
        config = {}

    ingestion_cfg = config.get("ingestion", {})
    db_pool = None

    async def _init():
        nonlocal db_pool
        import asyncpg
        pg_cfg = config.get("spma", {}).get("connections", {}).get("postgres", {})
        dsn = pg_cfg.get("readonly_replica") or pg_cfg.get("vector_db", "")
        if dsn:
            db_pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10)

    asyncio.run(_init())

    scheduler = BackgroundScheduler()
    shutdown_event = threading.Event()

    def shutdown(signum=None, frame=None):
        if not shutdown_event.is_set():
            shutdown_event.set()
            if scheduler.running:
                scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    doc_cfg = ingestion_cfg.get("doc", {})
    sql_cfg = ingestion_cfg.get("sql", {})
    synonym_cfg = ingestion_cfg.get("synonym_map", {})

    # Doc 全量同步 — 每日凌晨 2:00
    doc_cron = doc_cfg.get("full_sync_schedule", "0 2 * * *")
    scheduler.add_job(_run_doc_full_sync, "cron", hour=2, minute=0, id="doc_full_sync")

    # SQL Schema 轮询
    sql_interval = sql_cfg.get("polling_interval_seconds", 600)
    scheduler.add_job(_run_sql_polling, "interval", seconds=sql_interval, id="sql_polling")

    # 同义词自动刷新 — 每日凌晨 3:00
    scheduler.add_job(_run_synonym_refresh, "cron", hour=3, minute=0, id="synonym_refresh")

    # 新鲜度 SLO 检查 — 每 5 分钟
    scheduler.add_job(_run_freshness_check, "interval", seconds=300, id="freshness_check")

    scheduler.start()
    print("SPMA Ingestion Scheduler started. Press Ctrl+C to exit.")

    try:
        shutdown_event.wait()
    except KeyboardInterrupt:
        shutdown()
    finally:
        if db_pool:
            asyncio.run(db_pool.close())


def _run_doc_full_sync():
    logger.info("开始 Doc 全量同步...")


def _run_sql_polling():
    logger.info("执行 SQL Schema 定时轮询...")


def _run_synonym_refresh():
    logger.info("刷新同义词映射表...")


def _run_freshness_check():
    logger.debug("检查知识新鲜度 SLO...")

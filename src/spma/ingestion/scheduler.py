"""APScheduler 摄入调度——cron/webhook/interval 三种触发方式。

设计依据: SPMA-design-05 §4 摄入调度
"""

import signal
import threading

from apscheduler.schedulers.background import BackgroundScheduler


def main():
    """入口: uv run spma-ingest"""
    scheduler = BackgroundScheduler()
    shutdown_event = threading.Event()

    def shutdown(signum=None, frame=None):
        if not shutdown_event.is_set():
            shutdown_event.set()
            if scheduler.running:
                scheduler.shutdown(wait=False)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    scheduler.start()
    print("SPMA Ingestion Scheduler started. Press Ctrl+C to exit.")

    try:
        shutdown_event.wait()
    except KeyboardInterrupt:
        shutdown()


# ============================================================
# Schema 定时轮询——每 10 分钟检查 information_schema 变更
# ============================================================


async def schedule_schema_polling(
    db_connection_string: str,
    vector_store=None,
    embedding_client=None,
    interval_minutes: int = 10,
):
    """启动 APScheduler 定时轮询 job。"""
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from spma.ingestion.sql_pipeline import run_schema_ingestion

    scheduler = AsyncIOScheduler()

    @scheduler.scheduled_job("interval", minutes=interval_minutes)
    async def _poll():
        try:
            written = await run_schema_ingestion(
                db_connection_string=db_connection_string,
                vector_store=vector_store,
                embedding_client=embedding_client,
            )
            print(f"Schema 摄入完成: {written} 张表")
        except Exception as e:
            print(f"Schema 摄入失败: {e}")

    scheduler.start()
    return scheduler

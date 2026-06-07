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

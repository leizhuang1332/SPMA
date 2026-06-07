"""手动触发摄入管道——绕过 APScheduler 直接执行。

用法:
    uv run python scripts/run_ingestion.py --source doc
    uv run python scripts/run_ingestion.py --source code --repos auth-service
    uv run python scripts/run_ingestion.py --source sql
"""

def main():
    """解析参数，执行对应管道的单次摄入。"""
    raise NotImplementedError


if __name__ == "__main__":
    main()

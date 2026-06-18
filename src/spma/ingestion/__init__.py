"""数据摄入管道——三种异构数据源的离线/异步同步。

支持: PRD 文档(Confluence Webhook) + 代码仓库(Git Webhook) + SQL Schema(定时轮询)
新鲜度目标: 文档/代码 < 5min, Schema < 10min

设计依据: SPMA-design-05 数据摄入管道设计
"""

from spma.ingestion.sql_pipeline import SqlIngestionPipeline
from spma.ingestion.run_store import PipelineRunStore

__all__ = ["SqlIngestionPipeline", "PipelineRunStore"]

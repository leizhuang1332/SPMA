"""检索日志记录——结构化写入 Kafka/ClickHouse。

记录: BM25 Top-20 + 向量 Top-20 + RRF 融合 Top-10 + Agent 循环信息
用户反馈异步回填，不阻塞检索主链路。

设计依据: SPMA-design-02 §1.5.3 埋点日志结构详情
"""

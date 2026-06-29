"""QueryCache 端到端集成测试:Testcontainers PG + pgvector + Redis stub。

环境要求:
- 本机 Docker daemon 可达(127.0.0.1:2375 或默认 docker.sock)
- 无 Docker 时所有测试优雅跳过,而不是 fail/error。

参考设计: docs/superpowers/specs/2026-06-29-qr-cache-and-observability-design.md §3
"""

import socket
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from spma.agents.supervisor.query_cache import (
    L1Cache,
    L2Cache,
    QueryCache,
)


class RedisStub:
    """最小 Redis stub,只支持 setex/get/delete。"""

    def __init__(self) -> None:
        self.store: dict[bytes, bytes] = {}

    async def get(self, k):
        return self.store.get(k.encode())

    async def setex(self, k, ttl, v):
        self.store[k.encode()] = v if isinstance(v, bytes) else v.encode()

    async def delete(self, k):
        self.store.pop(k.encode(), None)


class FakeEmbedder:
    """稳定 hash 嵌入:同 query 同 embedding,不同 query 不同 embedding。

    使用 SHA-512 链式扩展生成 1024 维浮点向量。
    """

    async def embed_query(self, q: str) -> list[float]:
        import hashlib
        import struct

        digest = hashlib.sha512(q.encode()).digest()
        floats: list[float] = []
        while len(floats) < 1024:
            chunk = digest[:8]
            digest = hashlib.sha512(digest).digest()
            f = struct.unpack("<d", chunk)[0] / 1e18
            floats.append(f)
        return floats


def _docker_reachable() -> bool:
    """看本地 docker daemon 是否可达。用于在无 Docker 环境跳过。

    多重启发式:既检查 TCP 端口,也检查 unix socket 文件是否存在并且
    大小非零(防止空 socket 文件误报)。最后再做一次轻量 HTTP 探活
    以排除占位符 socket。
    """
    # 1) TCP 端口(标准 docker daemon 监听)
    try:
        s = socket.create_connection(("127.0.0.1", 2375), timeout=0.3)
        s.close()
        return True
    except OSError:
        pass

    # 2) Unix socket 候选路径
    candidates = [
        Path("/var/run/docker.sock"),
        Path.home().joinpath(".docker/run/docker.sock"),
    ]
    for sock in candidates:
        if not sock.exists():
            continue
        # 空/极小 socket 文件通常是占位符,跳过
        try:
            if sock.stat().st_size == 0:
                continue
        except OSError:
            continue
        # 3) 尝试实际连接
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(0.3)
            s.connect(str(sock))
            s.close()
            return True
        except OSError:
            continue
    return False


pytestmark_docker = pytest.mark.skipif(
    not _docker_reachable(),
    reason="Docker daemon not reachable; integration tests skipped",
)


@pytest.fixture
def pg_with_pgvector(pg_with_pgvector):
    """本文件内 override:conftest 的 session 级 fixture 在初始化时会尝试启动
    testcontainers 容器(连接 docker daemon),在无 Docker 环境下会 ERROR。

    pytest 的 module 级 fixture 会覆盖同名的 conftest fixture。这里
    先做可达性检查,如果不可达就 pytest.skip,这样 pytest 会把依赖此
    fixture 的所有测试标记为 skipped,而不是 ERROR。
    """
    if not _docker_reachable():
        pytest.skip("Docker daemon not reachable; integration tests skipped")
    return pg_with_pgvector


@pytest.fixture
async def pg_with_table(pg_with_pgvector):
    sql_path = (
        Path(__file__).resolve().parents[2]
        / "deployments/docker/migrations/002_qr_cache_and_state.sql"
    )
    async with pg_with_pgvector.acquire() as conn:
        await conn.execute(sql_path.read_text())
    yield pg_with_pgvector


@pytest.mark.integration
@pytest.mark.asyncio
@pytestmark_docker
async def test_end_to_end_l1_l2_compute(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)

    async def compute(query, entities):
        return {"rewrite": "如何取消订单", "candidates": ["cancel"]}

    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    # 第一次: miss → 走 compute → 写 L1 + L2
    out1 = await qc.lookup_or_compute(
        query="怎么取消订单",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    assert out1["cache_layer"] == "miss"
    assert out1["rewrite"] == "如何取消订单"

    # 第二次: L1 hit
    out2 = await qc.lookup_or_compute(
        query="怎么取消订单",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    assert out2["cache_layer"] == "l1"


@pytest.mark.integration
@pytest.mark.asyncio
@pytestmark_docker
async def test_end_to_end_version_bump_invalidates_l1(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)
    compute = AsyncMock(return_value={"rewrite": "x"})
    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    await qc.lookup_or_compute(
        query="q",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    # bumps weights version → key 不一样 → 必然 miss
    out = await qc.lookup_or_compute(
        query="q",
        history_fingerprint="fp",
        entities={},
        weights_version=2,
        synonym_version=1,
        compute=compute,
    )
    assert out["cache_layer"] == "miss"
    assert compute.await_count == 2


@pytest.mark.integration
@pytest.mark.asyncio
@pytestmark_docker
async def test_pii_query_does_not_pollute_l2(pg_with_table):
    redis = RedisStub()
    l1 = L1Cache(redis, ttl_s=60)
    l2 = L2Cache(pg_with_table, embedding_dim=1024)

    compute = AsyncMock(return_value={"rewrite": "请联系客服"})
    qc = QueryCache(l1=l1, l2=l2, pool=pg_with_table, embedder=FakeEmbedder())

    out = await qc.lookup_or_compute(
        query="我的手机号是 13812345678 怎么改",
        history_fingerprint="fp",
        entities={},
        weights_version=1,
        synonym_version=1,
        compute=compute,
    )
    assert out["cache_layer"] == "miss"

    async with pg_with_table.acquire() as conn:
        rows = await conn.fetch(
            "SELECT 1 FROM qr_cache_entries WHERE query_preview LIKE '%13812345678%'"
        )
        assert len(rows) == 0

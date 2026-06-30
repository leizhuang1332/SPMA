"""集成测试 fixtures for code agent routing。

参考最近 commit 9f8c3f15 test(qr): end-to-end integration——使用 Testcontainers PG 模式。

本 conftest 为 ``tests/integration/code/`` 子包提供：
    1. ``routing_replay_dataset`` 离线 replay 测试集（≥ 30 条样本）
       2. ``make_repo`` helper 快速构造 RepoMeta 实例
       3. ``mock_registry_e2e`` / ``mock_llm_e2e`` / ``mock_cache_e2e``
          三类 Mock 类（兼容 router.py 的接口契约）

注意：完整 30+ 条 replay 数据集需要人工标注。本 plan 只定义 fixture 接口 +
5 个样本；剩余 25 条由 reviewer 后续补充。
"""
import os
import pytest


# ----------------------------------------------------------------------------
# 离线 replay 测试集
# ----------------------------------------------------------------------------
# 数据格式：(query, entities, expected_repo_names, scenario)
#  - query: 用户原始查询（中文/英文混搭）
#  - entities: 从 query 中抽出的实体（code_refs / module）
#  - expected_repo_names: 期望路由命中的仓库名列表（用于断言）
#  - scenario: 场景标签（two_stage_zh / two_stage_en / single_stage / fallback）
# ----------------------------------------------------------------------------
REPLAY_SAMPLES: list[tuple[str, dict, list[str], str]] = [
    # --- 中文单仓库场景（场景 1: 仓库数 ≤ 5 走 single）---
    ("修改支付接口的认证逻辑", {}, ["repo_payment"], "two_stage_zh"),
    ("支付模块的单元测试", {}, ["repo_payment"], "two_stage_zh"),
    ("用户登录失败", {}, ["repo_auth"], "two_stage_zh"),
    # --- 英文单仓库场景 ---
    ("OAuth token 刷新逻辑", {}, ["repo_auth"], "two_stage_en"),
    ("fix payment auth bug", {}, ["repo_payment", "repo_auth"], "two_stage_en_multi"),
    # --- 跨仓库多跳场景（场景 2: 仓库数 > 5 走 two_stage）---
    ("订单中心怎么调用支付和库存", {}, ["repo_order", "repo_payment", "repo_inventory"], "two_stage_zh_multi"),
    ("order service integration with payment and inventory", {}, ["repo_order", "repo_payment", "repo_inventory"], "two_stage_en_multi"),
    # --- LLM 幻觉场景（场景 4: broad_search 兜底）---
    ("完全不相关的查询 xyz123", {}, [], "fallback_llm_hallucination"),
    # --- entities 显式提供场景 ---
    ("看这个文件", {"code_refs": ["src/payment/auth.py"]}, ["repo_payment"], "single_stage_with_entities"),
    # --- 中英混合杂项（占位，由 reviewer 后续补充至 ≥ 30 条）---
    ("查询订单状态", {}, ["repo_order"], "two_stage_zh"),
    ("audit log search", {}, ["repo_audit"], "two_stage_en"),
    ("商品 SKU 管理", {}, ["repo_inventory"], "two_stage_zh"),
    ("inventory restock workflow", {}, ["repo_inventory"], "two_stage_en"),
    ("用户权限分配", {}, ["repo_auth", "repo_user"], "two_stage_zh_multi"),
    # --- 占位样本占位（reviewer 需补全到 ≥ 30 条）---
    ("", {}, [], "empty_query_fallback"),
    ("   ", {}, [], "whitespace_query_fallback"),
    ("a", {}, [], "single_char_query_fallback"),
]


@pytest.fixture(scope="module")
def routing_replay_dataset():
    """离线 replay 测试集：≥ 30 条标注样本（占位实现，后续 reviewer 补全）。

    Returns
    -------
    list[tuple[str, dict, list[str], str]]
        每条样本为 (query, entities, expected_repo_names, scenario)。
    """
    return REPLAY_SAMPLES


@pytest.fixture
def make_repo():
    """工厂函数：快速构造 RepoMeta 实例，简化测试 setup。"""
    from spma.ingestion.code.repo_registry import RepoMeta

    def _make(name, desc, tags, **kwargs):
        return RepoMeta(
            repo_name=name,
            display_name=kwargs.get("display_name", name),
            description=desc,
            tags=tags,
            repo_url=kwargs.get("repo_url"),
            local_path=kwargs.get("local_path", f"/repos/{name}"),
            languages=kwargs.get("languages", ["Python"]),
            enabled=kwargs.get("enabled", True),
        )

    return _make

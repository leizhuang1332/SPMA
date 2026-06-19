"""BGE-Reranker v2 M3 模型加载与异步精排的单元测试。"""
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from llama_index.core.schema import NodeWithScore, TextNode, QueryBundle


def make_node(node_id: str, text: str, score: float = 0.5) -> NodeWithScore:
    """辅助：创建带分数的测试节点。"""
    node = TextNode(id_=node_id, text=text)
    return NodeWithScore(node=node, score=score)


class TestBGERerankerInit:
    """测试 BGEReranker.__init__ —— 模型加载逻辑。"""

    def test_cache_hit_skips_download(self):
        """缓存已存在时，跳过 ModelScope 下载，直接加载 CrossEncoder。"""
        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["config.json", "model.safetensors"]), \
             patch("os.environ.setdefault") as mock_setdefault, \
             patch("spma.retrieval.reranker.CrossEncoder") as mock_cross_encoder, \
             patch("spma.retrieval.reranker.snapshot_download") as mock_download:
            from spma.retrieval.reranker import BGEReranker

            reranker = BGEReranker()

            # 验证 SENTENCE_TRANSFORMERS_HOME 已设置
            mock_setdefault.assert_called_once_with(
                "SENTENCE_TRANSFORMERS_HOME",
                os.path.expanduser("~/.cache/modelscope/hub"),
            )
            # 验证 CrossEncoder 从本地缓存路径加载
            mock_cross_encoder.assert_called_once()
            local_path = mock_cross_encoder.call_args[0][0]
            assert "BAAI/bge-reranker-v2-m3" in local_path
            # 验证没有触发 ModelScope 下载
            mock_download.assert_not_called()

    def test_cache_miss_triggers_download(self):
        """缓存不存在时，触发 ModelScope snapshot_download。"""
        with patch("os.path.isdir", return_value=False), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.CrossEncoder") as mock_cross_encoder, \
             patch("spma.retrieval.reranker.snapshot_download", return_value="/fake/path") as mock_download:
            from spma.retrieval.reranker import BGEReranker

            reranker = BGEReranker()

            mock_download.assert_called_once_with(
                "BAAI/bge-reranker-v2-m3",
                cache_dir=os.path.expanduser("~/.cache/modelscope/hub"),
            )
            mock_cross_encoder.assert_called_once_with("/fake/path", device="cpu")

    def test_download_failure_propagates_exception(self):
        """下载失败时，异常向上传播（由调用方 initialize 处理降级）。"""
        with patch("os.path.isdir", return_value=False), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.CrossEncoder"), \
             patch("spma.retrieval.reranker.snapshot_download", side_effect=RuntimeError("Network error")):
            from spma.retrieval.reranker import BGEReranker

            with pytest.raises(RuntimeError, match="Network error"):
                BGEReranker()

    def test_cross_encoder_load_failure_propagates(self):
        """CrossEncoder 加载失败时，异常向上传播。"""
        with patch("os.path.isdir", return_value=True), \
             patch("os.listdir", return_value=["config.json"]), \
             patch("os.environ.setdefault"), \
             patch("spma.retrieval.reranker.CrossEncoder", side_effect=OSError("Corrupted model")):
            from spma.retrieval.reranker import BGEReranker

            with pytest.raises(OSError, match="Corrupted model"):
                BGEReranker()


class TestBGERerankerRerank:
    """测试 BGEReranker.rerank —— 异步精排逻辑。"""

    @staticmethod
    def _make_reranker_with_model(mock_model):
        """构造一个已注入 mock CrossEncoder 的 BGEReranker 实例，绕过 __init__。"""
        from spma.retrieval.reranker import BGEReranker

        with patch.object(BGEReranker, "__init__", lambda self: None):
            reranker = BGEReranker.__new__(BGEReranker)
        reranker._model = mock_model
        reranker._pool = ThreadPoolExecutor(max_workers=1)
        return reranker

    @pytest.mark.asyncio
    async def test_rerank_sorts_by_score_descending(self):
        """验证 rerank 按 CrossEncoder 分数降序排列。"""
        # 模拟 CrossEncoder.predict 返回的分数
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.9, 0.3, 0.7]

        reranker = self._make_reranker_with_model(mock_model)

        query = QueryBundle(query_str="什么是 Python？")
        nodes = [
            make_node("c1", "Python 是一种编程语言", score=0.5),
            make_node("c2", "Java 是一种编程语言", score=0.5),
            make_node("c3", "Python 简单易学", score=0.5),
        ]

        result = await reranker.rerank(nodes=nodes, query_bundle=query, top_n=2)

        assert len(result) == 2
        assert result[0].node.node_id == "c1"  # score 0.9
        assert result[1].node.node_id == "c3"  # score 0.7
        assert result[0].score == pytest.approx(0.9)
        assert result[1].score == pytest.approx(0.7)

    @pytest.mark.asyncio
    async def test_rerank_top_n_limits_results(self):
        """验证 top_n 参数限制返回数量。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [0.5, 0.4, 0.3, 0.2, 0.1]

        reranker = self._make_reranker_with_model(mock_model)

        query = QueryBundle(query_str="test")
        nodes = [make_node(f"c{i}", f"text{i}") for i in range(5)]

        result = await reranker.rerank(nodes=nodes, query_bundle=query, top_n=3)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_rerank_uses_correct_query_doc_pairs(self):
        """验证 rerank 传递正确的 (query, doc) 对给 CrossEncoder.predict。"""
        mock_model = MagicMock()
        mock_model.predict.return_value = [1.0, 0.5]

        reranker = self._make_reranker_with_model(mock_model)

        query = QueryBundle(query_str="搜索关键词")
        nodes = [
            make_node("a", "文档内容A"),
            make_node("b", "文档内容B"),
        ]

        await reranker.rerank(nodes=nodes, query_bundle=query, top_n=10)

        # 验证 predict 被调用时传入了正确的 (query, doc) 对
        call_args = mock_model.predict.call_args[0][0]
        assert call_args == [("搜索关键词", "文档内容A"), ("搜索关键词", "文档内容B")]

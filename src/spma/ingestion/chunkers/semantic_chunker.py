"""递归语义分块器——按自然边界切割文档。

策略: 先按一级标题切 → 二级标题切 → 段落切 → 句子切
参数: ~500 tokens/块, 50-token overlap
分隔符优先级: \n## > \n### > \n\n > \n > 。
"""

import uuid
from dataclasses import dataclass, field

import tiktoken


def estimate_tokens(text: str, model: str = "cl100k_base") -> int:
    """估算文本的 token 数量。"""
    enc = tiktoken.get_encoding(model)
    return len(enc.encode(text))


@dataclass
class DocChunk:
    chunk_id: str
    content: str
    source_id: str = ""
    source_type: str = ""
    source_path: str = ""
    req_ids: list[str] = field(default_factory=list)
    doc_type: str = ""
    version: str = ""
    updated_at: str = ""
    chunk_index: int = 0
    page_title: str = ""


class SemanticChunker:
    """递归语义分块器。

    按分隔符优先级递归切分: ## → ### → \n\n → \n → 。
    每个 chunk 控制在 ~500 tokens，相邻 chunk 之间 50-token overlap。
    """

    def __init__(
        self,
        chunk_size_tokens: int = 500,
        overlap_tokens: int = 50,
        min_chunk_size_tokens: int = 100,
    ):
        self.chunk_size_tokens = chunk_size_tokens
        self.overlap_tokens = overlap_tokens
        self.min_chunk_size_tokens = min_chunk_size_tokens
        self._separators = ["\n## ", "\n### ", "\n\n", "\n", "。"]

    def split(
        self,
        text: str,
        source_id: str = "",
        source_type: str = "",
        source_path: str = "",
        req_ids: list[str] | None = None,
        doc_type: str = "",
        version: str = "",
        updated_at: str = "",
        page_title: str = "",
    ) -> list[DocChunk]:
        """将文本切分为 chunk 列表。"""
        req_ids = req_ids or []
        # 只有标题级分隔符才触发强制切割；否则短文本直接返回
        heading_separators = ["\n## ", "\n### "]
        has_headings = any(sep in text for sep in heading_separators)

        if estimate_tokens(text) <= self.chunk_size_tokens and not has_headings:
            return [self._make_chunk(
                text, 0, source_id, source_type, source_path, req_ids,
                doc_type, version, updated_at, page_title,
            )]

        sections = self._recursive_split(text, 0)
        chunks = []
        for i, content in enumerate(sections):
            if estimate_tokens(content) < self.min_chunk_size_tokens and len(sections) > 1:
                continue
            chunks.append(self._make_chunk(
                content, i, source_id, source_type, source_path, req_ids,
                doc_type, version, updated_at, page_title,
            ))

        # 如果全部被 min_chunk_size 过滤掉，回退保留原始 section
        if not chunks and sections:
            for i, content in enumerate(sections):
                chunks.append(self._make_chunk(
                    content, i, source_id, source_type, source_path, req_ids,
                    doc_type, version, updated_at, page_title,
                ))

        if self.overlap_tokens > 0 and len(chunks) > 1:
            for i in range(1, len(chunks)):
                prev_end = chunks[i - 1].content[-200:]
                prefix = self._last_n_tokens(prev_end, self.overlap_tokens)
                chunks[i].content = prefix + chunks[i].content

        return chunks

    def _recursive_split(self, text: str, depth: int) -> list[str]:
        """递归按分隔符切分。"""
        if depth >= len(self._separators):
            return [text]

        sep = self._separators[depth]
        parts = text.split(sep)

        if len(parts) == 1:
            return self._recursive_split(text, depth + 1)

        result = []
        for part in parts:
            stripped = part.strip()
            if not stripped:
                continue
            if estimate_tokens(stripped) <= self.chunk_size_tokens:
                result.append(stripped)
            else:
                result.extend(self._recursive_split(stripped, depth + 1))

        return result

    def _make_chunk(self, content, index, source_id, source_type, source_path, req_ids,
                    doc_type, version, updated_at, page_title) -> DocChunk:
        return DocChunk(
            chunk_id=str(uuid.uuid4()),
            content=content,
            source_id=source_id,
            source_type=source_type,
            source_path=source_path,
            req_ids=list(req_ids),
            doc_type=doc_type,
            version=version,
            updated_at=updated_at,
            chunk_index=index,
            page_title=page_title,
        )

    def _last_n_tokens(self, text: str, n: int) -> str:
        enc = tiktoken.get_encoding("cl100k_base")
        tokens = enc.encode(text)
        if len(tokens) <= n:
            return text
        return enc.decode(tokens[-n:])

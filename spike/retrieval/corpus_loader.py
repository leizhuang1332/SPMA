"""从 JSON 文件加载三类检索语料。"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

SourceType = Literal["doc", "code", "sql"]


@dataclass
class Document:
    """单个可检索的文档片段。"""
    id: str
    source_type: SourceType
    content: str
    metadata: dict = field(default_factory=dict)


class CorpusLoader:
    """加载并管理 spike 本地语料。"""

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)

    def load_docs(self) -> list[Document]:
        """加载 Confluence 文档语料。"""
        return self._load_file("docs_sample.json", "doc")

    def load_code(self) -> list[Document]:
        """加载代码文件语料。"""
        return self._load_file("code_sample.json", "code")

    def load_sql_schema(self) -> list[Document]:
        """加载 SQL schema 语料。"""
        return self._load_file("sql_schema_sample.json", "sql")

    def load_all(self) -> dict[SourceType, list[Document]]:
        """加载全部三类语料。

        Returns:
            {"doc": [...], "code": [...], "sql": [...]}
        """
        return {
            "doc": self.load_docs(),
            "code": self.load_code(),
            "sql": self.load_sql_schema(),
        }

    def _load_file(self, filename: str, source_type: SourceType) -> list[Document]:
        filepath = self.corpus_dir / filename
        if not filepath.exists():
            raise FileNotFoundError(
                f"语料文件不存在: {filepath}\n"
                f"请先将数据源导出为 JSON 放入 {self.corpus_dir}/"
            )
        with open(filepath, encoding="utf-8") as f:
            raw = json.load(f)

        documents = []
        for item in raw:
            doc = Document(
                id=item["id"],
                source_type=source_type,
                content=item.get("content", ""),
                metadata=item.get("metadata", {}),
            )
            documents.append(doc)
        return documents

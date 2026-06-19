"""Source handlers — fetch documents from various sources (Confluence, local markdown, etc.)."""

from spma.ingestion.source_handlers.base import SourceDocument, SourceHandler
from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler

__all__ = ["SourceDocument", "SourceHandler", "MarkdownDirSourceHandler"]

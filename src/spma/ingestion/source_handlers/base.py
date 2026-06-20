"""Source handler protocol — decouples "fetch documents" from "process documents"."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol

from spma.api.schemas.ingestion import DocIngestionRequest


@dataclass
class SourceDocument:
    """Standardized document object produced by source handlers."""

    text: str
    """Document body content."""

    source_id: str
    """Unique identifier — SHA256 of absolute file path for markdown, page_id for Confluence."""

    source_type: str
    """"confluence" | "markdown_dir" | "wiki_api"."""

    source_path: str = ""
    """Human-readable source path — absolute file path for markdown, page URL for Confluence/Wiki."""

    page_title: str = ""
    """Document title — filename stem for markdown, page title for Confluence."""

    doc_type: str = "prd"
    version: str = ""
    req_ids: list[str] | None = None
    updated_at: str | None = None
    """ISO 8601 timestamp of last modification."""


class SourceHandler(Protocol):
    """Protocol for document source handlers.

    Each implementation fetches documents from a specific source type
    and yields standardized SourceDocument objects.
    """

    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]:
        """Yield documents matching the request parameters.

        The handler is responsible for:
        - Resolving the source path (request param, config fallback, etc.)
        - Scanning/listing documents matching filters
        - Reading document content
        - Yielding SourceDocument objects one at a time

        Errors for individual documents should be logged and skipped —
        the caller handles per-document error reporting.
        """
        ...

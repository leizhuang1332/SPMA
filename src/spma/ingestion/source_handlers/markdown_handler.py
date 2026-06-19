"""MarkdownDirSourceHandler — scan local directories for .md files and yield SourceDocuments."""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from spma.api.schemas.ingestion import DocIngestionRequest
from spma.ingestion.source_handlers.base import SourceDocument

logger = logging.getLogger(__name__)

# Maximum file size to read (10 MB)
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024


class MarkdownDirSourceHandler:
    """Scans local directories for Markdown files and yields SourceDocuments.

    Supports:
    - Single file paths
    - Directory paths (recursive scan for *.md)
    - Glob patterns (e.g. ``docs/**/*.md``)
    - Incremental mode via mtime filtering against last successful ingestion time
    """

    def __init__(self, run_store, config: dict):
        self._run_store = run_store
        self._config = config or {}

    # ── public API ──────────────────────────────────────────────────

    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]:
        """Scan, filter, and yield SourceDocuments per the request."""
        resolved = self._resolve_path(request.path)
        self.validate_path(resolved)
        files = self.expand_files(resolved)

        if request.mode == "incremental":
            last_time = await self._get_last_ingestion_time()
        else:
            last_time = None

        files = self.filter_by_mtime(files, last_time)

        for filepath in sorted(files):
            content = self.read_file_content(filepath)
            if content is None or content.strip() == "":
                continue

            yield SourceDocument(
                text=content,
                source_id=self.make_source_id(filepath),
                source_type="markdown_dir",
                page_title=filepath.stem,
                updated_at=datetime.fromtimestamp(
                    os.path.getmtime(filepath), tz=timezone.utc
                ).isoformat(),
            )

    # ── path resolution ─────────────────────────────────────────────

    def _resolve_path(self, request_path: str | None) -> str:
        """Resolve path: request param > config markdown_dir > error."""
        if request_path:
            return request_path
        doc_config = self._config.get("doc", {})
        fallback = doc_config.get("markdown_dir", "")
        if fallback:
            return fallback
        raise ValueError("path is required for markdown_dir source")

    # Static method for external use
    @staticmethod
    def resolve_path(request_path: str | None, config: dict | None = None) -> str:
        """Static version for external use."""
        if request_path:
            return request_path
        if config:
            fallback = config.get("markdown_dir", "")
            if fallback:
                return fallback
        raise ValueError("path is required for markdown_dir source")

    # ── validation ───────────────────────────────────────────────────

    @staticmethod
    def validate_path(path_str: str) -> None:
        """Raise ValueError if the resolved path does not exist."""
        # For glob patterns, walk up to find first existing ancestor
        if any(c in path_str for c in "*?[]"):
            p = Path(path_str)
            check = p.parent
            while check != check.parent:
                if check.exists():
                    return
                check = check.parent
        if not Path(path_str).exists():
            raise ValueError(f"Path not found: {path_str}")

    # ── file discovery ───────────────────────────────────────────────

    @staticmethod
    def expand_files(path_str: str) -> list[Path]:
        """Expand a path/glob into a list of .md files."""
        p = Path(path_str)

        if p.is_file():
            return [p] if p.suffix == ".md" else []

        if p.is_dir():
            files = list(p.rglob("*.md"))
            return [f for f in files if _is_real_file(f)]

        # Treat as glob
        if path_str.startswith("/"):
            glob_pattern = path_str.lstrip("/")
            files = list(Path("/").glob(glob_pattern))
        else:
            files = list(Path().glob(path_str))

        return [f for f in files if f.suffix == ".md" and _is_real_file(f)]

    # ── incremental filtering ────────────────────────────────────────

    @staticmethod
    def filter_by_mtime(files: list[Path], last_time: float | None) -> list[Path]:
        """Filter files by mtime > last_time. Returns all if last_time is None."""
        if last_time is None:
            return files
        return [f for f in files if os.path.getmtime(f) > last_time]

    async def _get_last_ingestion_time(self) -> float | None:
        """Query the last successful markdown_dir ingestion timestamp."""
        try:
            latest = await self._run_store.get_latest_successful(
                "doc", source_type="markdown_dir"
            )
            if latest and latest.get("started_at"):
                dt = datetime.fromisoformat(
                    str(latest["started_at"]).replace("Z", "+00:00")
                )
                return dt.timestamp()
        except Exception as e:
            logger.warning("Failed to get last ingestion time: %s", e)
        return None

    # ── file reading ─────────────────────────────────────────────────

    @staticmethod
    def read_file_content(filepath: Path) -> str | None:
        """Read file as UTF-8 text. Returns None for unreadable files."""
        try:
            size = filepath.stat().st_size
            if size > MAX_FILE_SIZE_BYTES:
                logger.warning(
                    "Skipping large file %s (%.1f MB > %d MB limit)",
                    filepath, size / (1024 * 1024), MAX_FILE_SIZE_BYTES // (1024 * 1024),
                )
                return None
            return filepath.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError) as e:
            logger.warning("Cannot read %s: %s", filepath, e)
            return None

    # ── source_id generation ─────────────────────────────────────────

    @staticmethod
    def make_source_id(filepath: Path) -> str:
        """Generate a deterministic source_id from the absolute file path."""
        return hashlib.sha256(str(filepath).encode()).hexdigest()


# ── helpers ──────────────────────────────────────────────────────────

def _is_real_file(p: Path) -> bool:
    """Check that a path is a real file, following symlinks but detecting cycles."""
    try:
        resolved = p.resolve()
        return resolved.is_file()
    except (OSError, RuntimeError):
        return False

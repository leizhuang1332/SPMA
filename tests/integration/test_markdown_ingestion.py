"""Integration tests for markdown_dir ingestion — uses real temp directories."""

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler


@pytest.fixture
def handler():
    """Create handler with mock run_store returning no previous runs."""
    mock_store = MagicMock()
    mock_store.get_latest_successful = AsyncMock(return_value=None)
    return MarkdownDirSourceHandler(
        run_store=mock_store,
        config={"doc": {"markdown_dir": "/tmp"}},
    )


@pytest.fixture
def md_tree():
    """Create a temporary markdown file tree.

    Structure:
        tmpdir/
        ├── readme.md
        ├── design.md
        ├── sub/
        │   └── arch.md
        └── notes.txt
    """
    tmpdir = tempfile.TemporaryDirectory()
    base = Path(tmpdir.name)

    (base / "readme.md").write_text("# README\n\nProject overview.", encoding="utf-8")
    (base / "design.md").write_text("# Design\n\n## Overview\n\nDetails here.", encoding="utf-8")
    (base / "sub").mkdir()
    (base / "sub" / "arch.md").write_text("# Architecture\n\n## Components\n\n- A\n- B", encoding="utf-8")
    (base / "notes.txt").write_text("Just notes.", encoding="utf-8")

    yield base
    tmpdir.cleanup()


class TestExpandFilesIntegration:
    """Integration tests for expand_files."""

    def test_expand_directory_finds_all_md_files(self, handler, md_tree):
        files = handler.expand_files(str(md_tree))
        names = {f.name for f in files}
        assert names == {"readme.md", "design.md", "arch.md"}

    def test_expand_single_file(self, handler, md_tree):
        target = str(md_tree / "readme.md")
        files = handler.expand_files(target)
        assert len(files) == 1
        assert files[0].name == "readme.md"

    def test_expand_glob_pattern(self, handler, md_tree):
        pattern = str(md_tree / "sub" / "*.md")
        files = handler.expand_files(pattern)
        assert len(files) == 1
        assert files[0].name == "arch.md"

    def test_expand_recursive_glob(self, handler, md_tree):
        pattern = str(md_tree / "**" / "*.md")
        files = handler.expand_files(pattern)
        assert len(files) == 3


class TestFilterByMtimeIntegration:
    """Integration tests for mtime filtering."""

    def test_mtime_filtering_incremental(self, handler, md_tree):
        import time
        files = [f for f in md_tree.rglob("*.md") if f.suffix == ".md"]

        time.sleep(0.02)
        snapshot_time = time.time()

        result = handler.filter_by_mtime(files, snapshot_time)
        assert len(result) == 0

    def test_mtime_filtering_new_file(self, handler, md_tree):
        import time
        old_files = [f for f in md_tree.rglob("*.md") if f.suffix == ".md"]
        time.sleep(0.02)
        snapshot_time = time.time()

        new_file = md_tree / "new.md"
        new_file.write_text("# New file")

        all_files = old_files + [new_file]
        result = handler.filter_by_mtime(all_files, snapshot_time)
        assert len(result) == 1
        assert result[0].name == "new.md"


class TestReadFileContentIntegration:
    """Integration tests for file reading."""

    def test_reads_utf8_content(self, handler, md_tree):
        content = handler.read_file_content(md_tree / "readme.md")
        assert content == "# README\n\nProject overview."

    def test_reads_empty_file(self, handler, md_tree):
        empty = md_tree / "empty.md"
        empty.write_text("")
        content = handler.read_file_content(empty)
        assert content == ""

    def test_skips_large_file(self, handler, md_tree):
        big = md_tree / "big.md"
        big.write_text("x" * (10 * 1024 * 1024 + 1))
        content = handler.read_file_content(big)
        assert content is None


class TestSourceIdIntegration:
    """Integration tests for source_id generation."""

    def test_source_id_deterministic(self, handler, md_tree):
        id1 = handler.make_source_id(md_tree / "readme.md")
        id2 = handler.make_source_id(md_tree / "readme.md")
        assert id1 == id2

    def test_source_id_unique_per_file(self, handler, md_tree):
        id1 = handler.make_source_id(md_tree / "readme.md")
        id2 = handler.make_source_id(md_tree / "design.md")
        assert id1 != id2


class TestResolvePathIntegration:
    """Integration tests for path resolution via instance method."""

    def test_resolve_path_request_wins(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default"}},
        )
        result = handler._resolve_path("/custom/path")
        assert result.startswith("/custom")  # note: resolve() normalizes

    def test_resolve_path_fallback(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default"}},
        )
        result = handler._resolve_path("")
        assert result == "/default"

    def test_resolve_path_none_fallback(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default"}},
        )
        result = handler._resolve_path(None)
        assert result == "/default"

    def test_resolve_path_raises_when_both_empty(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={},
        )
        with pytest.raises(ValueError, match="path is required"):
            handler._resolve_path(None)


class TestValidatePathIntegration:
    """Integration tests for path validation."""

    def test_validate_existing_path(self, handler, md_tree):
        handler.validate_path(str(md_tree))  # should not raise

    def test_validate_nonexistent_path_raises(self, handler):
        with pytest.raises(ValueError, match="Path not found"):
            handler.validate_path("/this/does/not/exist/at/all")

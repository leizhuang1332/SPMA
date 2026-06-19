"""Tests for MarkdownDirSourceHandler."""

import hashlib
import os
import tempfile
import time
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
from spma.ingestion.source_handlers.markdown_handler import MarkdownDirSourceHandler


class TestResolvePath:
    """Path resolution logic."""

    def test_uses_request_path_when_provided(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path("/custom/path")
        assert result == "/custom/path"

    def test_falls_back_to_config_when_request_path_empty(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path("")
        assert result == "/default/path"

    def test_falls_back_to_config_when_request_path_none(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={"doc": {"markdown_dir": "/default/path"}},
        )
        result = handler._resolve_path(None)
        assert result == "/default/path"

    def test_raises_when_both_empty(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={},
        )
        with pytest.raises(ValueError, match="path is required for markdown_dir source"):
            handler._resolve_path(None)


class TestExpandGlob:
    """Glob expansion logic."""

    def test_single_file_returns_itself(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "test.md"
            f.write_text("# Hello")
            result = handler.expand_files(str(f))
            assert len(result) == 1
            assert result[0] == f

    def test_directory_recursively_collects_md_files(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a.md").write_text("# A")
            (base / "sub").mkdir()
            (base / "sub" / "b.md").write_text("# B")
            (base / "notes.txt").write_text("not markdown")

            result = handler.expand_files(str(base))
            paths = {str(p) for p in result}
            assert len(result) == 2
            assert str(base / "a.md") in paths
            assert str(base / "sub" / "b.md") in paths

    def test_glob_pattern(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "specs").mkdir()
            (base / "docs").mkdir()
            (base / "specs" / "design.md").write_text("# Design")
            (base / "docs" / "readme.md").write_text("# Readme")

            result = handler.expand_files(str(base / "specs" / "*.md"))
            assert len(result) == 1
            assert result[0].name == "design.md"

    def test_glob_recursive(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "sub1").mkdir()
            (base / "sub2").mkdir()
            (base / "sub1" / "a.md").write_text("# A")
            (base / "sub2" / "b.md").write_text("# B")

            result = handler.expand_files(str(base / "**" / "*.md"))
            assert len(result) == 2

    def test_empty_directory_returns_empty_list(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handler.expand_files(str(tmpdir))
            assert result == []

    def test_no_md_files_returns_empty_list(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "notes.txt").write_text("text")
            (base / "data.json").write_text("{}")
            result = handler.expand_files(str(base))
            assert result == []


class TestFilterByMtime:
    """Mtime-based incremental filtering."""

    def test_returns_all_when_last_time_is_none(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        files = [Path("/fake/a.md"), Path("/fake/b.md")]
        result = handler.filter_by_mtime(files, None)
        assert result == files

    def test_filters_out_unchanged_files(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            old = base / "old.md"
            old.write_text("# Old")
            old_mtime = os.path.getmtime(old)

            time.sleep(0.01)  # ensure different mtime
            new = base / "new.md"
            new.write_text("# New")

            result = handler.filter_by_mtime([old, new], old_mtime)
            assert len(result) == 1
            assert result[0] == new


class TestValidatePath:
    """Path validation logic."""

    def test_existing_path_passes(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            handler.validate_path(tmpdir)  # should not raise

    def test_nonexistent_path_raises(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with pytest.raises(ValueError, match="Path not found"):
            handler.validate_path("/nonexistent/path/xyz")

    def test_glob_with_existing_parent_passes(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            handler.validate_path(str(base / "*.md"))  # should not raise

    def test_glob_with_nonexistent_parent_raises(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with pytest.raises(ValueError, match="Path not found"):
            handler.validate_path("/nonexistent/**/*.md")


class TestReadFileContent:
    """File reading and encoding handling."""

    def test_reads_utf8_file(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "doc.md"
            f.write_text("# Hello\n\nWorld", encoding="utf-8")
            content = handler.read_file_content(f)
            assert content == "# Hello\n\nWorld"

    def test_empty_file_returns_empty_string(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "empty.md"
            f.write_text("")
            content = handler.read_file_content(f)
            assert content == ""

    def test_non_utf8_file_is_handled(self):
        """Non-UTF-8 files: read_file_content should return None on decode error."""
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        with tempfile.TemporaryDirectory() as tmpdir:
            f = Path(tmpdir) / "latin1.md"
            # Write bytes that are NOT valid UTF-8
            f.write_bytes(b"caf\xe9\n")  # 0xe9 is invalid UTF-8
            content = handler.read_file_content(f)
            assert content is None


class TestSourceId:
    """SHA256 source_id generation."""

    def test_generates_sha256_of_absolute_path(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        path = Path("/data/docs/readme.md")
        expected = hashlib.sha256(str(path).encode()).hexdigest()
        result = handler.make_source_id(path)
        assert result == expected
        assert len(result) == 64

    def test_different_paths_produce_different_ids(self):
        handler = MarkdownDirSourceHandler(run_store=MagicMock(), config={})
        id1 = handler.make_source_id(Path("/data/a.md"))
        id2 = handler.make_source_id(Path("/data/b.md"))
        assert id1 != id2


class TestFetchDocuments:
    """Integration of the full fetch_documents flow."""

    @pytest.mark.asyncio
    async def test_yields_source_documents_for_each_file(self):
        mock_run_store = MagicMock()
        mock_run_store.get_latest_successful = AsyncMock(return_value=None)

        handler = MarkdownDirSourceHandler(
            run_store=mock_run_store,
            config={"doc": {"markdown_dir": "/tmp"}},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "a.md").write_text("# Alpha")
            (base / "b.md").write_text("# Beta")
            (base / "notes.txt").write_text("text")

            request = DocIngestionRequest(
                source=DocIngestionSource.MARKDOWN_DIR,
                mode="full",
                path=str(base),
            )

            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

            assert len(docs) == 2
            titles = {d.page_title for d in docs}
            assert "a" in titles
            assert "b" in titles
            for d in docs:
                assert d.source_type == "markdown_dir"
                assert len(d.source_id) == 64
                assert d.text != ""

    @pytest.mark.asyncio
    async def test_skips_empty_files(self):
        mock_run_store = MagicMock()
        mock_run_store.get_latest_successful = AsyncMock(return_value=None)

        handler = MarkdownDirSourceHandler(
            run_store=mock_run_store,
            config={},
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            (base / "empty.md").write_text("")
            (base / "has_content.md").write_text("# Content")

            request = DocIngestionRequest(
                source=DocIngestionSource.MARKDOWN_DIR,
                mode="full",
                path=str(base),
            )

            docs = []
            async for doc in handler.fetch_documents(request):
                docs.append(doc)

            assert len(docs) == 1
            assert docs[0].page_title == "has_content"

    @pytest.mark.asyncio
    async def test_raises_for_nonexistent_path(self):
        handler = MarkdownDirSourceHandler(
            run_store=MagicMock(),
            config={},
        )

        request = DocIngestionRequest(
            source=DocIngestionSource.MARKDOWN_DIR,
            mode="full",
            path="/nonexistent/path/xyz",
        )

        with pytest.raises(ValueError, match="Path not found"):
            async for _ in handler.fetch_documents(request):
                pass

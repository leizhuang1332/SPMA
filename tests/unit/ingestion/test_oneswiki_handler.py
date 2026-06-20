"""Tests for OneswikiSourceHandler."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
from spma.ingestion.source_handlers.oneswiki_handler import OneswikiSourceHandler


class TestConfigExtraction:
    """Config extraction and validation."""

    def test_raises_when_config_is_none(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config=None,
        )
        with pytest.raises(ValueError, match="request.config is required"):
            handler._extract_config(request)

    def test_raises_when_auth_token_missing(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "cookie": "c",
                "team_uuid": "t",
                "space_uuid": "s",
                "parent_uuid": "p",
            },
        )
        with pytest.raises(ValueError, match="Missing required config keys.*auth_token"):
            handler._extract_config(request)

    def test_raises_when_cookie_missing(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "a",
                "team_uuid": "t",
                "space_uuid": "s",
                "parent_uuid": "p",
            },
        )
        with pytest.raises(ValueError, match="Missing required config keys.*cookie"):
            handler._extract_config(request)

    def test_raises_when_team_uuid_missing(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "a",
                "cookie": "c",
                "space_uuid": "s",
                "parent_uuid": "p",
            },
        )
        with pytest.raises(ValueError, match="Missing required config keys.*team_uuid"):
            handler._extract_config(request)

    def test_raises_when_space_uuid_missing(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "a",
                "cookie": "c",
                "team_uuid": "t",
                "parent_uuid": "p",
            },
        )
        with pytest.raises(ValueError, match="Missing required config keys.*space_uuid"):
            handler._extract_config(request)

    def test_raises_when_parent_uuid_missing(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "a",
                "cookie": "c",
                "team_uuid": "t",
                "space_uuid": "s",
            },
        )
        with pytest.raises(ValueError, match="Missing required config keys.*parent_uuid"):
            handler._extract_config(request)

    def test_all_required_provided_returns_parsed_config(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "tok",
                "cookie": "ck",
                "team_uuid": "team1",
                "space_uuid": "space1",
                "parent_uuid": "parent1",
            },
        )
        result = handler._extract_config(request)
        assert result["auth_token"] == "tok"
        assert result["cookie"] == "ck"
        assert result["team_uuid"] == "team1"
        assert result["space_uuid"] == "space1"
        assert result["parent_uuid"] == "parent1"
        assert result["base_url"] == "https://ones.jtexpress.com.cn"
        assert result["concurrency"] == 5

    def test_optional_params_override_defaults(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "tok",
                "cookie": "ck",
                "team_uuid": "team1",
                "space_uuid": "space1",
                "parent_uuid": "parent1",
                "base_url": "https://custom.example.com",
                "concurrency": 3,
            },
        )
        result = handler._extract_config(request)
        assert result["base_url"] == "https://custom.example.com"
        assert result["concurrency"] == 3

    def test_concurrency_minimum_is_one(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        request = DocIngestionRequest(
            source=DocIngestionSource.ONES_WIKI,
            mode="full",
            config={
                "auth_token": "tok",
                "cookie": "ck",
                "team_uuid": "team1",
                "space_uuid": "space1",
                "parent_uuid": "parent1",
                "concurrency": 0,
            },
        )
        result = handler._extract_config(request)
        assert result["concurrency"] == 1


class TestBuildSubtree:
    """Subtree construction from flat page list."""

    SAMPLE_PAGES = [
        {"uuid": "root", "parent_uuid": ""},
        {"uuid": "c1", "parent_uuid": "root"},
        {"uuid": "c2", "parent_uuid": "root"},
        {"uuid": "gc1", "parent_uuid": "c1"},
        {"uuid": "gc2", "parent_uuid": "c1"},
        {"uuid": "gc3", "parent_uuid": "c2"},
        {"uuid": "orphan", "parent_uuid": "other"},
    ]

    def test_returns_direct_children(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "root")
        assert "c1" in result
        assert "c2" in result

    def test_returns_grandchildren(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "root")
        assert "gc1" in result
        assert "gc2" in result
        assert "gc3" in result

    def test_does_not_return_root_itself(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "root")
        assert "root" not in result

    def test_does_not_return_orphans(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "root")
        assert "orphan" not in result

    def test_returns_all_descendants_count(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "root")
        assert len(result) == 5  # c1, c2, gc1, gc2, gc3

    def test_empty_list_returns_empty(self):
        result = OneswikiSourceHandler._build_subtree([], "any")
        assert result == []

    def test_no_children_returns_empty(self):
        pages = [{"uuid": "lonely", "parent_uuid": ""}]
        result = OneswikiSourceHandler._build_subtree(pages, "lonely")
        assert result == []

    def test_no_matching_root_returns_empty(self):
        result = OneswikiSourceHandler._build_subtree(self.SAMPLE_PAGES, "nonexistent")
        assert result == []


class TestHtmlToMarkdown:
    """HTML to Markdown conversion."""

    def test_converts_h1(self):
        result = OneswikiSourceHandler._html_to_markdown("<h1>Title</h1>")
        assert "# Title" in result

    def test_converts_h2(self):
        result = OneswikiSourceHandler._html_to_markdown("<h2>Section</h2>")
        assert "## Section" in result

    def test_converts_paragraph(self):
        result = OneswikiSourceHandler._html_to_markdown("<p>Hello world</p>")
        assert "Hello world" in result

    def test_converts_bold(self):
        result = OneswikiSourceHandler._html_to_markdown("<strong>Bold</strong>")
        assert "**Bold**" in result

    def test_converts_emphasis(self):
        result = OneswikiSourceHandler._html_to_markdown("<em>Italic</em>")
        assert "*Italic*" in result

    def test_converts_image_in_figure(self):
        html = (
            '<figure class="ones-image-figure">'
            '<div class="image-wrapper">'
            '<img src="https://example.com/img.png" />'
            '</div></figure>'
        )
        result = OneswikiSourceHandler._html_to_markdown(html)
        assert "![](https://example.com/img.png)" in result

    def test_converts_links(self):
        result = OneswikiSourceHandler._html_to_markdown(
            '<a href="https://example.com">Click</a>'
        )
        assert "[Click](https://example.com)" in result

    def test_converts_unordered_list(self):
        html = "<ul><li>A</li><li>B</li></ul>"
        result = OneswikiSourceHandler._html_to_markdown(html)
        assert "- A" in result
        assert "- B" in result

    def test_strips_script_and_style_tags(self):
        html = "<div>Keep</div><script>drop()</script><style>.x{}</style>"
        result = OneswikiSourceHandler._html_to_markdown(html)
        assert "Keep" in result

    def test_empty_content_returns_empty_string(self):
        result = OneswikiSourceHandler._html_to_markdown("")
        assert result == ""

    def test_whitespace_only_returns_empty_string(self):
        result = OneswikiSourceHandler._html_to_markdown("   ")
        assert result == ""

    def test_fallback_on_error(self):
        """If conversion fails, returns raw HTML."""
        raw = "<custom:invalid>content</custom:invalid>"
        result = OneswikiSourceHandler._html_to_markdown(raw)
        assert len(result) > 0


class TestPageToDocument:
    """Page dict -> SourceDocument mapping."""

    CFG = {
        "base_url": "https://ones.example.com",
        "team_uuid": "team1",
        "space_uuid": "space1",
    }

    def test_maps_all_fields(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        page = {
            "uuid": "page1",
            "title": "Test Page",
            "content": "<h1>Hello</h1>",
            "version": 3,
            "updated_time": 1700000000,
        }
        doc = handler._page_to_document(page, self.CFG)
        assert doc is not None
        assert doc.source_id == "page1"
        assert doc.page_title == "Test Page"
        assert doc.source_type == DocIngestionSource.ONES_WIKI
        assert "Hello" in doc.text
        assert doc.version == "3"
        assert doc.updated_at is not None
        assert doc.source_path == (
            "https://ones.example.com/wiki/team/team1/space/space1/page/page1"
        )
        assert doc.doc_type == "prd"

    def test_page_without_uuid_returns_none(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        page = {"title": "No UUID", "content": "<p>x</p>"}
        doc = handler._page_to_document(page, self.CFG)
        assert doc is None

    def test_page_without_updated_time(self):
        handler = OneswikiSourceHandler(run_store=MagicMock(), config={})
        page = {
            "uuid": "page1",
            "title": "P1",
            "content": "<p>x</p>",
            "version": 1,
            "updated_time": 0,
        }
        doc = handler._page_to_document(page, self.CFG)
        assert doc is not None
        assert doc.updated_at is None


class TestShouldSkip:
    """Incremental skip logic."""

    def test_skips_when_updated_before_last_time(self):
        page = {"updated_time": 100}
        assert OneswikiSourceHandler._should_skip(page, 200) is True

    def test_does_not_skip_when_updated_after_last_time(self):
        page = {"updated_time": 300}
        assert OneswikiSourceHandler._should_skip(page, 200) is False

    def test_skips_when_equal(self):
        page = {"updated_time": 200}
        assert OneswikiSourceHandler._should_skip(page, 200) is True

    def test_handles_missing_updated_time(self):
        page = {}
        assert OneswikiSourceHandler._should_skip(page, 200) is True

    def test_handles_none_last_time(self):
        page = {"updated_time": 100}
        assert OneswikiSourceHandler._should_skip(page, None) is False

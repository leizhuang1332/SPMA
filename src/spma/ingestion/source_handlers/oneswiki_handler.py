"""OneswikiSourceHandler — fetch documents from Ones Wiki via REST API."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncIterator

import httpx

from spma.api.schemas.ingestion import DocIngestionRequest, DocIngestionSource
from spma.ingestion.source_handlers.base import SourceDocument, SourceHandler

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://ones.jtexpress.com.cn"
DEFAULT_CONCURRENCY = 5


class OneswikiSourceHandler:
    """Fetch documents from an Ones Wiki space subtree via REST API.

    Required request.config keys:
        auth_token  — Bearer token for Authorization header
        cookie      — Cookie string
        team_uuid   — Team UUID
        space_uuid  — Space UUID
        parent_uuid — Root page UUID for subtree

    Optional request.config keys:
        base_url    — Ones server base URL (default https://ones.jtexpress.com.cn)
        concurrency — Max concurrent page fetches (default 5, 1 = sequential)
    """

    def __init__(self, run_store, config: dict):
        self._run_store = run_store
        self._config = config or {}

    # ── public API ──────────────────────────────────────────────────

    async def fetch_documents(
        self, request: DocIngestionRequest
    ) -> AsyncIterator[SourceDocument]:
        """Fetch all pages in the subtree and yield SourceDocuments."""
        cfg = self._extract_config(request)

        async with httpx.AsyncClient(base_url=cfg["base_url"], timeout=30.0) as client:
            all_pages = await self._fetch_page_list(client, cfg)
            subtree_uuids = self._build_subtree(all_pages, cfg["parent_uuid"])

            if not subtree_uuids:
                logger.warning(
                    "No pages found in subtree for parent_uuid=%s", cfg["parent_uuid"]
                )
                return

            if request.mode == "incremental":
                last_time = await self._get_last_ingestion_time()
            else:
                last_time = None

            semaphore = asyncio.Semaphore(cfg["concurrency"])

            async def fetch_one(uuid: str):
                async with semaphore:
                    try:
                        return await self._fetch_page_content(client, cfg, uuid)
                    except Exception as e:
                        logger.warning("Failed to fetch page %s: %s", uuid, e)
                        return None

            # Launch all tasks and gather results; successful pages maintain
            # subtree_uuids order. Failed pages return None and are filtered.
            tasks = [asyncio.create_task(fetch_one(uuid)) for uuid in subtree_uuids]
            page_results = await asyncio.gather(*tasks)

            for page in page_results:
                if page is None:
                    continue
                try:
                    doc = self._page_to_document(page, cfg)
                    if doc is None:
                        continue
                    if last_time is not None and self._should_skip(page, last_time):
                        continue
                    yield doc
                except Exception as e:
                    logger.warning("Failed to process page %s: %s", page.get("uuid", "?"), e)

    # ── config extraction ───────────────────────────────────────────

    def _extract_config(self, request: DocIngestionRequest) -> dict:
        """Extract and validate OnesWiki config from request.config."""
        if not request.config:
            raise ValueError("request.config is required for ones_wiki source")

        cfg = request.config
        required = ["auth_token", "cookie", "team_uuid", "space_uuid", "parent_uuid"]
        missing = [k for k in required if not cfg.get(k)]
        if missing:
            raise ValueError(
                f"Missing required config keys for ones_wiki: {', '.join(missing)}"
            )

        return {
            "auth_token": cfg["auth_token"],
            "cookie": cfg["cookie"],
            "team_uuid": cfg["team_uuid"],
            "space_uuid": cfg["space_uuid"],
            "parent_uuid": cfg["parent_uuid"],
            "base_url": cfg.get("base_url", DEFAULT_BASE_URL),
            "concurrency": max(1, int(cfg.get("concurrency", DEFAULT_CONCURRENCY))),
        }

    # ── API calls ────────────────────────────────────────────────────

    async def _fetch_page_list(
        self, client: httpx.AsyncClient, cfg: dict
    ) -> list[dict]:
        """Fetch all pages in a space. Returns raw page list from API."""
        url = f"/wiki/api/wiki/team/{cfg['team_uuid']}/space/{cfg['space_uuid']}/pages"
        headers = self._build_headers(cfg)
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        data = response.json()
        pages = data.get("pages", [])
        logger.info("Fetched %d pages from space %s", len(pages), cfg["space_uuid"])
        return pages

    async def _fetch_page_content(
        self, client: httpx.AsyncClient, cfg: dict, page_uuid: str
    ) -> dict | None:
        """Fetch a single page's full content. Returns parsed JSON dict."""
        url = f"/wiki/api/wiki/team/{cfg['team_uuid']}/page/{page_uuid}?action=view"
        headers = self._build_headers(cfg)
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()

    def _build_headers(self, cfg: dict) -> dict:
        """Build HTTP headers with auth from config."""
        return {
            "Authorization": f"Bearer {cfg['auth_token']}",
            "Cookie": cfg["cookie"],
        }

    @staticmethod
    def _build_subtree(pages: list[dict], root_uuid: str) -> list[str]:
        raise NotImplementedError

    @staticmethod
    def _should_skip(page: dict, last_time: float | None) -> bool:
        raise NotImplementedError

    async def _get_last_ingestion_time(self) -> float | None:
        raise NotImplementedError

    def _page_to_document(self, page: dict, cfg: dict) -> SourceDocument | None:
        raise NotImplementedError

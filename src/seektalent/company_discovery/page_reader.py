from __future__ import annotations

import re
from html import unescape

import httpx

from seektalent.company_discovery.models import PageReadResult

_SCRIPT_STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def clean_page_text(html: str, *, max_chars: int) -> str:
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_chars].rstrip()


class PageReader:
    def __init__(self, http_client: httpx.AsyncClient | None = None, max_chars: int = 12000) -> None:
        self.http_client = http_client
        self.max_chars = max_chars

    async def read(self, url: str, *, timeout_s: float) -> PageReadResult:
        if self.http_client is not None:
            return await self._read_with_client(self.http_client, url, timeout_s=timeout_s)
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await self._read_with_client(client, url, timeout_s=timeout_s)

    async def _read_with_client(self, client: httpx.AsyncClient, url: str, *, timeout_s: float) -> PageReadResult:
        response = await client.get(url, timeout=timeout_s)
        response.raise_for_status()
        body = response.text
        return PageReadResult(
            url=url,
            title=_extract_title(body),
            text=clean_page_text(body, max_chars=self.max_chars),
        )


def _extract_title(html: str) -> str:
    match = _TITLE_RE.search(html)
    if not match:
        return ""
    return _WS_RE.sub(" ", unescape(match.group(1))).strip()

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx

from seektalent.company_discovery.models import SearchRerankResult, WebSearchResult
from seektalent.config import AppSettings

BOCHA_WEB_SEARCH_URL = "https://api.bochaai.com/v1/web-search"
BOCHA_RERANK_URL = "https://api.bochaai.com/v1/rerank"


class BochaWebSearchProvider:
    def __init__(self, settings: AppSettings, http_client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self.http_client = http_client

    async def search(self, query: str, *, count: int) -> list[WebSearchResult]:
        payload = {"query": query, "count": count, "summary": True}
        body = await self._post(BOCHA_WEB_SEARCH_URL, payload)
        return _parse_results(body)

    async def rerank(self, query: str, results: list[WebSearchResult], *, top_n: int) -> list[SearchRerankResult]:
        documents = [
            "\n".join(line for line in [result.title, result.url, result.snippet, result.summary] if line)
            for result in results
        ]
        payload = {
            "model": "gte-rerank",
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
            "return_documents": True,
        }
        body = await self._post(BOCHA_RERANK_URL, payload)
        return _parse_rerank_results(body, results)

    async def _post(self, url: str, payload: Mapping[str, object]) -> Any:
        if not self.settings.bocha_api_key:
            raise ValueError("SEEKTALENT_BOCHA_API_KEY is required when company web discovery runs.")
        headers = {"Authorization": f"Bearer {self.settings.bocha_api_key}"}
        if self.http_client is not None:
            response = await self.http_client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()
        async with httpx.AsyncClient(timeout=self.settings.company_discovery_timeout_seconds) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()
            return response.json()


def _parse_results(payload: Any) -> list[WebSearchResult]:
    items: list[dict[str, Any]] = []
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("malformed Bocha web search response.")
    web_pages = data.get("webPages")
    if isinstance(web_pages, dict):
        value = web_pages.get("value")
        if not isinstance(value, list):
            raise ValueError("malformed Bocha web search response.")
        items.extend(item for item in value if isinstance(item, dict))
    results = data.get("results")
    if isinstance(results, list):
        items.extend(item for item in results if isinstance(item, dict))
    if not items and not isinstance(results, list) and not isinstance(web_pages, dict):
        raise ValueError("malformed Bocha web search response.")

    parsed: list[WebSearchResult] = []
    for item in items:
        title = item.get("title") or item.get("name")
        url = item.get("url")
        if not title or not url:
            continue
        parsed.append(
            WebSearchResult(
                rank=len(parsed) + 1,
                title=str(title),
                url=str(url),
                site_name=str(item.get("siteName") or ""),
                snippet=str(item.get("snippet") or ""),
                summary=str(item.get("summary") or ""),
                published_at=str(item["datePublished"]) if item.get("datePublished") else None,
            )
        )
    return parsed


def _parse_rerank_results(payload: Any, source_results: list[WebSearchResult]) -> list[SearchRerankResult]:
    data = payload.get("data") if isinstance(payload, dict) else None
    results = data.get("results") if isinstance(data, dict) else None
    if not isinstance(results, list):
        raise ValueError("malformed Bocha rerank response.")

    parsed: list[SearchRerankResult] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        if not isinstance(index, int) or index < 0 or index >= len(source_results):
            continue
        score = item.get("relevance_score", item.get("score", item.get("bocha@rerankScore", 0)))
        source = source_results[index]
        parsed.append(
            SearchRerankResult(
                rank=len(parsed) + 1,
                source_index=index,
                score=float(score),
                title=source.title,
                url=source.url,
            )
        )
    return parsed

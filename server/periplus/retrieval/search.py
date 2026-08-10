"""The search seam.

DeepSeek has no browsing, so discovery is a separate dependency. It sits behind an
interface for the same reason the model does: search providers change pricing, quality and
availability faster than this pipeline should have to care about.

Tavily is the default because its free tier needs no card and its results come back as
verbatim chunks rather than a rendered SERP — which matters here, since a chunk pulled
straight from the source can stand in as evidence when the page itself refuses to be
fetched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from periplus.retrieval.extract import parse_date

TAVILY_ENDPOINT = "https://api.tavily.com/search"

#: Depths whose ``content`` is quoted from the page rather than summarised by a model.
#: Only these may be used as fallback evidence.
_VERBATIM_DEPTHS = frozenset({"basic", "advanced", "fast"})


@dataclass(frozen=True, slots=True)
class SearchResult:
    url: str
    title: str | None = None
    snippet: str = ""
    """Provider-supplied excerpt. Verbatim for most depths — see ``snippet_is_verbatim``."""
    score: float | None = None
    published_at: date | None = None
    raw_content: str | None = None
    snippet_is_verbatim: bool = False


@dataclass(frozen=True, slots=True)
class SearchQuery:
    text: str
    max_results: int = 6
    include_domains: Sequence[str] = field(default_factory=tuple)
    exclude_domains: Sequence[str] = field(default_factory=tuple)
    topic: str = "general"


class SearchError(RuntimeError):
    """Search failed in a way that will not fix itself."""


class TransientSearchError(SearchError):
    """Rate limited or provider-side failure; the same query may work shortly."""


class SearchProvider(ABC):
    @abstractmethod
    async def search(self, query: SearchQuery) -> list[SearchResult]:
        """Return results ranked by relevance. Never returns ``None``."""

    async def aclose(self) -> None:
        return None


class TavilySearch(SearchProvider):
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        search_depth: str = "basic",
        timeout_seconds: float = 30.0,
        include_raw_content: bool = False,
        endpoint: str = TAVILY_ENDPOINT,
    ) -> None:
        if not api_key:
            raise SearchError("no Tavily API key configured; set PERIPLUS_TAVILY_API_KEY")
        self.api_key = api_key
        self.search_depth = search_depth
        self.include_raw_content = include_raw_content
        self.endpoint = endpoint
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        payload: dict[str, Any] = {
            "query": query.text,
            "search_depth": self.search_depth,
            "max_results": query.max_results,
            "topic": query.topic,
            "include_raw_content": self.include_raw_content,
            "include_answer": False,
            "include_images": False,
        }
        if query.include_domains:
            payload["include_domains"] = list(query.include_domains)
        if query.exclude_domains:
            payload["exclude_domains"] = list(query.exclude_domains)

        try:
            response = await self._client.post(
                self.endpoint,
                json=payload,
                # The key travels in a header, never in the body or a query string, so it
                # cannot end up in a proxy log or a captured request body.
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise TransientSearchError(f"{type(exc).__name__}: {exc}") from exc

        self._raise_for_status(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise SearchError(f"unparseable Tavily response: {exc}") from exc

        return [self._to_result(item) for item in body.get("results", []) if item.get("url")]

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        detail = response.text[:200]
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientSearchError(f"HTTP {response.status_code}: {detail}")
        if response.status_code == 401:
            raise SearchError("Tavily rejected the API key")
        raise SearchError(f"HTTP {response.status_code}: {detail}")

    def _to_result(self, item: dict[str, Any]) -> SearchResult:
        return SearchResult(
            url=item["url"],
            title=item.get("title") or None,
            snippet=(item.get("content") or "").strip(),
            score=item.get("score"),
            published_at=_parse_iso_date(item.get("published_date")),
            raw_content=item.get("raw_content") or None,
            snippet_is_verbatim=self.search_depth in _VERBATIM_DEPTHS,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ScriptedSearch(SearchProvider):
    """Canned results for tests and offline development.

    Queries are matched by substring, longest pattern first, so a test can register both a
    broad default and a specific override without ordering games.
    """

    def __init__(self, responses: dict[str, Sequence[SearchResult]] | None = None) -> None:
        self.responses: dict[str, list[SearchResult]] = {
            pattern: list(results) for pattern, results in (responses or {}).items()
        }
        self.queries: list[SearchQuery] = []

    def register(self, pattern: str, results: Sequence[SearchResult]) -> None:
        self.responses[pattern] = list(results)

    async def search(self, query: SearchQuery) -> list[SearchResult]:
        self.queries.append(query)
        text = query.text.casefold()
        for pattern in sorted(self.responses, key=len, reverse=True):
            if pattern.casefold() in text:
                return self.responses[pattern][: query.max_results]
        return self.responses.get("", [])[: query.max_results]


def _parse_iso_date(value: Any) -> date | None:
    return parse_date(value) if isinstance(value, str) else None

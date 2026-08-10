"""Polite, bounded page fetching.

Three constraints shape this module, and none of them are about speed:

* **Do not get banned.** One request in flight per host, a delay between consecutive hits
  on the same host, robots.txt honoured. A crawler that hammers a museum's site loses
  access to the museum's own opening hours — the highest-quality source there is.
* **Do not read forever.** Responses stream with a byte ceiling, so one pathological page
  cannot exhaust memory or the run's wall clock.
* **Do not fetch twice.** The cache is consulted before the network, always.

Failures are values, not exceptions: a run that loses four of forty pages should carry on
and record what it lost, because gaps are information the research stage needs to report.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.robotparser import RobotFileParser

import httpx

from periplus.retrieval.cache import NullCache, PageCache
from periplus.retrieval.urls import host_of, normalise_url

DEFAULT_MAX_BYTES = 2_000_000
DEFAULT_PER_HOST_DELAY = 1.0
DEFAULT_MAX_CONCURRENCY = 6

_HTML_TYPES = ("text/html", "application/xhtml", "text/plain", "application/xml", "text/xml")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    requested_url: str
    url: str
    """Final URL after redirects, normalised."""
    html: str
    status: int
    content_type: str
    from_cache: bool = False
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class FetchFailure:
    url: str
    reason: str
    status: int | None = None

    def __str__(self) -> str:
        return f"{self.url}: {self.reason}"


FetchResult = FetchedPage | FetchFailure


class Fetcher:
    """Fetches HTML with a cache in front and a leash on."""

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        cache: PageCache | None = None,
        user_agent: str = "periplus/0.1",
        timeout_seconds: float = 30.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        per_host_delay: float = DEFAULT_PER_HOST_DELAY,
        max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
        obey_robots: bool = True,
    ) -> None:
        self.cache = cache or NullCache()
        self.user_agent = user_agent
        self.max_bytes = max_bytes
        self.per_host_delay = max(0.0, per_host_delay)
        self.obey_robots = obey_robots

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout_seconds,
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
                "Accept-Language": "en;q=0.9",
            },
        )
        self._gate = asyncio.Semaphore(max(1, max_concurrency))
        self._host_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def fetch(self, url: str) -> FetchResult:
        """Return a page, or a failure describing why there isn't one."""
        try:
            target = normalise_url(url)
        except ValueError as exc:
            return FetchFailure(url, f"unparseable url ({exc})")

        if not target.startswith(("http://", "https://")):
            return FetchFailure(url, "unsupported scheme")

        cached = self.cache.get(target)
        if cached is not None:
            return FetchedPage(
                requested_url=url,
                url=cached.url,
                html=cached.html,
                status=cached.status,
                content_type=cached.content_type,
                from_cache=True,
                fetched_at=cached.stored_at,
            )

        if self.obey_robots and not await self._allowed(target):
            return FetchFailure(url, "disallowed by robots.txt")

        async with self._gate:
            return await self._fetch_live(url, target)

    async def fetch_many(self, urls: list[str]) -> list[FetchResult]:
        """Fetch concurrently, preserving input order. Never raises for a single bad URL."""
        return list(await asyncio.gather(*(self.fetch(url) for url in urls)))

    async def pages(self, urls: list[str]) -> tuple[list[FetchedPage], list[FetchFailure]]:
        """:meth:`fetch_many`, split into what worked and what did not."""
        results = await self.fetch_many(urls)
        pages = [r for r in results if isinstance(r, FetchedPage)]
        failures = [r for r in results if isinstance(r, FetchFailure)]
        return pages, failures

    async def _fetch_live(self, requested: str, target: str) -> FetchResult:
        host = host_of(target)
        async with self._host_locks[host]:
            await self._respect_delay(host)
            try:
                page = await self._stream(requested, target)
            except httpx.HTTPError as exc:
                return FetchFailure(requested, f"{type(exc).__name__}: {exc}")
            finally:
                self._last_hit[host] = time.monotonic()

        if isinstance(page, FetchedPage):
            self.cache.put(page.url, page.html, status=page.status, content_type=page.content_type)
        return page

    async def _respect_delay(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is None or self.per_host_delay <= 0:
            return
        remaining = self.per_host_delay - (time.monotonic() - last)
        if remaining > 0:
            await asyncio.sleep(remaining)

    async def _stream(self, requested: str, target: str) -> FetchResult:
        async with self._client.stream("GET", target) as response:
            content_type = (response.headers.get("content-type") or "").split(";")[0].strip()

            if response.status_code >= 400:
                await response.aclose()
                return FetchFailure(
                    requested, f"HTTP {response.status_code}", status=response.status_code
                )
            if content_type and not content_type.startswith(_HTML_TYPES):
                await response.aclose()
                return FetchFailure(requested, f"unsupported content type {content_type}")

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total >= self.max_bytes:
                    break

            body = b"".join(chunks)[: self.max_bytes]
            encoding = response.charset_encoding or "utf-8"

        return FetchedPage(
            requested_url=requested,
            url=normalise_url(str(response.url)),
            html=body.decode(encoding, errors="replace"),
            status=response.status_code,
            content_type=content_type or "text/html",
        )

    async def _allowed(self, url: str) -> bool:
        host = host_of(url)
        async with self._robots_locks[host]:
            if host not in self._robots:
                self._robots[host] = await self._load_robots(url)
        parser = self._robots[host]
        # No robots.txt, or one we could not read, means no stated objection.
        return True if parser is None else parser.can_fetch(self.user_agent, url)

    async def _load_robots(self, url: str) -> RobotFileParser | None:
        scheme = "https" if url.startswith("https") else "http"
        robots_url = f"{scheme}://{host_of(url)}/robots.txt"
        try:
            response = await self._client.get(robots_url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or not response.text.strip():
            return None

        parser = RobotFileParser()
        parser.parse(response.text.splitlines())
        return parser

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def __aenter__(self) -> Fetcher:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

"""Search, fetch, clean — the whole of retrieval behind one call.

The research agent should ask for reading material about a place and receive clean text
with provenance attached. Where that text came from, whether it was cached, whether the
page was fetched or the search provider's own excerpt had to stand in — all of that is
this module's problem, not the agent's.

Two policies live here and are worth stating plainly:

* **A URL is read once per run**, no matter how many queries surface it. Duplicated
  reading material in a prompt is duplicated cost and, worse, makes a single source look
  like corroboration.
* **A failed fetch is not a lost source.** Search providers return verbatim chunks; when a
  page blocks us but the chunk is quoted rather than summarised, the chunk becomes a
  short document. Marked as such, so nothing pretends to be a full read.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from periplus.retrieval.document import SourceDocument, classify_source
from periplus.retrieval.extract import extract
from periplus.retrieval.fetch import FetchedPage, Fetcher, FetchFailure
from periplus.retrieval.search import SearchProvider, SearchQuery, SearchResult
from periplus.retrieval.urls import canonical_key, normalise_url

#: Sites that reliably cost a fetch and return a login wall or an app prompt.
DEFAULT_EXCLUDED_DOMAINS: tuple[str, ...] = (
    "facebook.com",
    "instagram.com",
    "x.com",
    "twitter.com",
    "tiktok.com",
    "pinterest.com",
    "linkedin.com",
)

#: Below this, an "extracted" page is a cookie notice or a JavaScript shell, not an article.
MIN_USEFUL_CHARS = 400


@dataclass(slots=True)
class RetrievalResult:
    documents: list[SourceDocument] = field(default_factory=list)
    queries_run: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def approx_tokens(self) -> int:
        return sum(doc.approx_tokens for doc in self.documents)

    def by_url(self) -> dict[str, SourceDocument]:
        return {doc.url: doc for doc in self.documents}


class Retriever:
    """Search and read, with per-run deduplication and a character budget."""

    def __init__(
        self,
        search: SearchProvider,
        fetcher: Fetcher,
        *,
        results_per_query: int = 6,
        max_chars_per_document: int = 24_000,
        min_useful_chars: int = MIN_USEFUL_CHARS,
        exclude_domains: tuple[str, ...] = DEFAULT_EXCLUDED_DOMAINS,
        allow_snippet_fallback: bool = True,
    ) -> None:
        self.search = search
        self.fetcher = fetcher
        self.results_per_query = results_per_query
        self.max_chars_per_document = max_chars_per_document
        self.min_useful_chars = min_useful_chars
        self.exclude_domains = exclude_domains
        self.allow_snippet_fallback = allow_snippet_fallback

    async def gather(
        self,
        queries: list[str],
        *,
        subject: str | None = None,
        seen_urls: set[str] | None = None,
    ) -> RetrievalResult:
        """Run each query, read what it finds, and return the readable documents.

        ``seen_urls`` carries deduplication across successive calls within one run; pass
        the same set back in and a page already read will not be read again.
        """
        result = RetrievalResult()
        seen = seen_urls if seen_urls is not None else set()

        for text in queries:
            request = SearchQuery(
                text=text,
                max_results=self.results_per_query,
                exclude_domains=self.exclude_domains,
            )
            try:
                hits = await self.search.search(request)
            except Exception as exc:  # noqa: BLE001 - a dead query must not kill the run
                result.failures.append(f"search {text!r}: {exc}")
                continue

            result.queries_run.append(text)
            fresh = [hit for hit in hits if _register(hit.url, seen)]
            if not fresh:
                continue

            documents = await self._read(
                fresh, query=text, subject=subject, failures=result.failures
            )
            result.documents.extend(documents)

        return result

    async def _read(
        self,
        hits: list[SearchResult],
        *,
        query: str,
        subject: str | None,
        failures: list[str],
    ) -> list[SourceDocument]:
        by_url = {normalise_url(hit.url): hit for hit in hits}
        outcomes = await self.fetcher.fetch_many([hit.url for hit in hits])

        documents: list[SourceDocument] = []
        for hit, outcome in zip(hits, outcomes, strict=True):
            if isinstance(outcome, FetchFailure):
                failures.append(str(outcome))
                fallback = self._from_snippet(hit, query=query, subject=subject)
                if fallback is not None:
                    documents.append(fallback)
                continue

            document = self._from_page(outcome, by_url, query=query, subject=subject)
            if document.char_count < self.min_useful_chars:
                failures.append(f"{document.url}: too little text after cleaning")
                fallback = self._from_snippet(hit, query=query, subject=subject)
                if fallback is not None and fallback.char_count > document.char_count:
                    documents.append(fallback)
                    continue
                if document.char_count == 0:
                    continue

            documents.append(document.truncated(self.max_chars_per_document))

        return documents

    async def aclose(self) -> None:
        """Release the search and fetch clients assembled behind this seam."""
        await self.search.aclose()
        await self.fetcher.aclose()

    def _from_page(
        self,
        page: FetchedPage,
        by_url: dict[str, SearchResult],
        *,
        query: str,
        subject: str | None,
    ) -> SourceDocument:
        hit = by_url.get(page.url) or by_url.get(normalise_url(page.requested_url))
        document = extract(
            page.html,
            page.url,
            subject=subject,
            query=query,
            fallback_title=hit.title if hit else None,
        )
        document.from_cache = page.from_cache
        document.fetched_at = page.fetched_at
        # A date the search provider states is better than none, but never overrides one
        # the page itself declares.
        if document.published_at is None and hit is not None:
            document.published_at = hit.published_at
        return document

    def _from_snippet(
        self, hit: SearchResult, *, query: str, subject: str | None
    ) -> SourceDocument | None:
        """Last resort: the provider's own verbatim chunk, clearly marked as partial."""
        if not self.allow_snippet_fallback or not hit.snippet_is_verbatim:
            return None
        text = (hit.raw_content or hit.snippet).strip()
        if not text:
            return None
        return SourceDocument(
            url=normalise_url(hit.url),
            text=text,
            title=hit.title,
            published_at=hit.published_at,
            source_kind=classify_source(hit.url, subject=subject),
            query=query,
        )


def _register(url: str, seen: set[str]) -> bool:
    """True the first time a URL is offered, False every time after."""
    try:
        key = canonical_key(url)
    except ValueError:
        return False
    if key in seen:
        return False
    seen.add(key)
    return True

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

A third, optional policy joins those two when an ``evidence_cache`` is configured (see
:mod:`periplus.retrieval.evidence_cache`): **a semantically near-identical source is read
once, full stop** — not once per run, but once ever. Before a search hit is handed to the
fetcher, its title and snippet are checked against the cache; a high-similarity match
means the stored source is reused and the network fetch never happens. A freshly fetched
page is then remembered the same way, so the next run — or the next query this run — that
surfaces an equivalent page skips the fetch too. This is genuinely optional: with no cache
configured, retrieval behaves exactly as it did before this existed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from periplus.retrieval.document import SourceDocument, classify_source
from periplus.retrieval.evidence_cache import SemanticEvidenceCache
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
    #: Every query handed to the search provider, whether it succeeded or raised. Always
    #: >= ``len(queries_run)``; the gap is exactly the queries that failed outright.
    queries_attempted: int = 0
    #: Every URL handed to the fetcher, whether it was fetched, blocked, too thin to use,
    #: or simply produced no evidence a claim later cited. This is the number that must
    #: be charged against a fetch budget — ``len(documents)`` undercounts it, and so does
    #: counting accepted evidence URLs downstream.
    fetch_attempts: int = 0
    #: Search hits satisfied from the semantic evidence cache instead of a live fetch.
    #: Always 0 with no ``evidence_cache`` configured. Included in ``fetch_attempts``
    #: (a reused hit was still a URL this run needed reading material for) but never
    #: handed to ``Fetcher`` — no network request and no DiskCache lookup happen for it.
    evidence_reused: int = 0

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
        evidence_cache: SemanticEvidenceCache | None = None,
    ) -> None:
        self.search = search
        self.fetcher = fetcher
        self.results_per_query = results_per_query
        self.max_chars_per_document = max_chars_per_document
        self.min_useful_chars = min_useful_chars
        self.exclude_domains = exclude_domains
        self.allow_snippet_fallback = allow_snippet_fallback
        #: Not owned by this instance — the same object typically outlives many
        #: ``Retriever``s (one per run) so it can dedupe across them. ``aclose`` below
        #: deliberately never closes it; whoever built it (see
        #: :func:`periplus.storage.build_evidence_cache`) also shuts it down.
        self.evidence_cache = evidence_cache

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
            result.queries_attempted += 1
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

            result.fetch_attempts += len(fresh)
            documents = await self._read(fresh, query=text, subject=subject, result=result)
            result.documents.extend(documents)

        return result

    async def _read(
        self,
        hits: list[SearchResult],
        *,
        query: str,
        subject: str | None,
        result: RetrievalResult,
    ) -> list[SourceDocument]:
        by_url = {normalise_url(hit.url): hit for hit in hits}

        resolved: dict[int, SourceDocument] = {}
        pending: list[tuple[int, SearchResult]] = []
        for index, hit in enumerate(hits):
            reused = await self._reuse(hit, query=query)
            if reused is not None:
                resolved[index] = reused
                result.evidence_reused += 1
            else:
                pending.append((index, hit))

        outcomes = await self.fetcher.fetch_many([hit.url for _, hit in pending])

        for (index, hit), outcome in zip(pending, outcomes, strict=True):
            if isinstance(outcome, FetchFailure):
                result.failures.append(str(outcome))
                fallback = self._from_snippet(hit, query=query, subject=subject)
                if fallback is not None:
                    resolved[index] = fallback
                continue

            document = self._from_page(outcome, by_url, query=query, subject=subject)
            if document.char_count < self.min_useful_chars:
                result.failures.append(f"{document.url}: too little text after cleaning")
                fallback = self._from_snippet(hit, query=query, subject=subject)
                if fallback is not None and fallback.char_count > document.char_count:
                    resolved[index] = fallback
                    continue
                if document.char_count == 0:
                    continue

            bounded = document.truncated(self.max_chars_per_document)
            resolved[index] = bounded
            # Only a genuinely fetched and read page is remembered — never a snippet
            # fallback. A search provider's excerpt standing in for a blocked page is a
            # worse source than the real page would be; caching it would mean a later
            # run that could fetch the real page successfully reuses the worse one
            # instead of trying again.
            if self.evidence_cache is not None:
                await self.evidence_cache.remember(bounded, query=query)

        return [resolved[index] for index in range(len(hits)) if index in resolved]

    async def _reuse(self, hit: SearchResult, *, query: str) -> SourceDocument | None:
        """Consult the evidence cache for ``hit`` before it is ever handed to the
        fetcher. Matched on the search provider's own title and snippet — the only
        description of the page available pre-fetch — against whatever full documents
        the cache already holds."""
        if self.evidence_cache is None:
            return None
        representative = f"{hit.title or ''}\n{hit.snippet}".strip()
        if not representative:
            return None
        cached = await self.evidence_cache.find_similar(representative)
        if cached is None:
            return None
        stored = cached.document
        # The cached document keeps its own provenance (url, title, source_kind,
        # original fetched_at) but takes this call's query — that is what actually
        # surfaced it this time, and is what Evidence.query should record.
        return SourceDocument(
            url=stored.url,
            text=stored.text,
            title=stored.title,
            published_at=stored.published_at,
            source_kind=stored.source_kind,
            language=stored.language,
            query=query,
            fetched_at=stored.fetched_at,
            from_cache=True,
        )

    async def aclose(self) -> None:
        """Release the search and fetch clients assembled behind this seam.

        Deliberately does not close ``evidence_cache`` — see the note on the field. A
        shared, cross-run cache is opened and closed by whoever assembled it, not by
        each disposable ``Retriever`` built for a single run.
        """
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

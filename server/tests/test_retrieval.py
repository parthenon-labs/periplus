"""Tests for the retrieval layer.

Entirely offline. Fetching runs against an in-process httpx transport, so the politeness
rules, the byte ceiling and the cache are exercised without a single real request.
"""

from __future__ import annotations

from datetime import date, timedelta

import httpx
import pytest

from periplus.embeddings import ScriptedEmbeddings
from periplus.models import SourceKind
from periplus.retrieval.cache import DiskCache, NullCache
from periplus.retrieval.document import SnippetNotFound, SourceDocument, classify_source
from periplus.retrieval.evidence_cache import InMemoryEvidenceCache
from periplus.retrieval.extract import extract, parse_date
from periplus.retrieval.fetch import FetchedPage, Fetcher, FetchFailure
from periplus.retrieval.retriever import Retriever
from periplus.retrieval.search import (
    ScriptedSearch,
    SearchError,
    SearchQuery,
    SearchResult,
    TavilySearch,
    TransientSearchError,
)
from periplus.retrieval.urls import (
    host_of,
    normalise_url,
    registrable_domain,
    same_site,
    url_key,
)

PAGE = """
<html lang="es-ES">
  <head>
    <title>Museo del Prado — Visitor information</title>
    <meta property="og:title" content="Museo del Prado: hours and tickets">
    <meta property="article:published_time" content="2026-03-04T09:00:00Z">
  </head>
  <body>
    <nav class="site-nav"><a href="/">Home</a><a href="/shop">Shop</a></nav>
    <div class="cookie-banner">We use cookies. Accept all?</div>
    <article class="entry-content">
      <h1>Visitor information</h1>
      <p>The museum opens Monday to Saturday from 10:00 to 20:00.</p>
      <p>General admission is 15 euros. Entry is free from 18:00 on weekdays.</p>
      <ul><li>Closed on 1 January and 25 December.</li></ul>
    </article>
    <aside class="related-articles"><a href="/x">Ten more museums you must see</a></aside>
    <footer class="site-footer">Contact us. Newsletter signup.</footer>
    <script>var tracking = 1;</script>
  </body>
</html>
"""


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def html_response(request: httpx.Request, body: str = PAGE, status: int = 200) -> httpx.Response:
    if request.url.path == "/robots.txt":
        return httpx.Response(404)
    return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})


class TestURLs:
    def test_drops_fragment_tracking_and_www(self):
        assert (
            normalise_url("HTTPS://WWW.Example.com/Guide?utm_source=x&b=2&a=1#top")
            == "https://example.com/Guide?a=1&b=2"
        )

    def test_default_port_and_trailing_slash_collapse(self):
        assert normalise_url("https://example.com:443/path/") == "https://example.com/path"

    def test_root_path_survives(self):
        assert normalise_url("https://example.com") == "https://example.com/"

    def test_non_tracking_query_is_kept(self):
        assert normalise_url("https://a.com/x?id=7") == "https://a.com/x?id=7"

    def test_variants_share_a_key(self):
        assert url_key("https://www.a.com/x?b=1&a=2") == url_key("http://a.com/x?a=2&b=1&fbclid=z")

    def test_registrable_domain_handles_compound_suffixes(self):
        assert registrable_domain("https://www.bbc.co.uk/travel") == "bbc.co.uk"
        assert registrable_domain("https://sub.museum.example.org/x") == "example.org"
        assert host_of("https://WWW.Example.com/a") == "example.com"

    def test_same_site_ignores_subdomains(self):
        assert same_site("https://en.wikipedia.org/a", "https://es.wikipedia.org/b")


class TestExtract:
    def test_keeps_article_text_and_drops_furniture(self):
        doc = extract(PAGE, "https://museodelprado.es/visit")
        assert "10:00 to 20:00" in doc.text
        assert "15 euros" in doc.text
        assert "Closed on 1 January" in doc.text
        assert "cookies" not in doc.text.lower()
        assert "Newsletter" not in doc.text
        assert "Ten more museums" not in doc.text
        assert "tracking" not in doc.text

    def test_reads_declared_metadata(self):
        doc = extract(PAGE, "https://museodelprado.es/visit")
        assert doc.title == "Museo del Prado: hours and tickets"
        assert doc.published_at == date(2026, 3, 4)
        assert doc.language == "es"

    def test_official_site_recognised_from_subject(self):
        doc = extract(PAGE, "https://museodelprado.es/visit", subject="Museo del Prado")
        assert doc.source_kind is SourceKind.OFFICIAL

    def test_page_without_main_region_still_yields_text(self):
        html = "<html><body><div><p>Tram 28 runs from Martim Moniz.</p></div></body></html>"
        assert "Martim Moniz" in extract(html, "https://x.com/a").text

    def test_repeated_block_text_is_not_duplicated(self):
        html = "<html><body><main><li><p>One line.</p></li></main></body></html>"
        assert extract(html, "https://x.com/a").text.count("One line.") == 1

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-03-04T09:00:00Z", date(2026, 3, 4)),
            ("2026-03-04", date(2026, 3, 4)),
            ("4 March 2026", date(2026, 3, 4)),
            ("March 4, 2026", date(2026, 3, 4)),
            ("last Tuesday", None),
            (None, None),
        ],
    )
    def test_date_parsing(self, raw, expected):
        assert parse_date(raw) == expected

    def test_json_ld_date_is_a_fallback(self):
        html = """<html><head><script type="application/ld+json">
        {"@type": "Article", "datePublished": "2025-11-02"}</script></head>
        <body><p>Body text here.</p></body></html>"""
        assert extract(html, "https://x.com/a").published_at == date(2025, 11, 2)


class TestClassification:
    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://en.wikipedia.org/wiki/Prado", SourceKind.ENCYCLOPEDIA),
            ("https://www.tripadvisor.com/x", SourceKind.REVIEW),
            ("https://www.lonelyplanet.com/spain", SourceKind.GUIDE),
            ("https://www.nps.gov/yose", SourceKind.GOVERNMENT),
            ("https://www.gov.uk/travel", SourceKind.GOVERNMENT),
            ("https://www.openstreetmap.org/x", SourceKind.MAP),
            ("https://some-blog.example/post", SourceKind.UNKNOWN),
        ],
    )
    def test_known_hosts(self, url, kind):
        assert classify_source(url) is kind

    def test_subject_match_marks_official(self):
        assert classify_source("https://museodelprado.es/x", subject="Museo del Prado") is (
            SourceKind.OFFICIAL
        )

    def test_short_subject_does_not_match_by_accident(self):
        assert classify_source("https://esa.int/x", subject="ESA") is SourceKind.UNKNOWN

    def test_a_shorter_domain_stem_still_counts_as_official(self):
        assert classify_source("https://prado.es/x", subject="Museo del Prado") is (
            SourceKind.OFFICIAL
        )

    def test_reseller_domain_is_not_official(self):
        # Found live: museodelprado-tickets.org is a ticket agency, and its prices must not
        # inherit the museum's authority.
        for url in (
            "https://museodelprado-tickets.org/en",
            "https://tickets-museodelprado.com/en",
            "https://museodelpradotours.com/en",
        ):
            assert classify_source(url, subject="Museo del Prado") is SourceKind.UNKNOWN


class TestDocument:
    def make(
        self, text: str = "The museum opens at 10:00.\nAdmission is 15 euros."
    ) -> SourceDocument:
        return SourceDocument(url="https://a.com/x", text=text, title="A")

    def test_evidence_requires_a_real_quote(self):
        doc = self.make()
        evidence = doc.evidence_for("Admission is 15 euros.", query="prado tickets")
        assert evidence.snippet == "Admission is 15 euros."
        assert evidence.query == "prado tickets"
        assert str(evidence.url) == "https://a.com/x"

    def test_quote_matching_ignores_whitespace_and_case(self):
        assert self.make().contains("admission   is 15\n euros.")

    def test_invented_quote_is_rejected(self):
        with pytest.raises(SnippetNotFound):
            self.make().evidence_for("Admission is 12 euros.")

    def test_empty_quote_is_rejected(self):
        with pytest.raises(SnippetNotFound):
            self.make().evidence_for("   ")

    def test_truncation_prefers_a_line_break(self):
        doc = self.make("a paragraph that runs past the halfway mark\n" + "x" * 100)
        clipped = doc.truncated(60)
        assert clipped.text == "a paragraph that runs past the halfway mark"
        assert clipped.url == doc.url

    def test_truncation_takes_a_hard_cut_rather_than_lose_half_the_budget(self):
        doc = self.make("tiny\n" + "x" * 100)
        assert len(doc.truncated(60).text) == 60

    def test_truncation_is_a_no_op_when_under_budget(self):
        doc = self.make()
        assert doc.truncated(10_000) is doc


class TestCache:
    def test_round_trip(self, tmp_path):
        cache = DiskCache(tmp_path)
        cache.put("https://a.com/x", "<p>hi</p>", status=200, content_type="text/html")
        page = cache.get("https://www.a.com/x?utm_source=y")
        assert page is not None
        assert page.html == "<p>hi</p>"
        assert cache.hits == 1

    def test_miss_on_unknown_url(self, tmp_path):
        assert DiskCache(tmp_path).get("https://a.com/nope") is None

    def test_expired_entry_is_a_miss(self, tmp_path):
        cache = DiskCache(tmp_path, ttl=timedelta(seconds=0))
        cache.put("https://a.com/x", "<p>hi</p>", status=200, content_type="text/html")
        assert cache.get("https://a.com/x") is None

    def test_null_cache_never_stores(self):
        cache = NullCache()
        cache.put("https://a.com/x", "<p>hi</p>", status=200, content_type="text/html")
        assert cache.get("https://a.com/x") is None


class TestFetcher:
    async def test_fetches_and_normalises(self):
        async with Fetcher(client=transport(html_response), per_host_delay=0) as fetcher:
            page = await fetcher.fetch("https://www.example.com/visit/?utm_source=x")
        assert isinstance(page, FetchedPage)
        assert page.url == "https://example.com/visit"
        assert "Museo del Prado" in page.html
        assert page.from_cache is False

    async def test_http_error_is_a_value_not_an_exception(self):
        def handler(request):
            return html_response(request, status=404)

        async with Fetcher(client=transport(handler), per_host_delay=0) as fetcher:
            result = await fetcher.fetch("https://example.com/gone")
        assert isinstance(result, FetchFailure)
        assert result.status == 404

    async def test_non_html_is_refused(self):
        def handler(request):
            return httpx.Response(
                200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
            )

        async with Fetcher(client=transport(handler), per_host_delay=0) as fetcher:
            result = await fetcher.fetch("https://example.com/a.pdf")
        assert isinstance(result, FetchFailure)
        assert "content type" in result.reason

    async def test_oversized_body_is_clipped_not_fatal(self):
        def handler(request):
            return html_response(request, body="<p>" + "x" * 50_000 + "</p>")

        async with Fetcher(client=transport(handler), per_host_delay=0, max_bytes=1_000) as fetcher:
            page = await fetcher.fetch("https://example.com/big")
        assert isinstance(page, FetchedPage)
        assert len(page.html) <= 1_000

    async def test_second_request_is_served_from_cache(self, tmp_path):
        calls: list[str] = []

        def handler(request):
            calls.append(str(request.url))
            return html_response(request)

        cache = DiskCache(tmp_path)
        async with Fetcher(client=transport(handler), cache=cache, per_host_delay=0) as fetcher:
            first = await fetcher.fetch("https://example.com/visit")
            second = await fetcher.fetch("https://www.example.com/visit?utm_campaign=z")

        assert isinstance(first, FetchedPage) and isinstance(second, FetchedPage)
        assert first.from_cache is False
        assert second.from_cache is True
        assert [url for url in calls if "robots" not in url] == ["https://example.com/visit"]

    async def test_robots_disallow_is_honoured(self):
        def handler(request):
            if request.url.path == "/robots.txt":
                return httpx.Response(200, text="User-agent: *\nDisallow: /private")
            return html_response(request)

        async with Fetcher(client=transport(handler), per_host_delay=0) as fetcher:
            blocked = await fetcher.fetch("https://example.com/private/page")
            allowed = await fetcher.fetch("https://example.com/public/page")

        assert isinstance(blocked, FetchFailure)
        assert "robots" in blocked.reason
        assert isinstance(allowed, FetchedPage)

    async def test_robots_is_fetched_once_per_host(self):
        robots_calls = []

        def handler(request):
            if request.url.path == "/robots.txt":
                robots_calls.append(str(request.url))
                return httpx.Response(200, text="User-agent: *\nAllow: /")
            return html_response(request)

        async with Fetcher(client=transport(handler), per_host_delay=0) as fetcher:
            await fetcher.fetch_many(
                ["https://example.com/a", "https://example.com/b", "https://example.com/c"]
            )
        assert len(robots_calls) == 1

    async def test_unsupported_scheme_is_refused(self):
        async with Fetcher(client=transport(html_response), per_host_delay=0) as fetcher:
            result = await fetcher.fetch("ftp://example.com/a")
        assert isinstance(result, FetchFailure)

    async def test_pages_splits_successes_from_failures(self):
        def handler(request):
            if request.url.path == "/gone":
                return html_response(request, status=500)
            return html_response(request)

        async with Fetcher(client=transport(handler), per_host_delay=0) as fetcher:
            pages, failures = await fetcher.pages(
                ["https://example.com/a", "https://example.com/gone"]
            )
        assert len(pages) == 1
        assert len(failures) == 1


class TestTavily:
    def tavily(self, handler, **kwargs):
        return TavilySearch("tvly-test", client=transport(handler), **kwargs)

    async def test_maps_the_response(self):
        seen: dict = {}

        def handler(request):
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = request.read().decode()
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Prado hours",
                            "url": "https://museodelprado.es/visit",
                            "content": "The museum opens Monday to Saturday.",
                            "score": 0.91,
                        },
                        {"content": "no url here"},
                    ]
                },
            )

        results = await self.tavily(handler).search(SearchQuery("prado opening hours"))
        assert len(results) == 1
        assert results[0].url == "https://museodelprado.es/visit"
        assert results[0].snippet_is_verbatim is True
        assert seen["auth"] == "Bearer tvly-test"
        assert "tvly-test" not in seen["body"]

    async def test_ultra_fast_snippets_are_not_treated_as_quotes(self):
        def handler(request):
            return httpx.Response(200, json={"results": [{"url": "https://a.com", "content": "x"}]})

        results = await self.tavily(handler, search_depth="ultra-fast").search(SearchQuery("q"))
        assert results[0].snippet_is_verbatim is False

    async def test_rate_limit_is_transient(self):
        def handler(request):
            return httpx.Response(429, text="slow down")

        with pytest.raises(TransientSearchError):
            await self.tavily(handler).search(SearchQuery("q"))

    async def test_bad_key_is_permanent(self):
        def handler(request):
            return httpx.Response(401, text="nope")

        with pytest.raises(SearchError) as caught:
            await self.tavily(handler).search(SearchQuery("q"))
        assert not isinstance(caught.value, TransientSearchError)

    def test_missing_key_fails_loudly(self):
        with pytest.raises(SearchError):
            TavilySearch("")


class TestRetriever:
    def hits(self, *urls: str, snippet: str = "The museum opens Monday to Saturday.") -> list:
        return [
            SearchResult(url=url, title="A page", snippet=snippet, snippet_is_verbatim=True)
            for url in urls
        ]

    def build(self, search, handler, **kwargs) -> Retriever:
        fetcher = Fetcher(client=transport(handler), per_host_delay=0)
        return Retriever(search, fetcher, min_useful_chars=10, **kwargs)

    async def test_gathers_documents_with_provenance(self):
        search = ScriptedSearch({"prado": self.hits("https://museodelprado.es/visit")})
        retriever = self.build(search, html_response)

        result = await retriever.gather(["prado opening hours"], subject="Museo del Prado")

        assert result.queries_run == ["prado opening hours"]
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert doc.source_kind is SourceKind.OFFICIAL
        assert doc.query == "prado opening hours"
        assert "15 euros" in doc.text
        assert result.approx_tokens > 0

    async def test_a_url_is_read_once_across_queries(self):
        search = ScriptedSearch({"": self.hits("https://a.com/x")})
        fetched: list[str] = []

        def handler(request):
            if request.url.path != "/robots.txt":
                fetched.append(str(request.url))
            return html_response(request)

        retriever = self.build(search, handler)
        result = await retriever.gather(["query one", "query two"])

        assert len(result.documents) == 1
        assert len(fetched) == 1
        assert result.queries_run == ["query one", "query two"]
        assert result.queries_attempted == 2
        # the URL was only ever offered to the fetcher once, on the first query
        assert result.fetch_attempts == 1

    async def test_failed_fetch_falls_back_to_the_verbatim_snippet(self):
        search = ScriptedSearch({"": self.hits("https://a.com/x")})

        def handler(request):
            return html_response(request, status=403)

        result = await self.build(search, handler).gather(["q"])

        assert len(result.documents) == 1
        assert result.documents[0].text == "The museum opens Monday to Saturday."
        assert any("403" in failure for failure in result.failures)
        # the fetch was attempted and blocked; the snippet fallback must not hide that
        assert result.fetch_attempts == 1

    async def test_no_fallback_when_the_snippet_is_a_summary(self):
        search = ScriptedSearch(
            {
                "": [
                    SearchResult(
                        url="https://a.com/x", snippet="summary", snippet_is_verbatim=False
                    )
                ]
            }
        )

        def handler(request):
            return html_response(request, status=403)

        result = await self.build(search, handler).gather(["q"])
        assert result.documents == []
        assert result.failures
        # no document and no fallback still means a fetch was attempted and must be
        # charged; the caller cannot infer this from an empty ``documents`` list
        assert result.fetch_attempts == 1

    async def test_search_failure_does_not_kill_the_run(self):
        class Broken(ScriptedSearch):
            async def search(self, query):
                if "bad" in query.text:
                    raise TransientSearchError("rate limited")
                return await super().search(query)

        search = Broken({"good": self.hits("https://a.com/x")})
        result = await self.build(search, html_response).gather(["bad query", "good query"])

        assert result.queries_run == ["good query"]
        assert len(result.documents) == 1
        assert any("rate limited" in failure for failure in result.failures)
        # both queries were attempted even though one raised before returning any hits
        assert result.queries_attempted == 2

    async def test_documents_are_clipped_to_the_budget(self):
        search = ScriptedSearch({"": self.hits("https://a.com/x")})
        retriever = self.build(search, html_response, max_chars_per_document=50)
        result = await retriever.gather(["q"])
        assert result.documents[0].char_count <= 50

    async def test_thin_page_is_reported_as_a_failure(self):
        search = ScriptedSearch({"": self.hits("https://a.com/x", snippet="")})

        def handler(request):
            return html_response(request, body="<html><body><p>hi</p></body></html>")

        fetcher = Fetcher(client=transport(handler), per_host_delay=0)
        result = await Retriever(search, fetcher, min_useful_chars=400).gather(["q"])
        assert any("too little text" in failure for failure in result.failures)
        # flagged as too thin to trust, but still a fetch attempt that must consume a
        # fetch budget regardless of what the extractor did with the result
        assert result.fetch_attempts == 1


class TestRetrieverEvidenceCache:
    """Entirely offline: :class:`InMemoryEvidenceCache` paired with
    :class:`ScriptedEmbeddings` exercises the real embed-then-compare similarity logic
    with no network and no real model call — the same guarantee every other seam in
    this module gets from ``ScriptedSearch``/``ScriptedDistance``.
    """

    def cache(self, *, threshold: float = 0.90) -> InMemoryEvidenceCache:
        return InMemoryEvidenceCache(ScriptedEmbeddings(), similarity_threshold=threshold)

    def build(self, search, handler, cache, **kwargs) -> Retriever:
        fetcher = Fetcher(client=transport(handler), per_host_delay=0)
        return Retriever(search, fetcher, min_useful_chars=10, evidence_cache=cache, **kwargs)

    async def test_reuses_a_cached_source_instead_of_fetching(self):
        cache = self.cache()
        stored = SourceDocument(
            url="https://museodelprado.es/hours",
            text="The museum opens Monday to Saturday from ten to eight.",
            title="Prado museum hours",
            source_kind=SourceKind.OFFICIAL,
        )
        await cache.remember(stored, query="prior query")

        search = ScriptedSearch(
            {
                "": [
                    SearchResult(
                        url="https://mirror.example.com/prado-hours",
                        title="Prado museum hours",
                        snippet="The museum opens Monday to Saturday from ten to eight.",
                        snippet_is_verbatim=True,
                    )
                ]
            }
        )
        fetched: list[str] = []

        def handler(request):
            if request.url.path != "/robots.txt":
                fetched.append(str(request.url))
            return html_response(request)

        retriever = self.build(search, handler, cache)
        result = await retriever.gather(["opening hours"])

        assert fetched == []  # the network was never touched
        assert result.evidence_reused == 1
        assert len(result.documents) == 1
        doc = result.documents[0]
        assert doc.text == stored.text
        assert doc.url == stored.url  # provenance follows the stored source, not the hit
        assert doc.query == "opening hours"  # but the query reflects this call
        assert doc.from_cache is True

    async def test_a_cache_miss_falls_through_to_a_normal_fetch(self):
        cache = self.cache()
        await cache.remember(
            SourceDocument(
                url="https://a.com/bananas",
                text="Bananas are a yellow tropical fruit rich in potassium.",
                title="About bananas",
            )
        )

        search = ScriptedSearch(
            {"": self.hits_with(title="Prado museum hours", snippet="Ticket prices and booking.")}
        )
        fetched: list[str] = []

        def handler(request):
            if request.url.path != "/robots.txt":
                fetched.append(str(request.url))
            return html_response(request)

        result = await self.build(search, handler, cache).gather(["q"])

        assert fetched != []  # nothing similar was cached, so the fetch still happened
        assert result.evidence_reused == 0
        assert len(result.documents) == 1

    async def test_a_freshly_fetched_document_is_remembered(self):
        cache = self.cache()
        search = ScriptedSearch({"": self.hits_with(title="A page", snippet="")})

        result = await self.build(search, html_response, cache).gather(["q"])
        assert len(result.documents) == 1

        # The exact text just fetched should now be an exact-match hit in the cache.
        doc = result.documents[0]
        remembered = await cache.find_similar(f"{doc.title}\n{doc.text}")
        assert remembered is not None
        assert remembered.document.url == result.documents[0].url

    async def test_a_snippet_fallback_is_not_remembered(self):
        cache = self.cache()
        search = ScriptedSearch(
            {
                "": [
                    SearchResult(
                        url="https://a.com/x",
                        title="A page",
                        snippet="The museum opens Monday to Saturday.",
                        snippet_is_verbatim=True,
                    )
                ]
            }
        )

        def handler(request):
            return html_response(request, status=403)

        result = await self.build(search, handler, cache).gather(["q"])
        assert len(result.documents) == 1  # the snippet fallback still produced a document

        remembered = await cache.find_similar("A page\nThe museum opens Monday to Saturday.")
        assert remembered is None

    def hits_with(self, *, title: str, snippet: str) -> list[SearchResult]:
        return [
            SearchResult(
                url="https://a.com/x", title=title, snippet=snippet, snippet_is_verbatim=True
            )
        ]

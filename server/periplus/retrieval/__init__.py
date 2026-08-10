"""Retrieval: finding pages, reading them, and keeping the provenance attached.

Agents import from here and nowhere deeper. ``build_retriever`` assembles the configured
stack — Tavily in front, a polite fetcher behind it, a disk cache in between — so nothing
downstream has to know which search provider is in use or where the cache lives.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from periplus.config import Settings, get_settings
from periplus.retrieval.cache import CachedPage, DiskCache, NullCache, PageCache
from periplus.retrieval.document import SnippetNotFound, SourceDocument, classify_source
from periplus.retrieval.extract import extract, parse_date
from periplus.retrieval.fetch import FetchedPage, Fetcher, FetchFailure, FetchResult
from periplus.retrieval.retriever import RetrievalResult, Retriever
from periplus.retrieval.search import (
    ScriptedSearch,
    SearchError,
    SearchProvider,
    SearchQuery,
    SearchResult,
    TavilySearch,
    TransientSearchError,
)
from periplus.retrieval.urls import (
    canonical_key,
    host_of,
    normalise_url,
    registrable_domain,
    url_key,
)

__all__ = [
    "CachedPage",
    "DiskCache",
    "FetchFailure",
    "FetchResult",
    "FetchedPage",
    "Fetcher",
    "NullCache",
    "PageCache",
    "RetrievalResult",
    "Retriever",
    "ScriptedSearch",
    "SearchError",
    "SearchProvider",
    "SearchQuery",
    "SearchResult",
    "SnippetNotFound",
    "SourceDocument",
    "TavilySearch",
    "TransientSearchError",
    "build_fetcher",
    "build_retriever",
    "build_search",
    "canonical_key",
    "classify_source",
    "extract",
    "host_of",
    "normalise_url",
    "parse_date",
    "registrable_domain",
    "url_key",
]


def build_cache(settings: Settings | None = None) -> PageCache:
    settings = settings or get_settings()
    if not settings.page_cache_enabled:
        return NullCache()
    ttl = timedelta(days=settings.page_cache_ttl_days) if settings.page_cache_ttl_days else None
    return DiskCache(Path(settings.page_cache_dir), ttl=ttl)


def build_fetcher(settings: Settings | None = None) -> Fetcher:
    settings = settings or get_settings()
    return Fetcher(
        cache=build_cache(settings),
        user_agent=settings.user_agent,
        timeout_seconds=settings.request_timeout_seconds,
        max_bytes=settings.max_page_bytes,
        per_host_delay=settings.per_host_delay_seconds,
        max_concurrency=settings.max_concurrent_fetches,
        obey_robots=settings.obey_robots,
    )


def build_search(settings: Settings | None = None) -> SearchProvider:
    settings = settings or get_settings()
    return TavilySearch(
        settings.tavily_api_key.get_secret_value(),
        search_depth=settings.search_depth,
        timeout_seconds=settings.request_timeout_seconds,
    )


def build_retriever(settings: Settings | None = None) -> Retriever:
    settings = settings or get_settings()
    return Retriever(
        build_search(settings),
        build_fetcher(settings),
        results_per_query=settings.results_per_query,
        max_chars_per_document=settings.max_chars_per_document,
    )

"""Run the retrieval layer from the command line.

    python -m periplus.probe "Museo del Prado opening hours" --subject "Museo del Prado"

Exists because the difference between "the tests pass" and "the pipeline can read the
open web" is one real request, and it should be cheap to make that request on purpose.
Costs one search credit; every page it reads lands in the cache, so a second run of the
same query is free and offline.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from periplus.config import get_settings
from periplus.retrieval import build_fetcher, build_search
from periplus.retrieval.retriever import Retriever


async def _run(queries: list[str], *, subject: str | None, preview: int) -> int:
    settings = get_settings()
    if not settings.has_search_key:
        print("no search key: set PERIPLUS_TAVILY_API_KEY in server/.env", file=sys.stderr)
        return 2

    search = build_search(settings)
    fetcher = build_fetcher(settings)
    retriever = Retriever(
        search,
        fetcher,
        results_per_query=settings.results_per_query,
        max_chars_per_document=settings.max_chars_per_document,
    )

    try:
        result = await retriever.gather(queries, subject=subject)
    finally:
        await search.aclose()
        await fetcher.aclose()

    for document in result.documents:
        cached = " (cached)" if document.from_cache else ""
        published = document.published_at.isoformat() if document.published_at else "no date"
        print(f"\n{'=' * 78}")
        print(f"{document.title or '(untitled)'}")
        print(f"{document.url}")
        print(
            f"{document.source_kind.value} · {published} · "
            f"{document.char_count} chars · ~{document.approx_tokens} tokens{cached}"
        )
        if preview:
            print("-" * 78)
            print(document.text[:preview])

    print(f"\n{'=' * 78}")
    print(f"queries run: {len(result.queries_run)}   documents: {len(result.documents)}")
    print(f"reading material: ~{result.approx_tokens} tokens")
    if result.failures:
        print(f"failures ({len(result.failures)}):")
        for failure in result.failures:
            print(f"  - {failure}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search, fetch and clean pages for a query.")
    parser.add_argument("queries", nargs="+", help="One or more search queries.")
    parser.add_argument("--subject", help="Venue name, used to recognise its official site.")
    parser.add_argument(
        "--preview", type=int, default=600, help="Characters of each document to print."
    )
    args = parser.parse_args(argv)
    return asyncio.run(_run(args.queries, subject=args.subject, preview=args.preview))


if __name__ == "__main__":
    raise SystemExit(main())

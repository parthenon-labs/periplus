"""pgvector-backed semantic evidence cache.

The other two Postgres classes in this package (:class:`~periplus.storage.postgres.
PostgresRunPersistence`, :class:`~periplus.storage.postgres_artifacts.
PostgresArtifactStore`) make a run's own state durable. This one is different in kind:
it is a cache shared *across* runs, and its job is to save a fetch, not to record one.

Two purposes, one lookup. ``docs/architecture.md``'s Storage section describes pgvector
as serving two things — deduplicating near-identical sources across queries, and letting
a claim reuse evidence a prior run already paid to fetch rather than spending a new
fetch on it. In this pipeline those collapse to the same call: :class:`Retriever
<periplus.retrieval.retriever.Retriever>` only ever knows "a search hit, not yet fetched"
before it reads a page, never "a claim, not yet evidenced" — claims are extracted *from*
fetched documents, not the other way around (see ``periplus.agents.research``). So the
one thing this cache does — before a URL is handed to the fetcher, check whether a
semantically similar document is already stored and reuse it — is what both purposes in
the architecture doc reduce to here. A near-identical page found by a second query is
the dedup case; a near-identical page fetched in an earlier run is the cross-run reuse
case. Same lookup, same code path.

Never load-bearing. Every method degrades to "nothing cached" — never a raised
exception — on an embedding failure or a database error, so a Postgres hiccup or a slow
model costs a redundant fetch, never a failed run. There is deliberately no logging
call here: the caller (:class:`~periplus.retrieval.retriever.Retriever`) is better
placed to decide whether a cache miss is worth surfacing, the same way it already
decides that for a failed search or a failed fetch.
"""

from __future__ import annotations

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg_pool import AsyncConnectionPool

from periplus.embeddings import EmbeddingProvider
from periplus.models import SourceKind
from periplus.retrieval.document import SourceDocument
from periplus.retrieval.evidence_cache import CachedSource
from periplus.retrieval.urls import canonical_key

_EXTENSION = "CREATE EXTENSION IF NOT EXISTS vector;"

#: Representative text is capped before it is embedded or stored. A whole page is not
#: needed to identify what a page is *about* — MiniLM itself only attends to roughly
#: the first 256 tokens — and storing the full text for every fetched page indefinitely
#: would make this cache the single largest table in the database for no similarity-
#: matching benefit.
MAX_REPRESENTATIVE_CHARS = 2_000
MAX_STORED_CHARS = 24_000


def _schema(dimensions: int) -> str:
    return f"""
    CREATE TABLE IF NOT EXISTS evidence_cache (
        canonical_key TEXT PRIMARY KEY,
        url TEXT NOT NULL,
        title TEXT,
        text TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        published_at DATE,
        fetched_at TIMESTAMPTZ NOT NULL,
        query TEXT,
        embedding vector({dimensions}) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    """


#: ivfflat needs training data to build useful lists; an empty or near-empty table (every
#: fresh database) just gets a single-list index, which degrades to a full scan until
#: enough rows accumulate. That is a correct, if slow, index — never a wrong answer.
_INDEX = """
CREATE INDEX IF NOT EXISTS evidence_cache_embedding_idx
    ON evidence_cache USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);
"""


class PostgresEvidenceCache:
    """Semantic reuse of already-fetched sources, keyed by embedding similarity.

    ``open``/``close`` bracket the pool's lifetime, same as
    :class:`~periplus.storage.postgres.PostgresRunPersistence`. One instance is shared
    for the process's lifetime — the cache's value comes entirely from being consulted
    across many runs, not from being rebuilt per run.
    """

    def __init__(
        self,
        database_url: str,
        embedder: EmbeddingProvider,
        *,
        similarity_threshold: float = 0.90,
    ) -> None:
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self._pool = AsyncConnectionPool(
            conninfo=database_url, open=False, configure=register_vector_async
        )

    async def open(self) -> None:
        # ``self._pool``'s ``configure`` callback registers pgvector's type adapter on
        # every connection the pool ever hands out — but that registration fails if the
        # extension does not exist yet, and ``pool.open()`` below runs ``configure`` on
        # its first connections immediately. So the extension is created here, first,
        # through a one-off connection the pool does not manage.
        async with await psycopg.AsyncConnection.connect(self._pool.conninfo) as conn:
            await conn.execute(_EXTENSION)

        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_schema(self.embedder.dimensions))
            await conn.execute(_INDEX)

    async def close(self) -> None:
        await self._pool.close()
        await self.embedder.aclose()

    async def find_similar(self, text: str) -> CachedSource | None:
        """Look up the closest stored source to ``text`` by cosine similarity.

        Returns ``None`` on a cache miss, a below-threshold match, an empty query, or
        any embedding/database failure — the caller cannot distinguish "nothing similar
        exists" from "the cache is unavailable right now", by design: both mean the same
        thing to a retriever deciding whether to fetch.
        """
        representative = text.strip()[:MAX_REPRESENTATIVE_CHARS]
        if not representative:
            return None
        try:
            [vector] = await self.embedder.embed([representative])
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    """
                    SELECT url, title, text, source_kind, published_at, fetched_at,
                           embedding <=> %s AS distance
                    FROM evidence_cache
                    ORDER BY embedding <=> %s
                    LIMIT 1
                    """,
                    (Vector(vector), Vector(vector)),
                )
                row = await cursor.fetchone()
        except Exception:  # noqa: BLE001 - a cache outage must degrade, never crash a run
            return None

        if row is None:
            return None
        url, title, doc_text, source_kind, published_at, fetched_at, distance = row
        similarity = 1.0 - distance
        if similarity < self.similarity_threshold:
            return None
        return CachedSource(
            document=SourceDocument(
                url=url,
                text=doc_text,
                title=title,
                published_at=published_at,
                source_kind=SourceKind(source_kind),
                fetched_at=fetched_at,
                from_cache=True,
            ),
            similarity=similarity,
        )

    async def remember(self, document: SourceDocument, *, query: str | None = None) -> None:
        """Store ``document`` for future semantic lookups. Best-effort: a failure here
        costs a future run a cache hit, never the current one anything at all."""
        representative = (f"{document.title or ''}\n{document.text}").strip()[
            :MAX_REPRESENTATIVE_CHARS
        ]
        if not representative:
            return
        try:
            key = canonical_key(document.url)
        except ValueError:
            return

        try:
            [vector] = await self.embedder.embed([representative])
            async with self._pool.connection() as conn:
                await conn.execute(
                    """
                    INSERT INTO evidence_cache
                        (canonical_key, url, title, text, source_kind, published_at,
                         fetched_at, query, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (canonical_key) DO UPDATE SET
                        url = EXCLUDED.url,
                        title = EXCLUDED.title,
                        text = EXCLUDED.text,
                        source_kind = EXCLUDED.source_kind,
                        published_at = EXCLUDED.published_at,
                        fetched_at = EXCLUDED.fetched_at,
                        query = EXCLUDED.query,
                        embedding = EXCLUDED.embedding
                    """,
                    (
                        key,
                        document.url,
                        document.title,
                        document.text[:MAX_STORED_CHARS],
                        document.source_kind.value,
                        document.published_at,
                        document.fetched_at,
                        query or document.query,
                        Vector(vector),
                    ),
                )
        except Exception:  # noqa: BLE001 - see find_similar: a write failure is not fatal
            return


__all__ = ["PostgresEvidenceCache"]

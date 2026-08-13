"""The semantic evidence cache seam :class:`~periplus.retrieval.retriever.Retriever`
consults before spending a fetch, and the in-memory implementation used to develop and
test against it without a database.

Defined here, in ``periplus.retrieval``, rather than in ``periplus.storage`` — the
package that holds the real, pgvector-backed implementation
(:class:`~periplus.storage.postgres_evidence.PostgresEvidenceCache`) — for the same
reason :class:`~periplus.retrieval.search.SearchProvider` and
:class:`~periplus.geo.distance.DistanceProvider` live next to the code that calls them
rather than next to whichever concrete backend answers first: ``periplus.retrieval``
must not import ``periplus.storage`` to type its own dependency, so the seam is defined
here and the real backend imports these types, not the other way around — the same
direction ``periplus.storage.postgres_artifacts`` already depends on
``periplus.orchestrator.artifacts``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from periplus.embeddings import EmbeddingProvider
from periplus.retrieval.document import SourceDocument
from periplus.retrieval.urls import canonical_key


@dataclass(slots=True)
class CachedSource:
    """A previously stored source, close enough in meaning to stand in for a fresh fetch."""

    document: SourceDocument
    similarity: float


class SemanticEvidenceCache(Protocol):
    async def find_similar(self, text: str) -> CachedSource | None:
        """The closest stored source to ``text`` above the cache's own similarity
        threshold, or ``None`` — for a miss, a below-threshold match, or an unavailable
        cache alike. See :class:`~periplus.storage.postgres_evidence.
        PostgresEvidenceCache` for why callers cannot and should not distinguish those."""

    async def remember(self, document: SourceDocument, *, query: str | None = None) -> None:
        """Store ``document`` for future lookups. Fire-and-forget in spirit: a caller
        does not need the result and an implementation should not need the caller to
        wait for a write that only benefits some later run."""


class NullEvidenceCache:
    """Every lookup misses, every store is a no-op. The explicit "off" cache, for a
    caller that wants to pass a seam rather than branch on ``None``."""

    async def find_similar(self, text: str) -> CachedSource | None:
        return None

    async def remember(self, document: SourceDocument, *, query: str | None = None) -> None:
        return None


class InMemoryEvidenceCache:
    """A real, working :class:`SemanticEvidenceCache` that needs no database — same
    embed-then-compare logic as :class:`~periplus.storage.postgres_evidence.
    PostgresEvidenceCache`, backed by a Python dict instead of a pgvector column.

    Two roles: a genuine option for local development without Postgres running, and the
    offline test double for :class:`~periplus.retrieval.retriever.Retriever`'s
    cache-consulting logic — paired with
    :class:`~periplus.embeddings.ScriptedEmbeddings`, it exercises the real
    similarity-threshold behaviour deterministically, with no network and no real model
    call, the same guarantee every other ``Scripted*`` seam in this codebase gives.

    Not durable: cleared when the process exits, so unlike the Postgres-backed cache it
    only ever dedupes within one process's lifetime, never across runs.

    Cosine similarity is computed as a plain dot product, which is only correct for unit
    vectors. Both shipped :class:`~periplus.embeddings.EmbeddingProvider` implementations
    return unit vectors (``SentenceTransformerEmbeddings`` via
    ``normalize_embeddings=True``, ``ScriptedEmbeddings`` by construction); a
    hand-written provider that does not normalise would need to divide by both vectors'
    norms here.
    """

    def __init__(self, embedder: EmbeddingProvider, *, similarity_threshold: float = 0.90) -> None:
        self.embedder = embedder
        self.similarity_threshold = similarity_threshold
        self._entries: dict[str, tuple[SourceDocument, list[float]]] = {}

    async def find_similar(self, text: str) -> CachedSource | None:
        representative = text.strip()
        if not representative or not self._entries:
            return None
        try:
            [vector] = await self.embedder.embed([representative])
        except Exception:  # noqa: BLE001 - an embedding failure is a cache miss, not a crash
            return None

        best: tuple[float, SourceDocument] | None = None
        for document, stored_vector in self._entries.values():
            similarity = _dot(vector, stored_vector)
            if best is None or similarity > best[0]:
                best = (similarity, document)
        if best is None or best[0] < self.similarity_threshold:
            return None
        similarity, document = best
        return CachedSource(document=document, similarity=similarity)

    async def remember(self, document: SourceDocument, *, query: str | None = None) -> None:
        representative = f"{document.title or ''}\n{document.text}".strip()
        if not representative:
            return
        try:
            key = canonical_key(document.url)
        except ValueError:
            return
        try:
            [vector] = await self.embedder.embed([representative])
        except Exception:  # noqa: BLE001 - see find_similar
            return
        self._entries[key] = (document, vector)

    async def aclose(self) -> None:
        await self.embedder.aclose()


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


__all__ = ["CachedSource", "InMemoryEvidenceCache", "NullEvidenceCache", "SemanticEvidenceCache"]

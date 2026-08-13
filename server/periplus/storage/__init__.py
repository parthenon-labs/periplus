"""Durable storage seams: finished-and-in-flight run records, plus the per-stage
artifacts a crashed run resumes from. The pipeline's other state (page cache,
model-call accounting) stays exactly where it already lived before this package
existed.

``PostgresEvidenceCache`` is a different kind of thing from the other two classes here —
a cache shared *across* runs, not one run's own durable state — but it is still
Postgres-backed storage, so it lives in this package; see its own module docstring.
``build_evidence_cache`` is its assembly point, the same role
:func:`periplus.geo.build_distance_provider` plays for its seam: it picks a concrete
backend from settings and returns ``None``, never raises, when the optional local
embedder this cache depends on cannot be built.
"""

from __future__ import annotations

from periplus.storage.postgres import PostgresRunPersistence
from periplus.storage.postgres_artifacts import PostgresArtifactStore
from periplus.storage.postgres_evidence import PostgresEvidenceCache

__all__ = [
    "PostgresArtifactStore",
    "PostgresEvidenceCache",
    "PostgresRunPersistence",
    "build_evidence_cache",
]


def build_evidence_cache(settings=None) -> PostgresEvidenceCache | None:
    """Build the pgvector evidence cache, or ``None`` if it cannot be built.

    Never raises. No local embedder (see :func:`periplus.embeddings.build_embedder`) —
    no ``sentence-transformers`` installed, the feature turned off, or the model
    otherwise unloadable — means no cache, full stop; a reachable Postgres alone is not
    enough to search by meaning. Callers still need to ``await cache.open()`` before use
    and ``await cache.close()`` at shutdown, same as the other two classes here.
    """
    from periplus.config import get_settings
    from periplus.embeddings import build_embedder

    settings = settings or get_settings()
    embedder = build_embedder(settings)
    if embedder is None:
        return None
    return PostgresEvidenceCache(
        settings.database_url,
        embedder,
        similarity_threshold=settings.evidence_cache_similarity_threshold,
    )

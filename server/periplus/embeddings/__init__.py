"""Embeddings: turning evidence text into a vector, for the pgvector cache.

Agents never import from here — only :mod:`periplus.storage.postgres_evidence` and
:mod:`periplus.retrieval` do, the same "import from the package root, not the module"
rule :mod:`periplus.geo` and :mod:`periplus.retrieval` already follow.

``build_embedder`` is the seam that makes the whole semantic cache optional: it never
raises. If ``sentence-transformers`` is not installed, or the model cannot be loaded, it
returns ``None`` and callers wire the cache off — the same pattern
:func:`periplus.agents.build_navigation_agent` uses for a missing geo API key.
"""

from __future__ import annotations

from periplus.config import Settings, get_settings
from periplus.embeddings.provider import EmbeddingError, EmbeddingProvider
from periplus.embeddings.scripted import ScriptedEmbeddings
from periplus.embeddings.sentence_transformer import DEFAULT_MODEL, SentenceTransformerEmbeddings

__all__ = [
    "DEFAULT_MODEL",
    "EmbeddingError",
    "EmbeddingProvider",
    "ScriptedEmbeddings",
    "SentenceTransformerEmbeddings",
    "build_embedder",
]


def build_embedder(settings: Settings | None = None) -> EmbeddingProvider | None:
    """Build the local embedder, or ``None`` if it cannot be built.

    Never raises: a missing optional dependency, an unloadable model, or the feature
    being turned off in settings are all just reasons to run without a semantic evidence
    cache, not reasons to fail the pipeline.
    """
    settings = settings or get_settings()
    if not settings.evidence_cache_enabled:
        return None
    try:
        return SentenceTransformerEmbeddings(settings.evidence_embedding_model)
    except EmbeddingError:
        return None

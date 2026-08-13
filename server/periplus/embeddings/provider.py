"""The embedding seam.

Storage's pgvector cache needs a vector for a chunk of text, not a preference about
which model produced it. This module keeps that vector behind an interface for the same
reason :mod:`periplus.geo.distance` keeps travel time behind one: the pipeline should
never have to change because the embedding backend did.

There is exactly one real implementation, and it is a hard constraint, not a default:
``sentence-transformers`` running a small local model, never a paid embedding API. See
:mod:`periplus.embeddings.sentence_transformer`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class EmbeddingError(RuntimeError):
    """The embedding backend is unavailable, misconfigured, or failed to embed."""


class EmbeddingProvider(ABC):
    #: Length of every vector this provider returns. Fixed per instance, since it is
    #: also the width of the pgvector column the cache creates.
    dimensions: int

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed each string in ``texts``, in order. Never called with an empty list by
        this package's own callers, but an empty list in is an empty list out, not an
        error."""

    async def aclose(self) -> None:
        return None


__all__ = ["EmbeddingError", "EmbeddingProvider"]

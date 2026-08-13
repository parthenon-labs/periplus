"""Local, free embeddings via ``sentence-transformers``.

Cost constraint, not a preference: this pipeline never sends evidence text to a paid
embedding API. ``all-MiniLM-L6-v2`` is small enough to run on CPU (≈80MB, 384
dimensions) and its license permits exactly this use. ``sentence-transformers`` is an
optional dependency — see :func:`periplus.embeddings.build_embedder` for what happens
when it is not installed.
"""

from __future__ import annotations

import asyncio

from periplus.embeddings.provider import EmbeddingError, EmbeddingProvider

#: Small, CPU-friendly, and the most widely used general-purpose sentence embedding
#: model — a reasonable default for evidence-passage similarity, not a claim that it is
#: the best available.
DEFAULT_MODEL = "all-MiniLM-L6-v2"


class SentenceTransformerEmbeddings(EmbeddingProvider):
    """Wraps a local ``SentenceTransformer`` model.

    The model loads at construction time (synchronously — there is no async model
    loading API), so building this provider is itself the "is this actually usable"
    check: an environment without the package, or without the model cached and no
    network to fetch it, raises :class:`EmbeddingError` right here rather than on the
    first real call.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError(
                "sentence-transformers is not installed; run `pip install "
                "sentence-transformers` or leave the evidence cache disabled"
            ) from exc

        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:  # noqa: BLE001 - any load failure means "no embedder"
            raise EmbeddingError(f"could not load embedding model {model_name!r}: {exc}") from exc

        self.model_name = model_name
        # Newer sentence-transformers renamed this method; older ones do not have the
        # new name yet. Support both rather than pin to one release's vocabulary.
        get_dimensions = getattr(
            self._model, "get_embedding_dimension", self._model.get_sentence_embedding_dimension
        )
        self.dimensions = get_dimensions()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        # Encoding is synchronous, CPU-bound work; running it inline would block the
        # event loop for every other run sharing this process, so it goes to a thread
        # the same way any other blocking call in this codebase would.
        vectors = await asyncio.to_thread(
            self._model.encode,
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [vector.tolist() for vector in vectors]


__all__ = ["DEFAULT_MODEL", "SentenceTransformerEmbeddings"]

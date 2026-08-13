"""A deterministic, dependency-free embedder for tests and offline development.

The repo's tests never make a real model call — see every other ``Scripted*`` seam in
:mod:`periplus.geo.distance` and :mod:`periplus.retrieval.search`. An embedder is no
different, except that a plain lookup table (as ``ScriptedSearch`` uses) cannot exercise
similarity *thresholds*: the whole point of the cache is "close but not identical" text
matching, and a test asserting that needs two different strings to actually land near
each other in vector space.

So this hashes words into fixed buckets (the "hashing trick": no vocabulary, no
training, no model weights) and L2-normalises the result. Two texts sharing most of
their words land close together under cosine similarity; texts sharing few or none land
far apart. That is a real, if crude, embedding — good enough to test dedup logic
end-to-end without ever importing ``sentence_transformers`` or touching the network.
"""

from __future__ import annotations

import hashlib
import math
import re

from periplus.embeddings.provider import EmbeddingProvider

_WORD = re.compile(r"[a-z0-9]+")

DEFAULT_DIMENSIONS = 384


class ScriptedEmbeddings(EmbeddingProvider):
    """Hashing-trick embeddings, plus an optional exact override table.

    ``register`` lets a test pin a specific text to a specific vector when it needs
    tighter control than the hash gives it (for example, to force two unrelated-looking
    strings to collide, or two similar-looking ones to stay apart). Anything not
    registered falls through to the hash.
    """

    def __init__(self, *, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.dimensions = dimensions
        self.overrides: dict[str, list[float]] = {}
        self.calls: list[list[str]] = []

    def register(self, text: str, vector: list[float]) -> None:
        if len(vector) != self.dimensions:
            raise ValueError(f"vector must have {self.dimensions} dimensions, got {len(vector)}")
        self.overrides[text] = list(vector)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.overrides.get(text) or _hash_embed(text, self.dimensions) for text in texts]


def _bucket(word: str, dimensions: int) -> int:
    """A stable hash across processes and runs. ``hash()`` is salted per-process by
    default (``PYTHONHASHSEED``), which would make two separate test runs — or the app
    and a test asserting against it — embed the same word into different buckets."""
    digest = hashlib.sha256(word.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % dimensions


def _hash_embed(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    words = _WORD.findall(text.casefold())
    if not words:
        # An all-zero vector cannot be cosine-normalised; a single stable dummy bucket
        # keeps it a valid (if maximally distant from everything) unit vector instead.
        vector[0] = 1.0
        return vector
    for word in words:
        vector[_bucket(word, dimensions)] += 1.0
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


__all__ = ["DEFAULT_DIMENSIONS", "ScriptedEmbeddings"]

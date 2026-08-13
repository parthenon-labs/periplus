"""Tests for the embedding seam.

Entirely offline, same principle as every other seam's tests in this repo:
``ScriptedEmbeddings`` is exercised directly for its real similarity behaviour, and
``SentenceTransformerEmbeddings`` is only ever exercised with its import forced to fail —
never with a real model load, which would be slow, network-dependent the first time, and
exactly the kind of real model call this test suite does not make.
"""

from __future__ import annotations

import math
import sys

import pytest

from periplus.config import Settings
from periplus.embeddings import build_embedder
from periplus.embeddings.provider import EmbeddingError
from periplus.embeddings.scripted import ScriptedEmbeddings
from periplus.embeddings.sentence_transformer import SentenceTransformerEmbeddings


def _norm(vector: list[float]) -> float:
    return math.sqrt(sum(component * component for component in vector))


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True)) / (_norm(a) * _norm(b))


class TestScriptedEmbeddings:
    async def test_deterministic_across_instances(self):
        first = await ScriptedEmbeddings().embed(["Prado museum opening hours"])
        second = await ScriptedEmbeddings().embed(["Prado museum opening hours"])
        assert first == second

    async def test_identical_text_has_similarity_one(self):
        embedder = ScriptedEmbeddings()
        [a, b] = await embedder.embed(["same text", "same text"])
        assert _cosine(a, b) == pytest.approx(1.0)

    async def test_near_duplicate_text_scores_higher_than_unrelated_text(self):
        embedder = ScriptedEmbeddings()
        [original, paraphrase, unrelated] = await embedder.embed(
            [
                "The museum opens Monday to Saturday from ten to eight",
                "The museum opens Monday to Saturday from ten to eight in the morning",
                "Bananas are a yellow tropical fruit rich in potassium",
            ]
        )
        assert _cosine(original, paraphrase) > _cosine(original, unrelated)

    async def test_returns_unit_vectors(self):
        [vector] = await ScriptedEmbeddings().embed(["anything"])
        assert _norm(vector) == pytest.approx(1.0)

    async def test_empty_string_is_still_a_valid_unit_vector(self):
        [vector] = await ScriptedEmbeddings().embed([""])
        assert _norm(vector) == pytest.approx(1.0)

    async def test_embed_empty_list_returns_empty_list(self):
        assert await ScriptedEmbeddings().embed([]) == []

    async def test_register_overrides_the_hash(self):
        embedder = ScriptedEmbeddings(dimensions=4)
        embedder.register("pinned", [1.0, 0.0, 0.0, 0.0])
        [vector] = await embedder.embed(["pinned"])
        assert vector == [1.0, 0.0, 0.0, 0.0]

    def test_register_rejects_the_wrong_dimension(self):
        embedder = ScriptedEmbeddings(dimensions=4)
        with pytest.raises(ValueError, match="4 dimensions"):
            embedder.register("pinned", [1.0, 0.0])

    async def test_records_every_call(self):
        embedder = ScriptedEmbeddings()
        await embedder.embed(["a", "b"])
        await embedder.embed(["c"])
        assert embedder.calls == [["a", "b"], ["c"]]


class TestSentenceTransformerEmbeddings:
    def test_missing_dependency_raises_embedding_error(self, monkeypatch):
        # Simulate an environment without sentence-transformers installed, without
        # actually uninstalling it — the import inside __init__ sees this and raises
        # ImportError exactly as it would with the package genuinely absent.
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        with pytest.raises(EmbeddingError, match="sentence-transformers is not installed"):
            SentenceTransformerEmbeddings()


class TestBuildEmbedder:
    def test_disabled_by_settings_returns_none(self):
        settings = Settings(evidence_cache_enabled=False)
        assert build_embedder(settings) is None

    def test_missing_dependency_returns_none_rather_than_raising(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
        settings = Settings(evidence_cache_enabled=True)
        assert build_embedder(settings) is None

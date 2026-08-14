"""Offline contract tests for Editor, the copy-editing agent."""

from __future__ import annotations

import json

from periplus.agents.editor import EditorAgent
from periplus.llm import LLMError, ScriptedClient, StagePolicy, Thinking
from periplus.models import Claim, ClaimKind, ContentPiece, ContentSet


def make_claim(**overrides) -> Claim:
    values = {
        "id": "claim-1",
        "subject": "Museo del Prado",
        "text": "The Prado opens at 10am and standard admission is €15.",
        "kind": ClaimKind.HOURS,
        "evidence_ids": ["evidence-1"],
    }
    values.update(overrides)
    return Claim(**values)


def make_piece(**overrides) -> ContentPiece:
    values = {
        "id": "piece-1",
        "kind": "article",
        "title": "A slow morning at the Prado",
        "body": "Madrid in September is a painter's dream, all golden light and quiet "
        "courtyards. The Prado opens at 10am; standard admission is €15.",
        "claim_ids": ["claim-1"],
    }
    values.update(overrides)
    return ContentPiece(**values)


def content_set(*, pieces: list[ContentPiece], claims: list[Claim]) -> ContentSet:
    return ContentSet(itinerary_id="itinerary-1", pieces=pieces, claims=claims)


def edit(**overrides) -> dict:
    payload = {
        "piece_id": "piece-1",
        "drop": False,
        "reason": None,
        "title": None,
        "body": "The Prado opens at 10am. Standard admission is €15.",
        "claim_ids": ["claim-1"],
    }
    payload.update(overrides)
    return payload


def reply(*edits: dict) -> str:
    return json.dumps({"pieces": list(edits)})


def agent(replies, **overrides) -> tuple[EditorAgent, ScriptedClient]:
    llm = ScriptedClient(replies, max_attempts=overrides.pop("llm_attempts", 1))
    editor = EditorAgent(
        llm=llm,
        policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        **overrides,
    )
    return editor, llm


class TestEditing:
    async def test_piece_is_tightened(self):
        editor, llm = agent([reply(edit())])
        piece = make_piece()

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        pieces = outcome.content.pieces
        assert len(pieces) == 1
        assert pieces[0].body == "The Prado opens at 10am. Standard admission is €15."
        assert pieces[0].claim_ids == ["claim-1"]
        assert pieces[0].word_count == len(pieces[0].body.split())
        assert pieces[0].id == piece.id
        assert outcome.content.edited is True
        assert llm.call_count == 1

    async def test_no_pieces_never_calls_the_model(self):
        editor, llm = agent([])
        outcome = await editor.edit(content_set(pieces=[], claims=[]))

        assert outcome.content.pieces == []
        assert "nothing was revised" in outcome.content.caveats[0]
        assert outcome.content.edited is False
        assert llm.call_count == 0

    async def test_a_change_that_leaves_the_piece_identical_is_not_flagged_as_edited(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(body=piece.body, title=piece.title))])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert outcome.content.edited is False


class TestGracefulDegrade:
    async def test_llm_failure_falls_back_to_the_original_pieces(self):
        piece = make_piece()
        editor, llm = agent([LLMError("boom")])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert outcome.content.edited is False
        assert any("Editing failed" in c for c in outcome.content.caveats)

    async def test_structured_output_failure_falls_back_to_the_original_pieces(self):
        piece = make_piece()
        editor, llm = agent(["not json", "still not json"], llm_attempts=2)

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert outcome.content.edited is False
        assert any("Editing failed" in c for c in outcome.content.caveats)

    async def test_empty_revision_body_keeps_the_original_piece(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(body="   "))])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert any("empty" in c for c in outcome.content.caveats)

    async def test_revision_over_the_length_limit_keeps_the_original_piece(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(body="x" * 100))], max_piece_chars=10)

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert any("exceeded the 10-character limit" in c for c in outcome.content.caveats)


class TestGroundingIsNeverInvented:
    async def test_a_claim_not_already_on_the_piece_is_dropped_not_trusted(self):
        piece = make_piece()
        unrelated_claim = make_claim(id="claim-2", subject="Retiro Park")
        editor, llm = agent([reply(edit(claim_ids=["claim-1", "claim-2"]))])

        outcome = await editor.edit(
            content_set(pieces=[piece], claims=[make_claim(), unrelated_claim])
        )

        assert outcome.content.pieces[0].claim_ids == ["claim-1"]
        assert any("cited a claim not already on that piece" in c for c in outcome.content.caveats)

    async def test_dropping_a_piece_removes_its_now_uncited_claims(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(drop=True, reason="pure atmosphere, no facts"))])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == []
        assert outcome.content.claims == []
        assert outcome.content.edited is True
        assert any(
            "Dropped a 'article' piece: pure atmosphere, no facts" in c
            for c in outcome.content.caveats
        )

    async def test_dropping_without_a_reason_still_logs_a_caveat(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(drop=True, reason=None))])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == []
        assert any("Dropped a 'article' piece:" in c for c in outcome.content.caveats)


class TestPartialCoverage:
    async def test_a_piece_the_model_never_mentions_is_left_untouched(self):
        edited_piece = make_piece(id="piece-1")
        untouched_piece = make_piece(
            id="piece-2", kind="checklist", body="Bring sunscreen.", claim_ids=[]
        )
        editor, llm = agent([reply(edit())])

        outcome = await editor.edit(
            content_set(pieces=[edited_piece, untouched_piece], claims=[make_claim()])
        )

        assert len(outcome.content.pieces) == 2
        assert outcome.content.pieces[1] == untouched_piece

    async def test_an_unknown_piece_id_is_ignored_with_a_caveat(self):
        piece = make_piece()
        editor, llm = agent([reply(edit(piece_id="ghost-piece"))])

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces == [piece]
        assert any("piece id that was not offered" in c for c in outcome.content.caveats)

    async def test_a_duplicate_revision_for_the_same_piece_keeps_the_first(self):
        piece = make_piece()
        editor, llm = agent(
            [reply(edit(body="First revision."), edit(body="Second revision."))]
        )

        outcome = await editor.edit(content_set(pieces=[piece], claims=[make_claim()]))

        assert outcome.content.pieces[0].body == "First revision."
        assert any("more than one revision" in c for c in outcome.content.caveats)


class TestBudgets:
    async def test_piece_limit_truncates_and_carries_the_rest_over_unedited(self):
        second_piece = make_piece(
            id="piece-2", kind="checklist", body="Bring sunscreen.", claim_ids=[]
        )
        editor, llm = agent([reply(edit())], max_pieces=1)

        outcome = await editor.edit(
            content_set(pieces=[make_piece(), second_piece], claims=[make_claim()])
        )

        assert len(outcome.content.pieces) == 2
        assert outcome.content.pieces[1] == second_piece
        assert any("Piece limit reached" in c for c in outcome.content.caveats)
        assert llm.call_count == 1

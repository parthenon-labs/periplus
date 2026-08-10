"""Offline contract tests for Chronicler, the writing agent."""

from __future__ import annotations

import json
from datetime import time

from periplus.agents.content import ContentAgent
from periplus.llm import LLMError, ScriptedClient, StagePolicy, Thinking
from periplus.models import (
    Claim,
    ClaimKind,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    Place,
    PlaceKind,
    TripBrief,
)


def brief(**overrides) -> TripBrief:
    from datetime import date

    values = {
        "destination": "Madrid",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 2),
    }
    values.update(overrides)
    return TripBrief(**values)


def make_claim(**overrides) -> Claim:
    values = {
        "id": "claim-1",
        "subject": "Museo del Prado",
        "text": "The Prado opens at 10am.",
        "kind": ClaimKind.HOURS,
        "evidence_ids": ["evidence-1"],
    }
    values.update(overrides)
    return Claim(**values)


def make_place(**overrides) -> Place:
    values = {
        "id": "place-1",
        "name": "Museo del Prado",
        "kind": PlaceKind.MUSEUM,
        "claim_ids": ["claim-1"],
    }
    values.update(overrides)
    return Place(**values)


def make_item(**overrides) -> ItineraryItem:
    values = {
        "day": 1,
        "start": time(10, 0),
        "title": "Visit the Prado",
        "place_id": "place-1",
        "claim_ids": ["claim-1"],
    }
    values.update(overrides)
    return ItineraryItem(**values)


def itinerary(
    *, items: list[ItineraryItem], places: list[Place], claims: list[Claim]
) -> Itinerary:
    from datetime import date

    return Itinerary(
        brief_id="brief-1",
        destination="Madrid",
        days=[ItineraryDay(day=1, date=date(2026, 9, 1), items=items)],
        places=places,
        claims=claims,
    )


def piece(
    *,
    kind: str = "article",
    title: str | None = "A day at the Prado",
    body: str = "The Prado opens at 10am, plenty of time for a slow morning.",
    claim_ids: list[str] | None = None,
    **overrides,
) -> dict:
    payload = {
        "kind": kind,
        "title": title,
        "body": body,
        "claim_ids": claim_ids if claim_ids is not None else ["claim-1"],
    }
    payload.update(overrides)
    return payload


def reply(*pieces: dict) -> str:
    return json.dumps({"pieces": list(pieces)})


def agent(replies, **overrides) -> tuple[ContentAgent, ScriptedClient]:
    llm = ScriptedClient(replies, max_attempts=overrides.pop("llm_attempts", 1))
    chronicler = ContentAgent(
        llm=llm,
        policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        **overrides,
    )
    return chronicler, llm


def default_itinerary() -> Itinerary:
    return itinerary(items=[make_item()], places=[make_place()], claims=[make_claim()])


class TestWriting:
    async def test_piece_is_written(self):
        chronicler, llm = agent([reply(piece())])
        trip = default_itinerary()
        outcome = await chronicler.write(brief(), trip)

        pieces = outcome.content.pieces
        assert len(pieces) == 1
        assert pieces[0].kind == "article"
        assert pieces[0].claim_ids == ["claim-1"]
        assert pieces[0].word_count == len(pieces[0].body.split())
        assert outcome.content.itinerary_id == trip.id
        assert llm.call_count == 1

    async def test_empty_itinerary_never_calls_the_model(self):
        chronicler, llm = agent([])
        empty = itinerary(items=[], places=[], claims=[])
        outcome = await chronicler.write(brief(), empty)

        assert outcome.content.pieces == []
        assert "nothing was written" in outcome.content.caveats[0]
        assert llm.call_count == 0

    async def test_structured_output_failure_is_a_caveat_not_an_exception(self):
        chronicler, llm = agent([LLMError("boom")])
        outcome = await chronicler.write(brief(), default_itinerary())

        assert outcome.content.pieces == []
        assert any("Writing failed" in c for c in outcome.content.caveats)


class TestDropsAndCaveats:
    async def test_unrequested_kind_is_dropped(self):
        chronicler, llm = agent([reply(piece(kind="haiku"))])
        outcome = await chronicler.write(brief(), default_itinerary())

        assert outcome.content.pieces == []
        assert any("not one of the requested kinds" in c for c in outcome.content.caveats)

    async def test_empty_body_is_dropped(self):
        chronicler, llm = agent([reply(piece(body="   "))])
        outcome = await chronicler.write(brief(), default_itinerary())

        assert outcome.content.pieces == []
        assert any("empty body" in c for c in outcome.content.caveats)

    async def test_body_over_the_length_limit_is_dropped(self):
        chronicler, llm = agent([reply(piece(body="x" * 100))], max_piece_chars=10)
        outcome = await chronicler.write(brief(), default_itinerary())

        assert outcome.content.pieces == []
        assert any("exceeds the 10-character limit" in c for c in outcome.content.caveats)

    async def test_unrelated_claim_id_is_dropped(self):
        chronicler, llm = agent([reply(piece(claim_ids=["ghost-claim"]))])
        outcome = await chronicler.write(brief(), default_itinerary())

        assert outcome.content.pieces[0].claim_ids == []
        assert any("unverified or unrelated claim" in c for c in outcome.content.caveats)


class TestBudgets:
    async def test_piece_limit_is_reported(self):
        chronicler, llm = agent([reply(piece(kind="article"), piece(kind="thread"))], max_pieces=1)
        outcome = await chronicler.write(brief(), default_itinerary())

        assert len(outcome.content.pieces) == 1
        assert any("Piece limit reached" in c for c in outcome.content.caveats)

    async def test_item_limit_is_reported(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park")
        second_place = make_place(id="place-2", name="Retiro Park", claim_ids=["claim-2"])
        second_item = make_item(
            title="Retiro Park", place_id="place-2", claim_ids=["claim-2"], start=time(14, 0)
        )
        chronicler, llm = agent([reply(piece())], max_items=1)

        outcome = await chronicler.write(
            brief(),
            itinerary(
                items=[make_item(), second_item],
                places=[make_place(), second_place],
                claims=[make_claim(), second_claim],
            ),
        )

        assert any("Item limit reached" in c for c in outcome.content.caveats)

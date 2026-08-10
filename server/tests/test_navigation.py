"""Offline contract tests for Navigator, the planning agent."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from periplus.agents.navigation import NavigationAgent
from periplus.geo.distance import Route, ScriptedDistance, TravelMode
from periplus.llm import LLMError, ScriptedClient, StagePolicy, Thinking
from periplus.models import (
    Check,
    Claim,
    ClaimKind,
    Evidence,
    GeoPoint,
    Place,
    PlaceKind,
    SourceKind,
    TripBrief,
    Verdict,
    VerifiedBundle,
)

PRADO = GeoPoint(lat=40.4138, lon=-3.6921)
RETIRO = GeoPoint(lat=40.4153, lon=-3.6844)


def brief(**overrides) -> TripBrief:
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
        "check": Check(verdict=Verdict.SUPPORTED, confidence=0.9, reason="stated in source"),
    }
    values.update(overrides)
    return Claim(**values)


def make_evidence(**overrides) -> Evidence:
    values = {
        "id": "evidence-1",
        "url": "https://museodelprado.es/visit",
        "snippet": "Open daily from 10am.",
        "source_kind": SourceKind.OFFICIAL,
        "fetched_at": datetime(2026, 8, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return Evidence(**values)


def make_place(**overrides) -> Place:
    values = {
        "id": "place-1",
        "name": "Museo del Prado",
        "kind": PlaceKind.MUSEUM,
        "point": PRADO,
        "claim_ids": ["claim-1"],
    }
    values.update(overrides)
    return Place(**values)


def bundle(*, places: list[Place], claims: list[Claim], evidence: list[Evidence]) -> VerifiedBundle:
    return VerifiedBundle(brief_id="brief-1", places=places, claims=claims, evidence=evidence)


def item(
    *,
    day: int = 1,
    start: str = "10:00",
    end: str | None = None,
    title: str = "Visit the Prado",
    place_id: str | None = "place-1",
    claim_ids: list[str] | None = None,
    transfer_mode: str | None = None,
    **overrides,
) -> dict:
    payload = {
        "day": day,
        "start": start,
        "end": end,
        "title": title,
        "place_id": place_id,
        "notes": None,
        "claim_ids": claim_ids if claim_ids is not None else ["claim-1"],
        "booking_required": False,
        "estimated_cost": None,
        "transfer_mode": transfer_mode,
    }
    payload.update(overrides)
    return payload


def reply(*items: dict, caveats: list[str] | None = None) -> str:
    return json.dumps({"items": list(items), "caveats": caveats or []})


def agent(replies, *, distance=None, **overrides) -> tuple[NavigationAgent, ScriptedClient]:
    llm = ScriptedClient(replies, max_attempts=overrides.pop("llm_attempts", 1))
    navigator = NavigationAgent(
        llm=llm,
        policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        distance=distance,
        **overrides,
    )
    return navigator, llm


class TestScheduling:
    async def test_single_item_is_scheduled(self):
        navigator, llm = agent([reply(item())])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        itinerary = outcome.itinerary
        assert len(itinerary.days) == 2  # a 2-day brief always yields 2 day buckets
        assert [i.title for i in itinerary.days[0].items] == ["Visit the Prado"]
        assert itinerary.days[1].items == []
        assert itinerary.places[0].id == "place-1"
        assert itinerary.claims[0].id == "claim-1"
        assert itinerary.evidence[0].id == "evidence-1"
        assert llm.call_count == 1

    async def test_no_usable_place_never_calls_the_model(self):
        unusable = make_claim(check=Check(verdict=Verdict.CONTRADICTED, confidence=0.9, reason="x"))
        navigator, llm = agent([])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[unusable], evidence=[make_evidence()]
        ))

        assert outcome.itinerary.days == []
        assert "nothing was scheduled" in outcome.itinerary.caveats[0]
        assert llm.call_count == 0

    async def test_items_are_sorted_within_a_day(self):
        navigator, llm = agent([reply(
            item(start="14:00", title="Afternoon", place_id=None, claim_ids=[]),
            item(start="09:00", title="Morning", place_id=None, claim_ids=[]),
        )])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        titles = [i.title for i in outcome.itinerary.days[0].items]
        assert titles == ["Morning", "Afternoon"]


class TestDropsAndCaveats:
    async def test_unknown_place_id_is_dropped_not_the_item(self):
        navigator, llm = agent([reply(item(place_id="ghost", claim_ids=[]))])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        day_item = outcome.itinerary.days[0].items[0]
        assert day_item.place_id is None
        assert any("unknown place" in c for c in outcome.itinerary.caveats)

    async def test_claim_not_belonging_to_the_place_is_dropped(self):
        other_claim = make_claim(id="claim-2", subject="Retiro Park")
        other_place = make_place(
            id="place-2", name="Retiro Park", point=RETIRO, claim_ids=["claim-2"]
        )
        navigator, llm = agent([reply(item(place_id="place-1", claim_ids=["claim-2"]))])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place(), other_place],
            claims=[make_claim(), other_claim],
            evidence=[make_evidence()],
        ))

        day_item = outcome.itinerary.days[0].items[0]
        assert day_item.claim_ids == []
        assert any("unrelated claim" in c for c in outcome.itinerary.caveats)

    async def test_day_outside_the_trip_is_dropped(self):
        navigator, llm = agent([reply(item(day=99))])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        assert all(not day.items for day in outcome.itinerary.days)
        assert any("outside the trip" in c for c in outcome.itinerary.caveats)

    async def test_unparseable_start_time_is_dropped(self):
        navigator, llm = agent([reply(item(start="not-a-time"))])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        assert all(not day.items for day in outcome.itinerary.days)
        assert any("invalid start time" in c for c in outcome.itinerary.caveats)

    async def test_structured_output_failure_is_a_caveat_not_an_exception(self):
        navigator, llm = agent([LLMError("boom")])
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        assert outcome.itinerary.days == []
        assert any("Planning failed" in c for c in outcome.itinerary.caveats)


class TestGroundedTransfers:
    async def test_transfer_is_grounded_from_the_distance_provider(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park")
        second_place = make_place(
            id="place-2", name="Retiro Park", point=RETIRO, claim_ids=["claim-2"]
        )
        distance = ScriptedDistance()
        distance.register(
            PRADO, RETIRO, TravelMode.WALKING, Route(850, 620, TravelMode.WALKING)
        )

        navigator, llm = agent(
            [reply(
                item(start="10:00", title="Prado", place_id="place-1"),
                item(
                    start="12:00", title="Retiro", place_id="place-2",
                    claim_ids=["claim-2"], transfer_mode="walking",
                ),
            )],
            distance=distance,
        )
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place(), second_place],
            claims=[make_claim(), second_claim],
            evidence=[make_evidence()],
        ))

        items = outcome.itinerary.days[0].items
        assert items[0].transfer_in is None
        assert items[1].transfer_in.mode == "walking"
        assert items[1].transfer_in.minutes == 10
        assert items[1].transfer_in.meters == 850
        assert len(distance.queries) == 1

    async def test_missing_provider_adds_one_trip_level_caveat(self):
        navigator, llm = agent([reply(item())], distance=None)
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place()], claims=[make_claim()], evidence=[make_evidence()]
        ))

        caveats = [c for c in outcome.itinerary.caveats if "distance provider" in c]
        assert len(caveats) == 1
        assert outcome.itinerary.days[0].items[0].transfer_in is None

    async def test_route_lookup_failure_is_a_caveat_not_a_crash(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park")
        second_place = make_place(
            id="place-2", name="Retiro Park", point=RETIRO, claim_ids=["claim-2"]
        )
        distance = ScriptedDistance()  # nothing registered -> NoRouteFound

        navigator, llm = agent(
            [reply(
                item(start="10:00", title="Prado", place_id="place-1"),
                item(start="12:00", title="Retiro", place_id="place-2", claim_ids=["claim-2"]),
            )],
            distance=distance,
        )
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place(), second_place],
            claims=[make_claim(), second_claim],
            evidence=[make_evidence()],
        ))

        assert outcome.itinerary.days[0].items[1].transfer_in is None
        assert any("Could not ground the transfer" in c for c in outcome.itinerary.caveats)
        assert isinstance(distance, ScriptedDistance)

    async def test_missing_coordinates_are_a_caveat(self):
        no_point_place = make_place(
            id="place-2", name="Somewhere", point=None, claim_ids=["claim-1"]
        )
        distance = ScriptedDistance()

        navigator, llm = agent(
            [reply(
                item(start="10:00", title="Prado", place_id="place-1"),
                item(start="12:00", title="Somewhere else", place_id="place-2"),
            )],
            distance=distance,
        )
        outcome = await navigator.plan(brief(), bundle(
            places=[make_place(), no_point_place],
            claims=[make_claim()],
            evidence=[make_evidence()],
        ))

        assert outcome.itinerary.days[0].items[1].transfer_in is None
        assert any("No coordinates" in c for c in outcome.itinerary.caveats)


class TestBudgets:
    async def test_place_and_claim_limits_are_reported(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park")
        second_place = make_place(
            id="place-2", name="Retiro Park", point=RETIRO, claim_ids=["claim-2"]
        )
        navigator, llm = agent([reply(item())], max_places=1, max_claims=1)

        outcome = await navigator.plan(brief(), bundle(
            places=[make_place(), second_place],
            claims=[make_claim(), second_claim],
            evidence=[make_evidence()],
        ))

        assert any("Place limit reached" in c for c in outcome.itinerary.caveats)

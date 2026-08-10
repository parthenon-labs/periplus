"""Contract tests for the domain model.

These pin the behaviour the rest of the pipeline is allowed to rely on: a claim is not
usable until something has checked it, and stage artifacts survive a round trip through
JSON so a stage can be replayed from stored input.
"""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from periplus.models import (
    Check,
    Claim,
    ClaimKind,
    Evidence,
    Place,
    PlaceKind,
    ResearchBundle,
    SourceKind,
    TripBrief,
    Verdict,
    VerifiedBundle,
)


def make_brief(**overrides) -> TripBrief:
    fields = {
        "destination": "Lisbon",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 5),
        "interests": ["architecture", "seafood"],
    }
    fields.update(overrides)
    return TripBrief(**fields)


def make_evidence(**overrides) -> Evidence:
    fields = {
        "url": "https://museudoazulejo.gov.pt/visit",
        "title": "Visit",
        "snippet": "Open Tuesday to Sunday, 10:00-13:00 and 14:00-18:00.",
        "source_kind": SourceKind.OFFICIAL,
    }
    fields.update(overrides)
    return Evidence(**fields)


class TestTripBrief:
    def test_day_count_is_inclusive_of_both_ends(self):
        brief = make_brief()
        assert brief.nights == 4
        assert brief.days == 5

    def test_rejects_end_before_start(self):
        with pytest.raises(ValidationError):
            make_brief(start_date=date(2026, 9, 5), end_date=date(2026, 9, 1))

    def test_single_day_trip_is_valid(self):
        brief = make_brief(start_date=date(2026, 9, 1), end_date=date(2026, 9, 1))
        assert brief.days == 1

    def test_party_size_counts_everyone(self):
        brief = make_brief(party={"adults": 2, "children": 1, "child_ages": [7]})
        assert brief.party.size == 3


class TestClaimVerification:
    def test_claim_without_evidence_reports_no_evidence(self):
        claim = Claim(subject="Museu do Azulejo", text="Closed on Mondays.")
        assert claim.verdict is Verdict.NO_EVIDENCE
        assert not claim.is_verified
        assert not claim.is_usable

    def test_claim_with_evidence_but_no_check_is_not_usable(self):
        """Research attaching a source is not the same as verification passing."""
        evidence = make_evidence()
        claim = Claim(
            subject="Museu do Azulejo",
            text="Closed on Mondays.",
            kind=ClaimKind.HOURS,
            evidence_ids=[evidence.id],
        )
        assert claim.verdict is Verdict.UNSUPPORTED
        assert not claim.is_verified
        assert not claim.is_usable

    @pytest.mark.parametrize(
        ("verdict", "usable"),
        [
            (Verdict.SUPPORTED, True),
            (Verdict.PARTIAL, True),
            (Verdict.STALE, True),
            (Verdict.CONTRADICTED, False),
            (Verdict.UNSUPPORTED, False),
            (Verdict.NO_EVIDENCE, False),
        ],
    )
    def test_usability_follows_verdict(self, verdict: Verdict, usable: bool):
        claim = Claim(subject="Tram 28", text="Runs from Martim Moniz.")
        claim.check = Check(verdict=verdict, confidence=0.9, reason="checked")
        assert claim.is_verified
        assert claim.is_usable is usable

    def test_volatile_kinds_carry_a_freshness_window(self):
        assert Claim(subject="x", text="y", kind=ClaimKind.PRICE).freshness_days() is not None
        assert Claim(subject="x", text="y", kind=ClaimKind.HOURS).freshness_days() is not None

    def test_stable_kinds_do_not_expire(self):
        assert Claim(subject="x", text="y", kind=ClaimKind.LOCATION).freshness_days() is None


class TestBundles:
    def build(self) -> tuple[ResearchBundle, Claim, Place]:
        evidence = make_evidence()
        claim = Claim(
            subject="Museu do Azulejo",
            text="Closed on Mondays.",
            kind=ClaimKind.HOURS,
            evidence_ids=[evidence.id],
        )
        place = Place(
            name="Museu Nacional do Azulejo",
            kind=PlaceKind.MUSEUM,
            claim_ids=[claim.id],
        )
        bundle = ResearchBundle(
            brief_id="brief-1",
            places=[place],
            claims=[claim],
            evidence=[evidence],
            queries_run=["museu do azulejo opening hours"],
        )
        return bundle, claim, place

    def test_claims_resolve_from_place(self):
        bundle, claim, place = self.build()
        assert bundle.claims_for(place) == [claim]

    def test_evidence_index_is_keyed_by_id(self):
        bundle, _, _ = self.build()
        evidence = bundle.evidence[0]
        assert bundle.evidence_by_id()[evidence.id] is evidence

    def test_verified_bundle_partitions_by_usability(self):
        bundle, claim, _ = self.build()
        rejected = Claim(subject="Tram 28", text="Free for tourists.", kind=ClaimKind.PRICE)
        rejected.check = Check(
            verdict=Verdict.CONTRADICTED, confidence=0.95, reason="Fare is EUR 3.20."
        )
        claim.check = Check(verdict=Verdict.SUPPORTED, confidence=0.9, reason="Site says so.")

        verified = VerifiedBundle(
            **bundle.model_dump(),
        )
        verified.claims = [claim, rejected]

        assert verified.usable_claims == [claim]
        assert verified.rejected_claims == [rejected]
        counts = verified.verdict_counts()
        assert counts[Verdict.SUPPORTED] == 1
        assert counts[Verdict.CONTRADICTED] == 1
        assert counts[Verdict.NO_EVIDENCE] == 0


class TestReplayability:
    """Stage boundaries must survive storage, or replaying a stage is impossible."""

    def test_research_bundle_round_trips_through_json(self):
        evidence = make_evidence()
        claim = Claim(
            subject="Museu do Azulejo",
            text="Closed on Mondays.",
            kind=ClaimKind.HOURS,
            evidence_ids=[evidence.id],
        )
        bundle = ResearchBundle(brief_id="brief-1", claims=[claim], evidence=[evidence])

        restored = ResearchBundle.model_validate_json(bundle.model_dump_json())

        assert restored.claims[0].id == claim.id
        assert restored.claims[0].kind is ClaimKind.HOURS
        assert restored.evidence[0].snippet == evidence.snippet

    def test_brief_round_trips_through_json(self):
        brief = make_brief(party={"adults": 2, "children": 1, "child_ages": [7]})
        restored = TripBrief.model_validate_json(brief.model_dump_json())
        assert restored == brief

    def test_unknown_fields_are_rejected(self):
        """Extra keys mean a schema drift we would rather fail on than silently drop."""
        with pytest.raises(ValidationError):
            TripBrief(
                destination="Lisbon",
                start_date=date(2026, 9, 1),
                end_date=date(2026, 9, 2),
                budgetz=100,
            )

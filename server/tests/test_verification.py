"""Offline contract tests for Auditor, the verification agent."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from periplus.agents import build_verification_agent
from periplus.agents.verification import VerificationAgent, evidence_is_stale
from periplus.config import Settings
from periplus.llm import LLMError, ScriptedClient, StagePolicy, Thinking
from periplus.models import (
    Claim,
    ClaimKind,
    Evidence,
    ResearchBundle,
    SourceKind,
    Stage,
    Verdict,
)

FIXTURES = Path(__file__).parent / "fixtures" / "verification"
AS_OF = date(2026, 8, 10)


def fixture(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text()


def make_claim(**overrides) -> Claim:
    values = {
        "id": "claim-1",
        "subject": "Harbour Museum",
        "text": "The Harbour Museum opens at 9am.",
        "kind": ClaimKind.HOURS,
        "evidence_ids": ["evidence-1"],
    }
    values.update(overrides)
    return Claim(**values)


def make_evidence(**overrides) -> Evidence:
    values = {
        "id": "evidence-1",
        "url": "https://museum.example/visit",
        "title": "Visit",
        "snippet": "The Harbour Museum opens daily at 9am.",
        "source_kind": SourceKind.OFFICIAL,
        "published_at": date(2026, 7, 1),
        "fetched_at": datetime(2026, 7, 2, tzinfo=UTC),
    }
    values.update(overrides)
    return Evidence(**values)


def decision(
    claim_id: str,
    evidence_id: str,
    *,
    verdict: str = "supported",
    supporting: list[str] | None = None,
    conflicting: list[str] | None = None,
) -> dict:
    if supporting is None:
        supporting = [evidence_id] if verdict in {"supported", "partial"} else []
    if conflicting is None:
        conflicting = [evidence_id] if verdict == "contradicted" else []
    return {
        "claim_id": claim_id,
        "verdict": verdict,
        "confidence": 0.9,
        "reason": "The supplied passage determines this verdict.",
        "supporting_evidence_ids": supporting,
        "conflicting_evidence_ids": conflicting,
    }


def reply(*decisions: dict) -> str:
    return json.dumps({"decisions": list(decisions)})


def agent(replies, **overrides) -> tuple[VerificationAgent, ScriptedClient]:
    llm = ScriptedClient(replies, max_attempts=overrides.pop("llm_attempts", 1))
    auditor = VerificationAgent(
        llm=llm,
        policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        **overrides,
    )
    return auditor, llm


class TestVerdicts:
    @pytest.mark.parametrize(
        ("fixture_name", "verdict"),
        [
            ("supported", Verdict.SUPPORTED),
            ("partial", Verdict.PARTIAL),
            ("contradicted", Verdict.CONTRADICTED),
            ("unsupported", Verdict.UNSUPPORTED),
        ],
    )
    async def test_semantic_verdicts_come_from_grounded_fixtures(self, fixture_name, verdict):
        auditor, llm = agent([fixture(fixture_name)])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)

        assert outcome.claims[0].verdict is verdict
        assert outcome.claims[0].check is not None
        assert outcome.claims[0].check.model == "scripted"
        assert outcome.calls[0].stage is Stage.VERIFY
        assert outcome.failures == []
        assert llm.call_count == 1

    async def test_no_evidence_is_deterministic_and_never_calls_the_model(self):
        auditor, llm = agent([])
        claim = make_claim(evidence_ids=[])
        outcome = await auditor.verify([claim], [make_evidence()], as_of=AS_OF)

        assert outcome.claims[0].verdict is Verdict.NO_EVIDENCE
        assert outcome.claims[0].check.confidence == 1.0
        assert outcome.claims[0].check.model is None
        assert outcome.calls == []
        assert llm.call_count == 0

    async def test_all_six_verdicts_are_reachable(self):
        semantic = {
            name: (
                await agent([fixture(name)])[0].verify(
                    [make_claim()], [make_evidence()], as_of=AS_OF
                )
            )
            .claims[0]
            .verdict
            for name in ("supported", "partial", "contradicted", "unsupported")
        }
        no_evidence = await agent([])[0].verify([make_claim(evidence_ids=[])], [], as_of=AS_OF)
        stale = await agent([fixture("supported")])[0].verify(
            [make_claim()], [make_evidence(published_at=date(2025, 1, 1))], as_of=AS_OF
        )

        assert set(semantic.values()) | {
            no_evidence.claims[0].verdict,
            stale.claims[0].verdict,
        } == set(Verdict)


class TestFreshness:
    async def test_old_support_is_downgraded_to_stale_by_code(self):
        auditor, _ = agent([fixture("supported")])
        outcome = await auditor.verify(
            [make_claim()], [make_evidence(published_at=date(2025, 1, 1))], as_of=AS_OF
        )
        check = outcome.claims[0].check
        assert check.verdict is Verdict.STALE
        assert "180-day freshness window" in check.reason
        assert check.supporting_evidence_ids == ["evidence-1"]

    async def test_evidence_exactly_at_window_is_still_fresh(self):
        auditor, _ = agent([fixture("supported")])
        outcome = await auditor.verify(
            [make_claim()], [make_evidence(published_at=date(2026, 2, 11))], as_of=AS_OF
        )
        assert outcome.claims[0].verdict is Verdict.SUPPORTED

    async def test_any_fresh_support_prevents_stale(self):
        claim = make_claim(evidence_ids=["evidence-1", "evidence-2"])
        evidence = [
            make_evidence(published_at=date(2025, 1, 1)),
            make_evidence(
                id="evidence-2",
                url="https://city.example/museum",
                published_at=date(2026, 8, 1),
            ),
        ]
        response = reply(
            decision(
                "claim-1",
                "evidence-1",
                supporting=["evidence-1", "evidence-2"],
            )
        )
        auditor, _ = agent([response])
        outcome = await auditor.verify([claim], evidence, as_of=AS_OF)
        assert outcome.claims[0].verdict is Verdict.SUPPORTED

    async def test_undated_support_uses_fetched_date(self):
        auditor, _ = agent([fixture("supported"), fixture("supported")])
        fresh = await auditor.verify(
            [make_claim()], [make_evidence(published_at=None)], as_of=AS_OF
        )
        old = await auditor.verify(
            [make_claim()],
            [
                make_evidence(
                    published_at=None,
                    fetched_at=datetime(2025, 1, 1, tzinfo=UTC),
                )
            ],
            as_of=AS_OF,
        )
        assert fresh.claims[0].verdict is Verdict.SUPPORTED
        assert old.claims[0].verdict is Verdict.STALE

    async def test_stable_claim_kind_never_expires(self):
        auditor, _ = agent([fixture("supported")])
        outcome = await auditor.verify(
            [make_claim(kind=ClaimKind.LOCATION)],
            [make_evidence(published_at=date(2000, 1, 1))],
            as_of=AS_OF,
        )
        assert outcome.claims[0].verdict is Verdict.SUPPORTED

    def test_freshness_helper_uses_supporting_evidence_not_fetch_time(self):
        claim = make_claim()
        evidence = make_evidence(
            published_at=date(2020, 1, 1),
            fetched_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        assert evidence_is_stale(claim, [evidence], as_of=AS_OF)


class TestEvidenceGroupingAndIDs:
    async def test_all_evidence_for_a_claim_is_sent_in_one_group(self):
        claim = make_claim(evidence_ids=["evidence-1", "evidence-2"])
        evidence = [
            make_evidence(),
            make_evidence(
                id="evidence-2",
                url="https://guide.example/museum",
                snippet="The museum opens at 10am.",
            ),
        ]
        auditor, llm = agent([fixture("multi_evidence")])
        outcome = await auditor.verify([claim], evidence, as_of=AS_OF)

        assert outcome.claims[0].verdict is Verdict.PARTIAL
        assert outcome.claims[0].check.supporting_evidence_ids == ["evidence-1"]
        assert outcome.claims[0].check.conflicting_evidence_ids == ["evidence-2"]
        prompt = llm.last_request[1].content
        assert prompt.count('"claim":{"id":"claim-1"') == 1
        assert '"id":"evidence-1"' in prompt
        assert '"id":"evidence-2"' in prompt

    async def test_unprovided_evidence_id_rejects_the_whole_batch(self):
        bad = reply(decision("claim-1", "invented-evidence"))
        auditor, _ = agent([bad])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)

        assert outcome.claims[0].check is None
        assert len(outcome.failures) == 1
        assert "unprovided evidence IDs" in outcome.failures[0].reason

    @pytest.mark.parametrize(
        "bad_reply",
        [
            reply(),
            reply(decision("other-claim", "evidence-1")),
            reply(
                decision("claim-1", "evidence-1"),
                decision("claim-1", "evidence-1"),
            ),
        ],
    )
    async def test_missing_unexpected_or_duplicate_claim_ids_are_rejected(self, bad_reply):
        auditor, _ = agent([bad_reply])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)
        assert outcome.claims[0].check is None
        assert "Invalid verification output" in outcome.failures[0].reason

    async def test_an_id_cannot_be_both_supporting_and_conflicting(self):
        bad = reply(
            decision(
                "claim-1",
                "evidence-1",
                verdict="partial",
                supporting=["evidence-1"],
                conflicting=["evidence-1"],
            )
        )
        auditor, _ = agent([bad])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)
        assert "both support and conflict" in outcome.failures[0].reason

    async def test_missing_input_evidence_is_reported_but_available_evidence_is_checked(self):
        claim = make_claim(evidence_ids=["evidence-1", "missing"])
        auditor, llm = agent([fixture("supported")])
        outcome = await auditor.verify([claim], [make_evidence()], as_of=AS_OF)

        assert outcome.claims[0].verdict is Verdict.SUPPORTED
        assert llm.call_count == 1
        assert outcome.failures[0].evidence_ids == ["missing"]

    async def test_no_resolved_evidence_returns_no_evidence_and_reports_dangling_id(self):
        auditor, llm = agent([])
        outcome = await auditor.verify([make_claim(evidence_ids=["missing"])], [], as_of=AS_OF)
        assert outcome.claims[0].verdict is Verdict.NO_EVIDENCE
        assert "not supplied" in outcome.failures[0].reason
        assert llm.call_count == 0

    async def test_repeated_claim_evidence_id_is_reported_and_checked_once(self):
        auditor, llm = agent([fixture("supported")])
        outcome = await auditor.verify(
            [make_claim(evidence_ids=["evidence-1", "evidence-1"])],
            [make_evidence()],
            as_of=AS_OF,
        )
        assert outcome.claims[0].verdict is Verdict.SUPPORTED
        assert "repeats evidence IDs" in outcome.failures[0].reason
        assert llm.last_request[1].content.count('"id":"evidence-1"') == 1

    async def test_duplicate_evidence_id_is_an_explicit_ambiguity(self):
        duplicate = make_evidence(url="https://other.example/visit")
        auditor, llm = agent([])
        outcome = await auditor.verify([make_claim()], [make_evidence(), duplicate], as_of=AS_OF)
        assert outcome.claims[0].check is None
        assert "Duplicate evidence ID" in outcome.failures[0].reason
        assert llm.call_count == 0


class TestBudgetsAndBatches:
    async def test_claim_count_batches_model_calls(self):
        claims = [
            make_claim(id=f"claim-{number}", evidence_ids=[f"evidence-{number}"])
            for number in range(1, 4)
        ]
        evidence = [
            make_evidence(id=f"evidence-{number}", url=f"https://museum.example/{number}")
            for number in range(1, 4)
        ]
        replies = [
            reply(
                decision("claim-1", "evidence-1"),
                decision("claim-2", "evidence-2"),
            ),
            reply(decision("claim-3", "evidence-3")),
        ]
        auditor, llm = agent(replies, claims_per_batch=2)
        outcome = await auditor.verify(claims, evidence, as_of=AS_OF)
        assert llm.call_count == 2
        assert all(claim.verdict is Verdict.SUPPORTED for claim in outcome.claims)

    async def test_per_batch_character_limit_fails_explicitly_without_truncation(self):
        auditor, llm = agent([], chars_per_batch=200)
        outcome = await auditor.verify(
            [make_claim()], [make_evidence(snippet="x" * 500)], as_of=AS_OF
        )
        assert llm.call_count == 0
        assert outcome.claims[0].check is None
        assert "per-batch input limit" in outcome.failures[0].reason

    async def test_total_input_budget_rejects_later_batch_explicitly(self):
        claims = [
            make_claim(id=f"claim-{number}", evidence_ids=[f"evidence-{number}"])
            for number in (1, 2)
        ]
        evidence = [
            make_evidence(id=f"evidence-{number}", url=f"https://museum.example/{number}")
            for number in (1, 2)
        ]
        first_prompt_budget = 700
        auditor, llm = agent(
            [reply(decision("claim-1", "evidence-1"))],
            claims_per_batch=1,
            chars_per_batch=2_000,
            max_input_chars=first_prompt_budget,
        )
        outcome = await auditor.verify(claims, evidence, as_of=AS_OF)
        assert llm.call_count == 1
        assert outcome.claims[0].verdict is Verdict.SUPPORTED
        assert outcome.claims[1].check is None
        assert "input budget reached" in outcome.failures[0].reason

    async def test_claim_limit_does_not_silently_drop_excess_claims(self):
        claims = [
            make_claim(id=f"claim-{number}", evidence_ids=[f"evidence-{number}"])
            for number in (1, 2)
        ]
        evidence = [
            make_evidence(id=f"evidence-{number}", url=f"https://museum.example/{number}")
            for number in (1, 2)
        ]
        auditor, llm = agent([reply(decision("claim-1", "evidence-1"))], max_claims=1)
        outcome = await auditor.verify(claims, evidence, as_of=AS_OF)
        assert llm.call_count == 1
        assert outcome.claims[1].check is None
        assert "claim limit" in outcome.failures[0].reason

    async def test_evidence_limit_fails_instead_of_omitting_a_source(self):
        claim = make_claim(evidence_ids=["evidence-1", "evidence-2"])
        evidence = [make_evidence(), make_evidence(id="evidence-2")]
        auditor, llm = agent([], max_evidence_per_claim=1)
        outcome = await auditor.verify([claim], evidence, as_of=AS_OF)
        assert llm.call_count == 0
        assert "no evidence was silently omitted" in outcome.failures[0].reason


class TestFailuresAndArtifacts:
    @pytest.mark.parametrize("verdict", ["stale", "no_evidence"])
    async def test_model_cannot_assign_code_owned_verdicts(self, verdict):
        bad = reply(decision("claim-1", "evidence-1", verdict=verdict))
        auditor, _ = agent([bad])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)
        assert outcome.claims[0].check is None
        assert "Structured verification failed" in outcome.failures[0].reason

    async def test_invalid_structured_output_is_visible_and_preserves_call(self):
        auditor, _ = agent(["not json"])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)
        assert outcome.claims[0].check is None
        assert len(outcome.calls) == 1
        assert "Structured verification failed" in outcome.failures[0].reason

    async def test_permanent_model_error_is_visible(self):
        auditor, _ = agent([LLMError("offline fixture failed")])
        outcome = await auditor.verify([make_claim()], [make_evidence()], as_of=AS_OF)
        assert outcome.claims[0].check is None
        assert "offline fixture failed" in outcome.failures[0].reason

    async def test_input_claim_is_not_mutated(self):
        claim = make_claim()
        auditor, _ = agent([fixture("supported")])
        outcome = await auditor.verify([claim], [make_evidence()], as_of=AS_OF)
        assert claim.check is None
        assert outcome.claims[0].check is not None

    async def test_outcome_reattaches_to_bundle_and_carries_failures_as_gaps(self):
        claim = make_claim(evidence_ids=["evidence-1", "missing"])
        source = ResearchBundle(
            brief_id="brief-1", claims=[claim], evidence=[make_evidence()], gaps=["prior gap"]
        )
        auditor, _ = agent([fixture("supported")])
        outcome = await auditor.verify(source.claims, source.evidence, as_of=AS_OF)
        verified = outcome.to_bundle(source)

        assert verified.claims[0].verdict is Verdict.SUPPORTED
        assert verified.gaps[0] == "prior gap"
        assert "Verification failed" in verified.gaps[1]
        assert not outcome.is_complete


class TestConfiguration:
    def test_builder_wires_all_auditor_limits_to_an_offline_client(self, monkeypatch):
        import periplus.llm

        client = ScriptedClient([])
        monkeypatch.setattr(periplus.llm, "build_client", lambda settings: client)
        settings = Settings(
            _env_file=None,
            llm_model="scripted",
            llm_model_verify=None,
            verification_claims_per_batch=2,
            verification_chars_per_batch=3_000,
            max_verification_claims=4,
            max_verification_input_chars=5_000,
            max_evidence_per_claim=3,
        )

        auditor = build_verification_agent(settings)

        assert auditor.llm is client
        assert auditor.policy.thinking is Thinking.OFF
        assert auditor.claims_per_batch == 2
        assert auditor.chars_per_batch == 3_000
        assert auditor.max_claims == 4
        assert auditor.max_input_chars == 5_000
        assert auditor.max_evidence_per_claim == 3

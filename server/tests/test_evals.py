"""Tests for the eval harness itself.

An eval suite that cannot fail is decoration. Roughly half of what follows exists to prove
the opposite: that a broken quote, a bad citation, an unmet threshold and a typo'd metric
name each turn the suite red.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from periplus.evals.case import (
    CaseError,
    CorpusPage,
    Expectation,
    ModelScript,
    VerdictLabel,
    load_case,
    load_cases,
)
from periplus.evals.harness import SUPPORTED_STAGES, run_case, run_suite
from periplus.evals.metrics import CitationFailureKind, citation_failures, measure
from periplus.evals.offline import CorpusRetriever, VerdictOracle
from periplus.evals.report import compare, render_markdown, to_payload, write_reports
from periplus.models import (
    Check,
    Claim,
    ClaimKind,
    ContentPiece,
    ContentSet,
    Evidence,
    Itinerary,
    ItineraryDay,
    ItineraryItem,
    ModelCall,
    ResearchBundle,
    Run,
    RunStatus,
    Stage,
    StageRun,
    TripBrief,
    Verdict,
    VerifiedBundle,
)

CASES_DIR = Path(__file__).resolve().parent.parent / "evals" / "cases"


def brief() -> TripBrief:
    return TripBrief(
        destination="Sydney", start_date=date(2026, 10, 1), end_date=date(2026, 10, 3)
    )


def evidence(**overrides) -> Evidence:
    values = {
        "url": "https://example.gov.au/visit",
        "snippet": "Open daily from 9am.",
    }
    values.update(overrides)
    return Evidence(**values)


def claim(verdict: Verdict | None = Verdict.SUPPORTED, **overrides) -> Claim:
    values = {"subject": "Somewhere", "text": "Open daily from 9am.", "kind": ClaimKind.HOURS}
    values.update(overrides)
    built = Claim(**values)
    if verdict is not None:
        built.check = Check(verdict=verdict, confidence=0.9, reason="labelled")
    return built


def run_with(**overrides) -> Run:
    run = Run(brief=brief(), status=RunStatus.SUCCEEDED)
    for key, value in overrides.items():
        setattr(run, key, value)
    return run


class TestMetrics:
    def test_gap_rate_counts_everything_outside_usable(self):
        item = evidence()
        claims = [
            claim(Verdict.SUPPORTED, evidence_ids=[item.id]),
            claim(Verdict.STALE, evidence_ids=[item.id]),
            claim(Verdict.CONTRADICTED, evidence_ids=[item.id]),
            claim(Verdict.NO_EVIDENCE),
        ]
        bundle = VerifiedBundle(brief_id="b", claims=claims, evidence=[item])
        metrics = measure(run_with(verified=bundle))

        assert metrics.claims == 4
        assert metrics.usable_claims == 2
        assert metrics.gap_rate == 0.5
        assert metrics.flat()["verdict.stale"] == 1

    def test_an_empty_run_is_a_total_gap_not_a_perfect_score(self):
        """Zero claims must never satisfy a ``gap_rate <= x`` threshold."""
        metrics = measure(run_with(verified=VerifiedBundle(brief_id="b")))
        assert metrics.claims == 0
        assert metrics.gap_rate == 1.0
        assert metrics.usable_rate == 0.0

    def test_research_only_run_is_not_penalised_for_missing_verdicts(self):
        item = evidence()
        bundle = ResearchBundle(
            brief_id="b", claims=[claim(None, evidence_ids=[item.id])], evidence=[item]
        )
        assert citation_failures(run_with(research=bundle)) == []

    def test_dangling_evidence_is_a_citation_failure(self):
        bundle = VerifiedBundle(
            brief_id="b", claims=[claim(Verdict.SUPPORTED, evidence_ids=["missing"])], evidence=[]
        )
        failures = citation_failures(run_with(verified=bundle))
        assert [f.kind for f in failures] == [CitationFailureKind.DANGLING_EVIDENCE]

    def test_unverified_claim_reaching_verification_output_is_a_failure(self):
        bundle = VerifiedBundle(brief_id="b", claims=[claim(None)], evidence=[])
        kinds = {f.kind for f in citation_failures(run_with(verified=bundle))}
        assert CitationFailureKind.UNVERIFIED_CLAIM in kinds

    def test_itinerary_may_not_schedule_a_contradicted_claim(self):
        bad = claim(Verdict.CONTRADICTED)
        itinerary = Itinerary(
            brief_id="b",
            destination="Sydney",
            claims=[bad],
            days=[
                ItineraryDay(
                    day=1,
                    date=date(2026, 10, 1),
                    items=[ItineraryItem(day=1, title="Visit", claim_ids=[bad.id])],
                )
            ],
        )
        failures = citation_failures(run_with(itinerary=itinerary))
        assert [f.kind for f in failures] == [CitationFailureKind.UNUSABLE_CLAIM]

    def test_content_citing_a_claim_it_does_not_carry_is_a_failure(self):
        content = ContentSet(
            itinerary_id="i",
            claims=[],
            pieces=[ContentPiece(kind="article", body="text", claim_ids=["ghost"])],
        )
        failures = citation_failures(run_with(content=content))
        assert [f.kind for f in failures] == [CitationFailureKind.DANGLING_CLAIM]

    def test_stage_metrics_accumulate_attempts_and_keep_the_last_status(self):
        run = run_with()
        run.stages = [
            StageRun(
                stage=Stage.RESEARCH,
                attempt=1,
                status=RunStatus.FAILED,
                queries=3,
                calls=[
                    ModelCall(
                        stage=Stage.RESEARCH, model="m", prompt_hash="aaa", error="boom"
                    )
                ],
            ),
            StageRun(
                stage=Stage.RESEARCH,
                attempt=2,
                status=RunStatus.SUCCEEDED,
                queries=2,
                fetches=4,
                calls=[
                    ModelCall(
                        stage=Stage.RESEARCH,
                        model="m",
                        prompt_hash="bbb",
                        prompt_tokens=100,
                        completion_tokens=20,
                        cost_usd=0.5,
                        latency_ms=7,
                    )
                ],
            ),
        ]
        metrics = measure(run)
        stage = metrics.stages[Stage.RESEARCH]

        assert stage.attempts == 2
        assert stage.failed_attempts == 1
        assert stage.status is RunStatus.SUCCEEDED
        assert stage.queries == 5
        assert stage.tokens == 120
        assert metrics.stage_retries == 1
        assert metrics.failed_model_calls == 1
        assert metrics.cost_usd == 0.5
        assert metrics.prompt_hashes[Stage.RESEARCH] == ("aaa", "bbb")
        assert metrics.stages_completed == (Stage.RESEARCH,)


class TestExpectation:
    def test_requires_a_bound(self):
        with pytest.raises(ValueError, match="neither min nor max"):
            Expectation(metric="gap_rate")

    def test_unknown_metric_fails_rather_than_passing_vacuously(self):
        failure = Expectation(metric="gpa_rate", max=0.2).check({"gap_rate": 0.1})
        assert failure is not None
        assert "unknown metric" in failure

    def test_reports_the_actual_value(self):
        failure = Expectation(metric="gap_rate", max=0.2).check({"gap_rate": 0.5})
        assert failure == "gap_rate = 0.5, expected <= 0.2"
        assert Expectation(metric="claims", min=3).check({"claims": 3}) is None

    def test_describe_reads_as_an_assertion(self):
        assert Expectation(metric="claims", min=3, max=3).describe() == "claims == 3"
        assert Expectation(metric="gap_rate", max=0.4).describe() == "gap_rate <= 0.4"


class TestCaseLoading:
    def _write(self, tmp_path: Path, payload: dict, name: str = "case.json") -> Path:
        path = tmp_path / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def minimal(self) -> dict:
        return {
            "id": "tiny",
            "as_of": "2026-08-17",
            "brief": {
                "destination": "Sydney",
                "start_date": "2026-10-01",
                "end_date": "2026-10-02",
            },
            "corpus": [{"url": "https://example.gov.au/a", "text": "Open daily from 9am."}],
            "expect": [{"metric": "citation_failures", "max": 0}],
        }

    def test_loads_and_defaults_to_research_then_verify(self, tmp_path: Path):
        case = load_case(self._write(tmp_path, self.minimal()))
        assert case.stages == (Stage.RESEARCH, Stage.VERIFY)
        assert case.as_of == datetime(2026, 8, 17, tzinfo=UTC)

    def test_as_of_is_mandatory(self, tmp_path: Path):
        payload = self.minimal()
        del payload["as_of"]
        with pytest.raises(CaseError, match="as_of is required"):
            load_case(self._write(tmp_path, payload))

    def test_stages_must_be_a_contiguous_prefix(self, tmp_path: Path):
        payload = self.minimal() | {"stages": ["research", "plan"]}
        with pytest.raises(CaseError, match="contiguous prefix"):
            load_case(self._write(tmp_path, payload))

    def test_a_case_with_no_expectations_is_rejected(self, tmp_path: Path):
        payload = self.minimal() | {"expect": []}
        with pytest.raises(CaseError, match="no expectations"):
            load_case(self._write(tmp_path, payload))

    def test_a_case_with_no_corpus_is_rejected(self, tmp_path: Path):
        payload = self.minimal() | {"corpus": []}
        with pytest.raises(CaseError, match="no corpus"):
            load_case(self._write(tmp_path, payload))

    def test_unknown_keys_are_rejected_rather_than_ignored(self, tmp_path: Path):
        payload = self.minimal() | {"expects": []}
        with pytest.raises(CaseError, match="unknown keys"):
            load_case(self._write(tmp_path, payload))

    def test_an_empty_directory_is_an_error_not_an_all_green_suite(self, tmp_path: Path):
        with pytest.raises(CaseError, match="no case files"):
            load_cases(tmp_path)

    def test_only_filters_by_id_or_tag(self, tmp_path: Path):
        self._write(tmp_path, self.minimal() | {"id": "one", "tags": ["alpha"]}, "one.json")
        self._write(tmp_path, self.minimal() | {"id": "two", "tags": ["beta"]}, "two.json")
        assert [c.id for c in load_cases(tmp_path, only=["alpha"])] == ["one"]
        assert [c.id for c in load_cases(tmp_path, only=["two"])] == ["two"]
        with pytest.raises(CaseError, match="matches"):
            load_cases(tmp_path, only=["gamma"])


class TestOffline:
    async def test_corpus_retriever_reports_real_attempt_counts(self):
        pages = (CorpusPage(url="https://example.gov.au/a", text="Open daily."),)
        result = await CorpusRetriever(pages).gather(["q1", "q2"], subject="Sydney")

        assert result.queries_attempted == 2
        assert result.fetch_attempts == 1
        assert result.documents[0].source_kind.value == "government"

    async def test_undated_pages_never_fall_back_to_the_real_clock(self):
        """Otherwise a freshness assertion would pass or fail depending on the day."""
        pages = (CorpusPage(url="https://example.com/a", text="Open daily."),)
        result = await CorpusRetriever(pages).gather(["q"], subject=None)
        assert result.documents[0].fetched_at.year == 1970

    async def test_oracle_records_claims_no_label_matched(self):
        oracle = VerdictOracle([VerdictLabel(match="hours", verdict="supported")])
        item = evidence()
        agent_claims = [claim(None, text="Tickets cost 8 euros.", evidence_ids=[item.id])]

        from periplus.agents.verification import VerificationAgent
        from periplus.llm import StagePolicy, Thinking

        outcome = await VerificationAgent(
            llm=oracle, policy=StagePolicy(model="eval-oracle", thinking=Thinking.OFF)
        ).verify(agent_claims, [item], as_of=date(2026, 8, 17))

        assert oracle.unmatched
        assert outcome.claims[0].verdict is Verdict.UNSUPPORTED


class TestShippedGoldenSet:
    """The committed cases are part of the test suite, so they cannot rot unnoticed."""

    def test_every_case_loads(self):
        cases = load_cases(CASES_DIR)
        assert len(cases) >= 4

    @pytest.mark.parametrize("case_id", [c.id for c in load_cases(CASES_DIR)])
    async def test_case_meets_its_expectations(self, case_id: str):
        case = next(c for c in load_cases(CASES_DIR) if c.id == case_id)
        result = await run_case(case)
        assert result.passed, result.failures

    async def test_the_suite_is_deterministic_across_runs(self):
        cases = load_cases(CASES_DIR)
        first = await run_suite(cases)
        second = await run_suite(cases)
        left = {c.case_id: c.metrics.flat() for c in first.cases}
        right = {c.case_id: c.metrics.flat() for c in second.cases}
        # Latency is wall-clock and legitimately varies; nothing else may.
        for values in (*left.values(), *right.values()):
            for key in [k for k in values if k.endswith("latency_ms")]:
                values[key] = 0
        assert left == right


class TestHarnessCatchesRegressions:
    """The half of the suite that proves the other half is not decoration."""

    def baseline(self):
        return next(c for c in load_cases(CASES_DIR) if c.id == "grounded-baseline")

    async def test_breaking_a_quote_drops_the_claim_and_fails_the_case(self):
        case = self.baseline()
        script = copy.deepcopy(case.model.research)
        script[0]["places"][0]["claims"][0]["quote"] = "The Opera House opens at 9:00am sharp."
        broken = replace(case, model=ModelScript(research=script, labels=case.model.labels))

        result = await run_case(broken)

        assert not result.passed
        assert result.metrics.claims == 2
        assert any("claims = 2" in failure for failure in result.failures)

    async def test_a_page_the_claims_do_not_quote_yields_no_claims_and_a_failed_run(self):
        case = self.baseline()
        empty = replace(
            case,
            corpus=(CorpusPage(url="https://example.gov.au/blank", text="Nothing quotable here."),),
        )
        result = await run_case(empty)

        assert not result.passed
        assert result.metrics.status is RunStatus.FAILED
        assert result.metrics.gap_rate == 1.0
        assert any("run ended failed" in warning for warning in result.warnings)

    async def test_an_impossible_threshold_fails_rather_than_being_skipped(self):
        case = self.baseline()
        strict = replace(
            case, expectations=(*case.expectations, Expectation(metric="claims", min=99))
        )
        result = await run_case(strict)
        assert not result.passed
        assert any("expected >= 99" in failure for failure in result.failures)

    async def test_unsupported_stages_are_refused_loudly(self):
        case = replace(self.baseline(), stages=(Stage.RESEARCH, Stage.VERIFY, Stage.PLAN))
        result = await run_case(case)

        assert not result.passed
        assert "plan" in result.error
        assert Stage.PLAN not in SUPPORTED_STAGES


class TestReport:
    async def test_payload_and_markdown_carry_the_verdict_and_the_numbers(self, tmp_path: Path):
        suite = await run_suite(load_cases(CASES_DIR))
        payload = to_payload(suite, label="v1", generated_at=datetime(2026, 8, 17, tzinfo=UTC))

        assert payload["schema"] == "periplus.evals.report/1"
        assert payload["label"] == "v1"
        assert payload["totals"]["cases"] == len(suite.cases)
        markdown = render_markdown(payload)
        assert "Periplus eval report" in markdown
        assert "grounded-baseline" in markdown

        json_path, markdown_path = write_reports(payload, directory=tmp_path / "out")
        assert json.loads(json_path.read_text())["passed"] is payload["passed"]
        assert markdown_path.read_text() == markdown

    async def test_a_failing_case_is_named_in_the_markdown(self):
        case = next(c for c in load_cases(CASES_DIR) if c.id == "grounded-baseline")
        strict = replace(case, expectations=(Expectation(metric="claims", min=99),))
        suite = await run_suite([strict])
        markdown = render_markdown(
            to_payload(suite, generated_at=datetime(2026, 8, 17, tzinfo=UTC))
        )

        assert "FAIL" in markdown
        assert "expected >= 99" in markdown

    def test_compare_names_the_direction_of_each_move(self):
        before = {
            "generated_at": "2026-08-16T00:00:00+00:00",
            "label": "before",
            "cases": [
                {
                    "case_id": "a",
                    "metrics": {"gap_rate": 0.5, "claims": 4, "cost_usd": 0.01},
                    "prompt_digests": {"research": "aaa"},
                }
            ],
        }
        after = copy.deepcopy(before)
        after["generated_at"] = "2026-08-17T00:00:00+00:00"
        after["label"] = "after"
        after["cases"][0]["metrics"] = {"gap_rate": 0.2, "claims": 4, "cost_usd": 0.02}
        after["cases"][0]["prompt_digests"] = {"research": "bbb"}

        diff = compare(before, after)

        assert "gap_rate" in diff
        assert "better" in diff
        assert "worse" in diff  # cost went up
        assert "System prompts changed for: a" in diff
        # claims did not move, so it must not appear as a row
        assert "| claims |" not in diff

    def test_compare_flags_a_case_that_vanished(self):
        before = {
            "generated_at": "x",
            "cases": [
                {"case_id": "a", "metrics": {"gap_rate": 0.5}, "prompt_digests": {}},
                {"case_id": "b", "metrics": {"gap_rate": 0.5}, "prompt_digests": {}},
            ],
        }
        after = {
            "generated_at": "y",
            "cases": [{"case_id": "a", "metrics": {"gap_rate": 0.1}, "prompt_digests": {}}],
        }
        assert "present only before: b" in compare(before, after)

    async def test_a_committed_report_is_byte_stable_apart_from_time(self, tmp_path: Path):
        """A baseline nobody can re-generate cleanly stops being used as a baseline."""
        cases = load_cases(CASES_DIR)
        stamp = datetime(2026, 8, 17, tzinfo=UTC)
        first = to_payload(await run_suite(cases), generated_at=stamp)
        second = to_payload(await run_suite(cases), generated_at=stamp)

        for payload in (first, second):
            payload["totals"]["wall_seconds"] = 0
            for case in payload["cases"]:
                case["wall_seconds"] = 0
                for key in [k for k in case["metrics"] if k.endswith("latency_ms")]:
                    case["metrics"][key] = 0
        assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)

"""Offline tests for Hermes, the orchestrator.

Most of these drive :class:`Hermes` with scripted, in-process fakes that implement the
narrow :class:`~periplus.orchestrator.stages.StageAdapter` protocol directly — the state
machine, budgets, retries, gates, cancellation and replay do not need a real Explorer or
Auditor to be exercised thoroughly. ``TestRealAdapters`` at the bottom wires the real
agents in through :class:`~periplus.orchestrator.stages.ResearchStageAdapter` and
:class:`~periplus.orchestrator.stages.VerificationStageAdapter` to prove the bridge
itself, using :class:`~periplus.llm.ScriptedClient` exactly as the agent tests do.

No network, no real sleeping: an injectable :class:`~periplus.orchestrator.FakeClock`
stands in for wall-clock budgets, and ``asyncio.sleep`` is monkeypatched where retry
backoff timing itself is under test.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime

import pytest

from periplus.agents.editor import EditorAgent
from periplus.agents.illustration import IllustrationAgent
from periplus.agents.research import ResearchAgent
from periplus.agents.verification import VerificationAgent
from periplus.llm import ScriptedClient, StagePolicy, Thinking
from periplus.media.images import GeneratedImage, ScriptedImages
from periplus.models import (
    Check,
    Claim,
    ClaimKind,
    ContentPiece,
    ContentSet,
    Evidence,
    ModelCall,
    ResearchBundle,
    Run,
    RunStatus,
    Stage,
    TripBrief,
    Verdict,
    VerifiedBundle,
)
from periplus.orchestrator import (
    STAGE_ORDER,
    EditorStageAdapter,
    FakeClock,
    Hermes,
    IllustrationStageAdapter,
    InMemoryArtifactStore,
    InvalidTransition,
    ReplayError,
    ResearchStageAdapter,
    ResourceUsage,
    RunBudget,
    StageFailure,
    StageOrderError,
    StageResult,
    StageRetryPolicy,
    SystemClock,
    TransientStageError,
    VerificationStageAdapter,
    edit_gate,
    research_gate,
    verification_gate,
)
from periplus.orchestrator.budget import BudgetTracker
from periplus.retrieval import RetrievalResult, SourceDocument

# --------------------------------------------------------------------------------------
# Fixtures shared across the state-machine tests
# --------------------------------------------------------------------------------------


def brief(**overrides) -> TripBrief:
    values = {
        "destination": "Lisbon",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 4),
    }
    values.update(overrides)
    return TripBrief(**values)


def evidence(**overrides) -> Evidence:
    values = {
        "id": "ev-1",
        "url": "https://museum.example/visit",
        "snippet": "Open daily from 9am.",
    }
    values.update(overrides)
    return Evidence(**values)


def claim(**overrides) -> Claim:
    values = {
        "id": "claim-1",
        "subject": "Museum",
        "text": "The museum opens at 9am.",
        "kind": ClaimKind.HOURS,
        "evidence_ids": ["ev-1"],
    }
    values.update(overrides)
    return Claim(**values)


def checked_claim(**overrides) -> Claim:
    made = claim(**overrides)
    made.check = Check(verdict=Verdict.SUPPORTED, confidence=0.9, reason="The source states it.")
    return made


def bundle(*, claims: list[Claim] | None = None, **overrides) -> ResearchBundle:
    values = {
        "brief_id": "brief-1",
        "claims": claims if claims is not None else [claim()],
        "evidence": [evidence()],
        "queries_run": ["q"],
    }
    values.update(overrides)
    return ResearchBundle(**values)


def verified(*, claims: list[Claim] | None = None, **overrides) -> VerifiedBundle:
    payload = bundle(claims=claims).model_dump()
    payload.update(overrides)
    return VerifiedBundle(**payload)


def result(artifact: object, *, usage: ResourceUsage | None = None, calls=None) -> StageResult:
    return StageResult(artifact=artifact, usage=usage or ResourceUsage(), calls=list(calls or []))


class FakeStage:
    """A scripted :class:`StageAdapter`. Replies are consumed in order; an ``Exception``
    entry is raised in place rather than returned.
    """

    def __init__(self, stage: Stage, replies: list) -> None:
        self.stage = stage
        self._replies = list(replies)
        self.calls = 0
        self.received: list[object] = []

    def push(self, reply) -> None:
        self._replies.append(reply)

    async def run(self, stage_input: object) -> StageResult:
        self.calls += 1
        self.received.append(stage_input)
        if not self._replies:
            raise AssertionError(f"{self.stage.value} stage ran out of scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class FlippingStage(FakeStage):
    """A fake stage that flips a shared flag as a side effect of running — used to model
    "cancellation requested while a stage was in flight" without real concurrency.
    """

    def __init__(self, stage: Stage, replies: list, flag: list[bool]) -> None:
        super().__init__(stage, replies)
        self._flag = flag

    async def run(self, stage_input: object) -> StageResult:
        self._flag[0] = True
        return await super().run(stage_input)


# --------------------------------------------------------------------------------------
# Clock, budget and artifact store — unit tests for the injectable pieces
# --------------------------------------------------------------------------------------


class TestFakeClock:
    def test_advance_moves_both_now_and_monotonic(self):
        clock = FakeClock(start=datetime(2026, 1, 1, tzinfo=UTC), start_monotonic=100.0)
        clock.advance(30)
        assert clock.now() == datetime(2026, 1, 1, 0, 0, 30, tzinfo=UTC)
        assert clock.monotonic() == 130.0

    def test_cannot_go_backwards(self):
        clock = FakeClock()
        with pytest.raises(ValueError):
            clock.advance(-1)


class TestBudgetTracker:
    def test_reports_no_breach_when_under_every_ceiling(self):
        tracker = BudgetTracker(RunBudget(max_queries=5, max_tokens=100), SystemClock())
        tracker.charge(ResourceUsage(queries=1, tokens=10))
        assert tracker.exceeded() is None

    def test_reports_the_first_breached_ceiling(self):
        tracker = BudgetTracker(RunBudget(max_queries=1, max_tokens=100), SystemClock())
        tracker.charge(ResourceUsage(queries=2, tokens=10))
        assert tracker.exceeded() is not None
        assert "queries" in tracker.exceeded()

    def test_wall_clock_uses_the_injected_clock(self):
        clock = FakeClock()
        tracker = BudgetTracker(RunBudget(max_wall_clock_seconds=5), clock)
        clock.advance(10)
        assert tracker.exceeded() is not None
        assert "wall clock" in tracker.exceeded()

    def test_unbounded_budget_never_breaches(self):
        tracker = BudgetTracker(RunBudget(), SystemClock())
        tracker.charge(ResourceUsage(queries=10_000, fetches=10_000, tokens=10_000))
        assert tracker.exceeded() is None


class TestArtifactStore:
    def test_put_then_get_roundtrips(self):
        store = InMemoryArtifactStore()
        made = bundle()
        store.put("run-1", Stage.RESEARCH, 1, made)
        assert store.get("run-1", Stage.RESEARCH, 1) is made

    def test_get_of_a_missing_attempt_is_none(self):
        store = InMemoryArtifactStore()
        assert store.get("run-1", Stage.RESEARCH, 1) is None

    def test_put_never_overwrites_a_prior_attempt(self):
        store = InMemoryArtifactStore()
        store.put("run-1", Stage.RESEARCH, 1, bundle())
        with pytest.raises(ValueError):
            store.put("run-1", Stage.RESEARCH, 1, bundle())

    def test_mark_passed_requires_a_retained_artifact(self):
        store = InMemoryArtifactStore()
        with pytest.raises(ValueError):
            store.mark_passed("run-1", Stage.RESEARCH, 1)

    def test_latest_passed_tracks_the_highest_passing_attempt(self):
        store = InMemoryArtifactStore()
        first, second = bundle(), bundle()
        store.put("run-1", Stage.RESEARCH, 1, first)
        store.put("run-1", Stage.RESEARCH, 2, second)
        store.mark_passed("run-1", Stage.RESEARCH, 2)
        store.mark_passed("run-1", Stage.RESEARCH, 1)  # out of order; must not regress
        retained = store.latest_passed("run-1", Stage.RESEARCH)
        assert retained.attempt == 2
        assert retained.artifact is second

    def test_latest_passed_is_none_when_nothing_passed(self):
        store = InMemoryArtifactStore()
        store.put("run-1", Stage.RESEARCH, 1, bundle())
        assert store.latest_passed("run-1", Stage.RESEARCH) is None


class TestStageRetryPolicy:
    def test_rejects_less_than_one_attempt(self):
        with pytest.raises(ValueError):
            StageRetryPolicy(max_attempts=0)

    def test_rejects_negative_backoff(self):
        with pytest.raises(ValueError):
            StageRetryPolicy(backoff_seconds=-1)


class TestDefaultGates:
    def test_research_gate_rejects_no_claims(self):
        assert research_gate(bundle(claims=[])) == "research produced no grounded claims"

    def test_research_gate_passes_with_claims(self):
        assert research_gate(bundle()) is None

    def test_verification_gate_rejects_no_claims(self):
        assert verification_gate(verified(claims=[])) == "verification received no claims"

    def test_verification_gate_rejects_unverified_claims(self):
        assert verification_gate(verified(claims=[claim()])) == "not every claim received a verdict"

    def test_verification_gate_passes_when_every_claim_is_checked(self):
        assert verification_gate(verified(claims=[checked_claim()])) is None

    def test_edit_gate_rejects_no_pieces(self):
        empty = ContentSet(itinerary_id="itinerary-1")
        assert edit_gate(empty) == "writing produced no content pieces"

    def test_edit_gate_passes_with_pieces(self):
        non_empty = ContentSet(
            itinerary_id="itinerary-1",
            pieces=[ContentPiece(kind="captions", body="See the museum!")],
        )
        assert edit_gate(non_empty) is None


# --------------------------------------------------------------------------------------
# Construction: strict stage order
# --------------------------------------------------------------------------------------


class TestStageConfiguration:
    def test_rejects_a_gap_in_stage_order(self):
        with pytest.raises(StageOrderError):
            Hermes(
                adapters={
                    Stage.RESEARCH: FakeStage(Stage.RESEARCH, []),
                    Stage.PLAN: FakeStage(Stage.PLAN, []),
                }
            )

    def test_rejects_starting_after_research(self):
        with pytest.raises(StageOrderError):
            Hermes(adapters={Stage.VERIFY: FakeStage(Stage.VERIFY, [])})

    def test_rejects_empty_adapters(self):
        with pytest.raises(StageOrderError):
            Hermes(adapters={})

    def test_accepts_a_single_stage_pipeline(self):
        Hermes(adapters={Stage.RESEARCH: FakeStage(Stage.RESEARCH, [])})

    def test_accepts_a_contiguous_prefix(self):
        Hermes(
            adapters={
                Stage.RESEARCH: FakeStage(Stage.RESEARCH, []),
                Stage.VERIFY: FakeStage(Stage.VERIFY, []),
            }
        )

    def test_stage_order_constant_matches_the_documented_pipeline(self):
        assert STAGE_ORDER == (
            Stage.RESEARCH,
            Stage.VERIFY,
            Stage.PLAN,
            Stage.WRITE,
            Stage.EDIT,
            Stage.ILLUSTRATE,
        )


# --------------------------------------------------------------------------------------
# Happy path, gates, retries and failures
# --------------------------------------------------------------------------------------


class TestHappyPath:
    async def test_both_stages_succeed_and_the_run_completes(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        verify_fake = FakeStage(Stage.VERIFY, [result(verified(claims=[checked_claim()]))])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake})

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert run.research is not None
        assert run.verified is not None
        statuses = [s.status for s in run.stages]
        assert statuses == [RunStatus.SUCCEEDED, RunStatus.SUCCEEDED]
        assert [s.attempt for s in run.stages] == [1, 1]
        assert verify_fake.received == [run.research]

    async def test_a_single_stage_pipeline_completes_without_verify(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert run.verified is None
        assert len(run.stages) == 1

    async def test_exposes_the_live_run_once_before_the_first_stage_completes(self):
        stage_started = asyncio.Event()
        release_stage = asyncio.Event()
        observed: list[Run] = []

        class BlockingResearch:
            stage = Stage.RESEARCH

            async def run(self, stage_input: object) -> StageResult:
                stage_started.set()
                await release_stage.wait()
                return result(bundle())

        hermes = Hermes(adapters={Stage.RESEARCH: BlockingResearch()})
        task = asyncio.create_task(hermes.start(brief(), on_run_created=observed.append))

        await stage_started.wait()
        assert len(observed) == 1
        assert observed[0].status is RunStatus.RUNNING
        assert observed[0].stages[-1].status is RunStatus.RUNNING

        release_stage.set()
        completed = await task
        assert observed == [completed]


class TestGates:
    async def test_a_rejected_artifact_fails_the_stage_and_stops_downstream_work(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(claims=[]))])
        verify_fake = FakeStage(Stage.VERIFY, [])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake})

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert len(run.stages) == 1
        assert run.stages[0].error is not None
        assert run.stages[0].error.startswith("gate:")
        assert verify_fake.calls == 0

    async def test_a_gate_can_be_disabled_explicitly(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(claims=[]))])
        verify_fake = FakeStage(Stage.VERIFY, [result(verified(claims=[checked_claim()]))])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake},
            gates={Stage.RESEARCH: None},
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert verify_fake.calls == 1


class TestTransientRetry:
    async def test_retries_up_to_the_bound_then_succeeds(self):
        research_fake = FakeStage(
            Stage.RESEARCH,
            [TransientStageError("blip"), TransientStageError("blip2"), result(bundle())],
        )
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=3),
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert [s.status for s in run.stages] == [
            RunStatus.FAILED,
            RunStatus.FAILED,
            RunStatus.SUCCEEDED,
        ]
        assert [s.attempt for s in run.stages] == [1, 2, 3]
        assert all(s.error.startswith("transient:") for s in run.stages[:2])

    async def test_stops_once_the_bound_is_exhausted(self):
        research_fake = FakeStage(
            Stage.RESEARCH, [TransientStageError("a"), TransientStageError("b")]
        )
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=2),
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert len(run.stages) == 2
        assert research_fake.calls == 2

    async def test_retry_policy_can_differ_per_stage(self):
        research_fake = FakeStage(Stage.RESEARCH, [TransientStageError("x"), result(bundle())])
        verify_fake = FakeStage(Stage.VERIFY, [TransientStageError("y")])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake},
            retry={
                Stage.RESEARCH: StageRetryPolicy(max_attempts=2),
                Stage.VERIFY: StageRetryPolicy(max_attempts=1),
            },
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        research_attempts = [s for s in run.stages if s.stage is Stage.RESEARCH]
        verify_attempts = [s for s in run.stages if s.stage is Stage.VERIFY]
        assert len(research_attempts) == 2
        assert research_attempts[-1].status is RunStatus.SUCCEEDED
        assert len(verify_attempts) == 1
        assert verify_attempts[-1].status is RunStatus.FAILED

    async def test_backoff_scales_with_attempt_number(self, monkeypatch):
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        monkeypatch.setattr("periplus.orchestrator.hermes.asyncio.sleep", fake_sleep)

        research_fake = FakeStage(
            Stage.RESEARCH,
            [TransientStageError("a"), TransientStageError("b"), result(bundle())],
        )
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=3, backoff_seconds=2.0),
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert sleeps == [2.0, 4.0]


class TestLogicalFailure:
    async def test_is_never_retried_even_with_a_generous_bound(self):
        research_fake = FakeStage(Stage.RESEARCH, [StageFailure("brief is unusable")])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=5),
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert len(run.stages) == 1
        assert research_fake.calls == 1
        assert run.stages[0].error.startswith("logical:")

    async def test_unexpected_exception_becomes_an_explicit_terminal_failure(self):
        research_fake = FakeStage(Stage.RESEARCH, [ValueError("broken adapter")])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert run.finished_at is not None
        assert run.stages[0].status is RunStatus.FAILED
        assert run.stages[0].error == "internal: ValueError: broken adapter"


# --------------------------------------------------------------------------------------
# Budgets and cancellation
# --------------------------------------------------------------------------------------


class TestBudgetExhaustion:
    async def test_budget_exhausted_during_backoff_gates_off_the_retry_before_it_starts(
        self, monkeypatch
    ):
        # Resource usage is only charged once a stage attempt actually returns, and that
        # charge is checked immediately (see TestHardCeilingOnCompletion) — so the one
        # place left where a stage attempt can still be gated off *before it starts* is
        # wall clock spent waiting out backoff between retries of the same stage.
        clock = FakeClock()

        async def fake_sleep(seconds: float) -> None:
            clock.advance(seconds)

        monkeypatch.setattr("periplus.orchestrator.hermes.asyncio.sleep", fake_sleep)

        research_fake = FakeStage(Stage.RESEARCH, [TransientStageError("blip"), result(bundle())])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=2, backoff_seconds=10),
            budget=RunBudget(max_wall_clock_seconds=5),
            clock=clock,
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert research_fake.calls == 1  # the retry was gated off, never actually run
        assert len(run.stages) == 2
        assert run.stages[0].error.startswith("transient:")
        retry_entry = run.stages[1]
        assert retry_entry.status is RunStatus.FAILED
        assert retry_entry.error.startswith("budget_exceeded:")
        assert retry_entry.started_at is None  # gated off before the retry ever started

    async def test_wall_clock_budget_is_driven_by_the_injected_clock(self):
        clock = FakeClock()

        class SlowStage(FakeStage):
            async def run(self, stage_input: object) -> StageResult:
                clock.advance(10)
                return await super().run(stage_input)

        # Research is instant and stays well under the clock budget; verify is the one
        # that burns the clock, so verify's own attempt is what must fail.
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        verify_fake = SlowStage(Stage.VERIFY, [result(verified(claims=[checked_claim()]))])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake},
            budget=RunBudget(max_wall_clock_seconds=5),
            clock=clock,
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        verify_entry = next(s for s in run.stages if s.stage is Stage.VERIFY)
        assert verify_entry.status is RunStatus.FAILED
        assert verify_entry.error.startswith("budget_exceeded:")
        assert "wall clock" in verify_entry.error
        assert verify_entry.started_at is not None  # it ran; the clock tripped after
        assert run.verified is None


class TestHardCeilingOnCompletion:
    """A stage that pushes cumulative usage over budget must fail its own attempt, not
    just block whatever stage would have come next — otherwise a single-stage or
    final-stage pipeline can succeed having blown a configured ceiling, since there is no
    downstream stage left to gate off.
    """

    @pytest.mark.parametrize(
        ("usage", "budget", "reason_fragment"),
        [
            (ResourceUsage(tokens=1000), RunBudget(max_tokens=500), "tokens"),
            (ResourceUsage(queries=10), RunBudget(max_queries=5), "queries"),
            (ResourceUsage(fetches=10), RunBudget(max_fetches=5), "fetches"),
        ],
    )
    async def test_a_single_stage_pipeline_fails_when_it_overshoots_its_own_budget(
        self, usage, budget, reason_fragment
    ):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(), usage=usage)])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake}, budget=budget)

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        assert len(run.stages) == 1
        research_entry = run.stages[0]
        assert research_entry.status is RunStatus.FAILED
        assert research_entry.error.startswith("budget_exceeded:")
        assert reason_fragment in research_entry.error
        assert research_entry.started_at is not None  # it ran; only then did it fail
        assert run.research is None  # never attached: the stage did not succeed
        # retained for inspection, but never a valid replay boundary
        assert hermes.artifacts.get(run.id, Stage.RESEARCH, 1) is not None
        assert hermes.artifacts.latest_passed(run.id, Stage.RESEARCH) is None

    async def test_the_final_stage_of_a_multi_stage_pipeline_fails_when_it_overshoots(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        heavy_usage = ResourceUsage(tokens=1000)
        verify_fake = FakeStage(
            Stage.VERIFY, [result(verified(claims=[checked_claim()]), usage=heavy_usage)]
        )
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake},
            budget=RunBudget(max_tokens=500),
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.FAILED
        verify_entry = next(s for s in run.stages if s.stage is Stage.VERIFY)
        assert verify_entry.status is RunStatus.FAILED
        assert verify_entry.error.startswith("budget_exceeded:")
        assert run.research is not None  # the earlier, in-budget stage is unaffected
        assert run.verified is None
        assert hermes.artifacts.latest_passed(run.id, Stage.VERIFY) is None

    async def test_an_overshot_attempt_is_never_a_valid_replay_boundary(self):
        heavy_usage = ResourceUsage(tokens=1000)
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(), usage=heavy_usage)])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: FakeStage(Stage.VERIFY, [])},
            budget=RunBudget(max_tokens=500),
        )

        run = await hermes.start(brief())
        assert run.status is RunStatus.FAILED

        with pytest.raises(ReplayError):
            await hermes.replay(run, from_stage=Stage.VERIFY)


class TestCancellation:
    async def test_cancellation_before_the_first_stage_stops_everything(self):
        research_fake = FakeStage(Stage.RESEARCH, [])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief(), is_cancelled=lambda: True)

        assert run.status is RunStatus.CANCELLED
        assert research_fake.calls == 0
        assert len(run.stages) == 1
        assert run.stages[0].status is RunStatus.CANCELLED
        assert run.stages[0].started_at is None

    async def test_cancellation_requested_mid_run_stops_downstream_work(self):
        flag = [False]
        research_fake = FlippingStage(Stage.RESEARCH, [result(bundle())], flag)
        verify_fake = FakeStage(Stage.VERIFY, [])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake})

        run = await hermes.start(brief(), is_cancelled=lambda: flag[0])

        assert run.status is RunStatus.CANCELLED
        assert run.research is not None  # research itself completed
        assert verify_fake.calls == 0


# --------------------------------------------------------------------------------------
# Explicit invalid transitions
# --------------------------------------------------------------------------------------


class TestInvalidTransitions:
    async def test_replay_rejects_a_run_that_is_still_in_flight(self):
        hermes = Hermes(adapters={Stage.RESEARCH: FakeStage(Stage.RESEARCH, [])})
        run = Run(brief=brief(), status=RunStatus.RUNNING)

        with pytest.raises(InvalidTransition) as excinfo:
            await hermes.replay(run, from_stage=Stage.RESEARCH)

        assert excinfo.value.from_status is RunStatus.RUNNING
        assert excinfo.value.to_status is RunStatus.RUNNING


# --------------------------------------------------------------------------------------
# Replay: resuming from a retained boundary without rerunning earlier stages
# --------------------------------------------------------------------------------------


class TestReplay:
    async def test_replay_from_the_first_stage_reruns_from_the_brief(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief())
        assert run.status is RunStatus.SUCCEEDED

        research_fake.push(result(bundle()))
        replayed = await hermes.replay(run, from_stage=Stage.RESEARCH)

        assert replayed is run
        assert replayed.status is RunStatus.SUCCEEDED
        assert research_fake.calls == 2
        assert [s.attempt for s in run.stages if s.stage is Stage.RESEARCH] == [1, 2]

    async def test_a_rejected_artifact_is_retained_and_never_overwritten(self):
        rejected_bundle = verified(claims=[claim()])  # unverified: fails the gate
        accepted_bundle = verified(claims=[checked_claim()])
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        verify_fake = FakeStage(Stage.VERIFY, [result(rejected_bundle)])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake})

        run = await hermes.start(brief())
        assert run.status is RunStatus.FAILED
        assert hermes.artifacts.get(run.id, Stage.VERIFY, 1) is rejected_bundle
        assert hermes.artifacts.latest_passed(run.id, Stage.VERIFY) is None

        verify_fake.push(result(accepted_bundle))
        replayed = await hermes.replay(run, from_stage=Stage.VERIFY)

        assert replayed.status is RunStatus.SUCCEEDED
        assert research_fake.calls == 1  # earlier stage was never rerun
        verify_attempts = [s for s in run.stages if s.stage is Stage.VERIFY]
        assert [s.status for s in verify_attempts] == [RunStatus.FAILED, RunStatus.SUCCEEDED]
        # the rejected attempt's artifact is exactly what it always was
        assert hermes.artifacts.get(run.id, Stage.VERIFY, 1) is rejected_bundle
        assert hermes.artifacts.get(run.id, Stage.VERIFY, 2) is accepted_bundle
        assert hermes.artifacts.latest_passed(run.id, Stage.VERIFY).attempt == 2
        assert run.verified is accepted_bundle

    async def test_replay_from_a_stage_not_configured_raises(self):
        hermes = Hermes(adapters={Stage.RESEARCH: FakeStage(Stage.RESEARCH, [])})
        run = Run(brief=brief(), status=RunStatus.FAILED)

        with pytest.raises(ReplayError):
            await hermes.replay(run, from_stage=Stage.VERIFY)

    async def test_replay_without_a_retained_boundary_raises(self):
        hermes = Hermes(
            adapters={
                Stage.RESEARCH: FakeStage(Stage.RESEARCH, []),
                Stage.VERIFY: FakeStage(Stage.VERIFY, []),
            }
        )
        run = Run(brief=brief(), status=RunStatus.FAILED)

        with pytest.raises(ReplayError):
            await hermes.replay(run, from_stage=Stage.VERIFY)


# --------------------------------------------------------------------------------------
# Replay budget semantics: a RunBudget is a ceiling for the whole Run, replays included —
# a replay draws down what the run already spent rather than starting over.
# --------------------------------------------------------------------------------------


class TestReplayRetryBudget:
    async def test_replay_gets_a_fresh_retry_bound_but_keeps_global_attempt_ids(self):
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            retry=StageRetryPolicy(max_attempts=3),
        )
        run = await hermes.start(brief())
        assert run.status is RunStatus.SUCCEEDED

        research_fake.push(TransientStageError("one"))
        research_fake.push(TransientStageError("two"))
        research_fake.push(result(bundle()))
        replayed = await hermes.replay(run, from_stage=Stage.RESEARCH)

        assert replayed.status is RunStatus.SUCCEEDED
        assert [entry.attempt for entry in replayed.stages] == [1, 2, 3, 4]
        assert [entry.status for entry in replayed.stages[-3:]] == [
            RunStatus.FAILED,
            RunStatus.FAILED,
            RunStatus.SUCCEEDED,
        ]

    async def test_replay_clears_the_old_finished_timestamp_while_running(self):
        clock = FakeClock()
        flag: list[datetime | None] = []

        class InspectingStage(FakeStage):
            async def run(self, stage_input: object) -> StageResult:
                flag.append(run.finished_at)
                return await super().run(stage_input)

        initial = FakeStage(Stage.RESEARCH, [result(bundle())])
        hermes = Hermes(adapters={Stage.RESEARCH: initial}, clock=clock)
        run = await hermes.start(brief())
        assert run.finished_at is not None

        inspecting = InspectingStage(Stage.RESEARCH, [result(bundle())])
        hermes._adapters[Stage.RESEARCH] = inspecting
        await hermes.replay(run, from_stage=Stage.RESEARCH)

        assert flag == [None]


class TestReplayBudgetIsCumulative:
    async def test_replay_draws_down_the_same_run_wide_budget_rather_than_a_fresh_one(self):
        research_fake = FakeStage(
            Stage.RESEARCH, [result(bundle(), usage=ResourceUsage(tokens=500))]
        )
        rejected = verified(claims=[claim()])  # unverified: fails the verification gate
        verify_fake = FakeStage(Stage.VERIFY, [result(rejected, usage=ResourceUsage(tokens=200))])
        hermes = Hermes(
            adapters={Stage.RESEARCH: research_fake, Stage.VERIFY: verify_fake},
            budget=RunBudget(max_tokens=800),
        )

        run = await hermes.start(brief())
        assert run.status is RunStatus.FAILED
        assert run.stages[-1].error.startswith("gate:")  # rejected on content, not budget

        # A second verify attempt would stay under budget computed fresh (200 < 800),
        # but the run's true cumulative total — 500 + 200 + 200 — does not.
        accepted = verified(claims=[checked_claim()])
        verify_fake.push(result(accepted, usage=ResourceUsage(tokens=200)))
        replayed = await hermes.replay(run, from_stage=Stage.VERIFY)

        assert replayed.status is RunStatus.FAILED
        retry_entry = [s for s in run.stages if s.stage is Stage.VERIFY][-1]
        assert retry_entry.status is RunStatus.FAILED
        assert retry_entry.error.startswith("budget_exceeded:")
        assert retry_entry.started_at is not None  # it ran; only then did it overshoot
        assert hermes.artifacts.latest_passed(run.id, Stage.VERIFY) is None
        assert run.verified is None

    async def test_a_stricter_budget_on_replay_gates_off_the_stage_before_it_starts(self):
        # A run that finished under one Hermes instance's (unbounded) budget is later
        # replayed under a different, stricter one — e.g. a persisted run resumed by a
        # process configured with a tighter ceiling. The stage never gets to run.
        shared_artifacts = InMemoryArtifactStore()
        research_fake = FakeStage(
            Stage.RESEARCH, [result(bundle(), usage=ResourceUsage(tokens=1000))]
        )
        lenient = Hermes(adapters={Stage.RESEARCH: research_fake}, artifacts=shared_artifacts)

        run = await lenient.start(brief())
        assert run.status is RunStatus.SUCCEEDED

        verify_fake = FakeStage(Stage.VERIFY, [])
        strict = Hermes(
            adapters={Stage.RESEARCH: FakeStage(Stage.RESEARCH, []), Stage.VERIFY: verify_fake},
            artifacts=shared_artifacts,
            budget=RunBudget(max_tokens=500),
        )

        replayed = await strict.replay(run, from_stage=Stage.VERIFY)

        assert replayed.status is RunStatus.FAILED
        assert verify_fake.calls == 0
        verify_entry = next(s for s in run.stages if s.stage is Stage.VERIFY)
        assert verify_entry.status is RunStatus.FAILED
        assert verify_entry.error.startswith("budget_exceeded:")
        assert verify_entry.started_at is None  # gated off before it ever started

    async def test_replaying_long_after_a_run_finished_is_not_charged_for_the_idle_gap(self):
        clock = FakeClock()
        shared_artifacts = InMemoryArtifactStore()
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle())])
        first = Hermes(
            adapters={Stage.RESEARCH: research_fake},
            budget=RunBudget(max_wall_clock_seconds=5),
            clock=clock,
            artifacts=shared_artifacts,
        )
        run = await first.start(brief())
        assert run.status is RunStatus.SUCCEEDED

        clock.advance(10_000)  # time passes between the two calls; no stage is running

        verify_fake = FakeStage(Stage.VERIFY, [result(verified(claims=[checked_claim()]))])
        second = Hermes(
            adapters={Stage.RESEARCH: FakeStage(Stage.RESEARCH, []), Stage.VERIFY: verify_fake},
            budget=RunBudget(max_wall_clock_seconds=5),
            clock=clock,
            artifacts=shared_artifacts,
        )

        replayed = await second.replay(run, from_stage=Stage.VERIFY)

        assert replayed.status is RunStatus.SUCCEEDED
        assert verify_fake.calls == 1


# --------------------------------------------------------------------------------------
# Audit trail
# --------------------------------------------------------------------------------------


class TestAuditTrail:
    async def test_model_calls_are_attached_to_their_stage_and_summed_on_the_run(self):
        call = ModelCall(
            stage=Stage.RESEARCH,
            model="scripted",
            prompt_hash="deadbeef",
            prompt_tokens=10,
            completion_tokens=5,
        )
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(), calls=[call])])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief())

        assert run.stages[0].calls == [call]
        assert run.total_tokens == 15

    async def test_resource_usage_is_recorded_on_the_stage_run(self):
        usage = ResourceUsage(queries=3, fetches=5, tokens=15)
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(), usage=usage)])
        hermes = Hermes(adapters={Stage.RESEARCH: research_fake})

        run = await hermes.start(brief())

        stage_run = run.stages[0]
        assert (stage_run.queries, stage_run.fetches, stage_run.tokens) == (3, 5, 15)


# --------------------------------------------------------------------------------------
# Integration: the real agents through the narrow adapters
# --------------------------------------------------------------------------------------


class _FixedRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self._result = result

    async def gather(self, queries, *, subject=None, seen_urls=None) -> RetrievalResult:
        return self._result


class TestResearchStageAdapterBudgetCharging:
    """ResearchStageAdapter must charge exact retrieval-attempt counts, not an
    approximation from what survived into the bundle.
    """

    async def test_charges_exact_attempts_rather_than_an_approximation(self):
        quote = "The Harbour Museum opens daily at 9am."
        document = SourceDocument(
            url="https://harbourmuseum.example/visit",
            title="Visitor information",
            text=quote,
            query="museum hours",
        )
        # Two queries were attempted but only one succeeded; three pages were fetched but
        # only one document made it through (the others were blocked or rejected) — the
        # old approximation (len(queries_run), distinct accepted-evidence URLs) would
        # charge 1 and 1, undercounting both.
        retrieval = RetrievalResult(
            documents=[document],
            queries_run=["museum hours"],
            failures=["search 'bad query': boom", "blocked.example: 403"],
            queries_attempted=2,
            fetch_attempts=3,
        )
        research_reply = json.dumps(
            {
                "places": [
                    {
                        "name": "Harbour Museum",
                        "kind": "museum",
                        "summary": "A landmark relevant to the brief.",
                        "tags": [],
                        "claims": [
                            {
                                "subject": "Harbour Museum",
                                "text": "The Harbour Museum opens daily at 9am.",
                                "kind": "description",
                                "source_index": 0,
                                "quote": quote,
                            }
                        ],
                    }
                ],
                "gaps": [],
            }
        )
        research_agent = ResearchAgent(
            llm=ScriptedClient([research_reply], max_attempts=1),
            retriever=_FixedRetriever(retrieval),
            policy=StagePolicy(model="scripted", thinking=Thinking.HIGH),
        )
        adapter = ResearchStageAdapter(research_agent)

        result = await adapter.run(brief())

        assert result.usage.queries == 2
        assert result.usage.fetches == 3

    async def test_charges_failed_and_unselected_fetch_attempts_even_with_no_evidence(self):
        # Every fetch fails or is rejected; nothing survives into the bundle at all.
        retrieval = RetrievalResult(
            queries_run=[], failures=["boom"], queries_attempted=1, fetch_attempts=4
        )
        research_agent = ResearchAgent(
            llm=ScriptedClient([]),
            retriever=_FixedRetriever(retrieval),
            policy=StagePolicy(model="scripted"),
        )
        adapter = ResearchStageAdapter(research_agent)

        result = await adapter.run(brief())

        assert result.usage.queries == 1
        assert result.usage.fetches == 4
        assert result.artifact.claims == []


class TestRealAdapters:
    async def test_research_then_verify_through_hermes_end_to_end(self):
        quote = "The Harbour Museum opens daily at 9am."
        document = SourceDocument(
            url="https://harbourmuseum.example/visit",
            title="Visitor information",
            text=quote,
            query="museum hours",
        )
        retrieval = RetrievalResult(documents=[document], queries_run=["museum hours"])

        research_reply = json.dumps(
            {
                "places": [
                    {
                        "name": "Harbour Museum",
                        "kind": "museum",
                        "summary": "A landmark relevant to the brief.",
                        "tags": [],
                        "claims": [
                            {
                                "subject": "Harbour Museum",
                                "text": "The Harbour Museum opens daily at 9am.",
                                # DESCRIPTION never expires, so the assertions below do
                                # not depend on the real wall-clock date at test time.
                                "kind": "description",
                                "source_index": 0,
                                "quote": quote,
                            }
                        ],
                    }
                ],
                "gaps": [],
            }
        )
        research_agent = ResearchAgent(
            llm=ScriptedClient([research_reply], max_attempts=1),
            retriever=_FixedRetriever(retrieval),
            policy=StagePolicy(model="scripted", thinking=Thinking.HIGH),
        )

        def verify_reply(messages):
            payload = json.loads(messages[-1].content.split("\n", 1)[1])
            group = payload[0]
            return json.dumps(
                {
                    "decisions": [
                        {
                            "claim_id": group["claim"]["id"],
                            "verdict": "supported",
                            "confidence": 0.9,
                            "reason": "The evidence directly states the opening time.",
                            "supporting_evidence_ids": group["claim"]["evidence_ids"],
                            "conflicting_evidence_ids": [],
                        }
                    ]
                }
            )

        verification_agent = VerificationAgent(
            llm=ScriptedClient([verify_reply], max_attempts=1),
            policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        )

        clock = FakeClock(start=datetime(2026, 8, 10, tzinfo=UTC))
        hermes = Hermes(
            adapters={
                Stage.RESEARCH: ResearchStageAdapter(research_agent),
                Stage.VERIFY: VerificationStageAdapter(verification_agent, clock=clock),
            },
            clock=clock,
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert isinstance(run.research, ResearchBundle)
        assert isinstance(run.verified, VerifiedBundle)
        assert len(run.verified.claims) == 1
        assert run.verified.claims[0].verdict is Verdict.SUPPORTED

        research_run = run.stage_run(Stage.RESEARCH)
        verify_run = run.stage_run(Stage.VERIFY)
        assert research_run is not None and len(research_run.calls) == 1
        assert verify_run is not None and len(verify_run.calls) == 1
        assert run.total_tokens > 0
        # Hermes wires each real adapter's progress callback into the live StageRun it
        # just appended (see _progress_reporter); a single-document, single-claim run
        # produces exactly one tick each, landing at "done".
        assert (research_run.progress_current, research_run.progress_total) == (1, 1)
        assert (verify_run.progress_current, verify_run.progress_total) == (1, 1)
        # Same wiring, for the bundle-progress callback (see _bundle_progress_reporter):
        # one claim grounded in one piece of evidence, ticked once.
        assert (research_run.live_claims, research_run.live_evidence) == (1, 1)


# --------------------------------------------------------------------------------------
# Edit: the stage after write, wired the same way as every other real adapter
# --------------------------------------------------------------------------------------


def content_set(*, pieces: list[ContentPiece], claims: list[Claim]) -> ContentSet:
    return ContentSet(itinerary_id="itinerary-1", pieces=pieces, claims=claims)


class TestEditorStageAdapter:
    async def test_wraps_the_agent_and_charges_tokens(self):
        edited_claim = claim(id="c1", subject="Harbour Museum")
        content = content_set(
            pieces=[ContentPiece(kind="captions", body="See the museum!", claim_ids=["c1"])],
            claims=[edited_claim],
        )
        edit_reply = json.dumps(
            {
                "pieces": [
                    {
                        "piece_id": content.pieces[0].id,
                        "drop": False,
                        "reason": None,
                        "title": None,
                        "body": "The Harbour Museum is free to visit.",
                        "claim_ids": ["c1"],
                    }
                ]
            }
        )
        editor_agent = EditorAgent(
            llm=ScriptedClient([edit_reply], max_attempts=1),
            policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        )
        adapter = EditorStageAdapter(editor_agent)

        result = await adapter.run(content)

        assert result.artifact.pieces[0].body == "The Harbour Museum is free to visit."
        assert result.artifact.edited is True
        assert result.usage.tokens > 0
        assert len(result.calls) == 1

    async def test_edit_joins_a_six_stage_hermes_run_between_write_and_illustrate(self):
        edited_claim = checked_claim(id="c1", subject="Harbour Museum")
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(claims=[edited_claim]))])
        verify_fake = FakeStage(Stage.VERIFY, [result(verified(claims=[edited_claim]))])
        drafted = content_set(
            pieces=[ContentPiece(kind="captions", body="See the museum!", claim_ids=["c1"])],
            claims=[edited_claim],
        )
        plan_fake = FakeStage(Stage.PLAN, [result(object())])
        write_fake = FakeStage(Stage.WRITE, [result(drafted)])
        edit_reply = json.dumps(
            {
                "pieces": [
                    {
                        "piece_id": drafted.pieces[0].id,
                        "drop": False,
                        "reason": None,
                        "title": None,
                        "body": "Visit the Harbour Museum: free entry, open daily.",
                        "claim_ids": ["c1"],
                    }
                ]
            }
        )
        editor_agent = EditorAgent(
            llm=ScriptedClient([edit_reply], max_attempts=1),
            policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
        )
        edit_adapter = EditorStageAdapter(editor_agent)
        illustrate_adapter = IllustrationStageAdapter(IllustrationAgent(provider=None))

        hermes = Hermes(
            adapters={
                Stage.RESEARCH: research_fake,
                Stage.VERIFY: verify_fake,
                Stage.PLAN: plan_fake,
                Stage.WRITE: write_fake,
                Stage.EDIT: edit_adapter,
                Stage.ILLUSTRATE: illustrate_adapter,
            },
            gates={Stage.PLAN: None},
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert run.content is not None and run.content.pieces[0].body == "See the museum!"
        assert run.edited is not None
        assert run.edited.pieces[0].body == "Visit the Harbour Museum: free entry, open daily."
        # Illustrate ran against Editor's output, not Chronicler's raw draft.
        assert run.illustrated is not None
        assert run.illustrated.pieces[0].body == run.edited.pieces[0].body
        edit_run = run.stage_run(Stage.EDIT)
        assert edit_run is not None and edit_run.status is RunStatus.SUCCEEDED


class TestIllustrationStageAdapter:
    async def test_wraps_the_agent_and_charges_no_budget(self):
        illustrated_claim = claim(
            id="c1", subject="Harbour Museum", kind=ClaimKind.DESCRIPTION,
            text="A striking waterfront building with a copper roof.",
        )
        content = content_set(
            pieces=[ContentPiece(kind="captions", body="See the museum!", claim_ids=["c1"])],
            claims=[illustrated_claim],
        )
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=", model="gpt-image-1")])
        adapter = IllustrationStageAdapter(IllustrationAgent(provider=provider))

        result = await adapter.run(content)

        assert len(result.artifact.images) == 1
        assert result.artifact.images[0].claim_ids == ["c1"]
        assert result.usage == ResourceUsage()
        assert result.calls == []

    async def test_degrades_gracefully_without_a_provider(self):
        illustrated_claim = claim(id="c1", subject="Harbour Museum")
        content = content_set(
            pieces=[ContentPiece(kind="captions", body="See the museum!", claim_ids=["c1"])],
            claims=[illustrated_claim],
        )
        adapter = IllustrationStageAdapter(IllustrationAgent(provider=None))

        result = await adapter.run(content)

        assert result.artifact.images == []
        assert any("provider configured" in c for c in result.artifact.caveats)

    async def test_illustrate_joins_a_six_stage_hermes_run_and_a_missing_provider_never_fails_it(
        self,
    ):
        illustrated_claim = checked_claim(id="c1", subject="Harbour Museum")
        research_fake = FakeStage(Stage.RESEARCH, [result(bundle(claims=[illustrated_claim]))])
        verify_fake = FakeStage(
            Stage.VERIFY, [result(verified(claims=[illustrated_claim]))]
        )
        content = content_set(
            pieces=[ContentPiece(kind="captions", body="See the museum!", claim_ids=["c1"])],
            claims=[illustrated_claim],
        )
        plan_fake = FakeStage(Stage.PLAN, [result(object())])
        write_fake = FakeStage(Stage.WRITE, [result(content)])
        # Edit is a pass-through here — it is exercised on its own terms in
        # TestEditorStageAdapter below; this test is about illustrate joining the run.
        edit_fake = FakeStage(Stage.EDIT, [result(content)])
        illustrate_adapter = IllustrationStageAdapter(IllustrationAgent(provider=None))

        hermes = Hermes(
            adapters={
                Stage.RESEARCH: research_fake,
                Stage.VERIFY: verify_fake,
                Stage.PLAN: plan_fake,
                Stage.WRITE: write_fake,
                Stage.EDIT: edit_fake,
                Stage.ILLUSTRATE: illustrate_adapter,
            },
            # PLAN's default gate inspects a real Itinerary's .days; the fake artifact
            # here is a placeholder standing in for "some prior stage's output", not a
            # real one — irrelevant to what this test is checking (illustrate joining a
            # six-stage run and its own, permissive gate).
            gates={Stage.PLAN: None},
        )

        run = await hermes.start(brief())

        assert run.status is RunStatus.SUCCEEDED
        assert run.illustrated is not None
        assert run.illustrated.images == []
        assert any("provider configured" in c for c in run.illustrated.caveats)
        illustrate_run = run.stage_run(Stage.ILLUSTRATE)
        assert illustrate_run is not None and illustrate_run.status is RunStatus.SUCCEEDED

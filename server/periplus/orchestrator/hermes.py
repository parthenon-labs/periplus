"""Hermes: the orchestrator.

Owns everything docs/architecture.md assigns to "Orchestration: Hermes" — run state,
strict stage order, budgets, bounded retries, the audit trail and replay. It knows
nothing about how a stage does its work; that is
:mod:`periplus.orchestrator.stages`'s job. Hermes only ever decides *whether* a stage
may run next.

Two failure shapes are kept distinct on purpose. A :class:`TransientStageError` is worth
retrying with the same input — a provider blip, a timeout — and is retried up to that
stage's bound. A :class:`StageFailure`, and a stage gate rejecting a structurally valid
artifact, are logical: retrying with the same input would just repeat them, so neither
consumes or triggers an automatic retry. Both are recorded as a failed
:class:`~periplus.models.StageRun`, tagged in ``error`` so the two are never confused
after the fact.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from periplus.models import Run, RunStatus, Stage, StageRun, TripBrief
from periplus.orchestrator.artifacts import ArtifactStore, InMemoryArtifactStore
from periplus.orchestrator.budget import BudgetTracker, ResourceUsage, RunBudget
from periplus.orchestrator.clock import Clock, SystemClock
from periplus.orchestrator.stages import DEFAULT_GATES, StageAdapter, StageGate, StageResult

__all__ = [
    "STAGE_ORDER",
    "Hermes",
    "HermesError",
    "InvalidTransition",
    "ReplayError",
    "StageFailure",
    "StageOrderError",
    "StageRetryPolicy",
    "TransientStageError",
]

#: The pipeline's full, fixed order. Hermes only ever runs a contiguous prefix of it — a
#: run may stop after plan and skip Chronicler, but it may never run write without having
#: run everything before it.
STAGE_ORDER: tuple[Stage, ...] = (Stage.RESEARCH, Stage.VERIFY, Stage.PLAN, Stage.WRITE)


class HermesError(Exception):
    """Base for every orchestration-level failure."""


class StageOrderError(HermesError):
    """The configured adapters do not form a contiguous prefix of the pipeline."""


class InvalidTransition(HermesError):
    """An explicit, rejected state transition — never a silent no-op."""

    def __init__(self, entity: str, from_status: RunStatus, to_status: RunStatus) -> None:
        super().__init__(f"{entity}: cannot transition from {from_status} to {to_status}")
        self.entity = entity
        self.from_status = from_status
        self.to_status = to_status


class ReplayError(HermesError):
    """No retained, gate-passed artifact exists at the requested replay boundary."""


class TransientStageError(HermesError):
    """A stage adapter's retryable failure: worth attempting again with the same input."""


class StageFailure(HermesError):
    """A stage adapter's non-retryable failure: the same input would fail the same way."""


@dataclass(frozen=True, slots=True)
class StageRetryPolicy:
    """How many attempts a stage gets, and how long to wait between them.

    Only :class:`TransientStageError` consumes this budget. ``max_attempts=1`` — the
    default — means a transient failure is not retried either; it must be opted into.
    """

    max_attempts: int = 1
    backoff_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative")


# Run and StageRun share periplus.models.RunStatus. A value not present as a key here
# is treated as having no legal outgoing transitions.
_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}),
    # A terminal run may only move again via an explicit replay, which resumes it.
    RunStatus.SUCCEEDED: frozenset({RunStatus.RUNNING}),
    RunStatus.FAILED: frozenset({RunStatus.RUNNING}),
    RunStatus.CANCELLED: frozenset({RunStatus.RUNNING}),
}

_STAGE_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    # PENDING -> FAILED/CANCELLED directly records a stage gated off before it ever
    # started (budget exhaustion or cancellation), with no RUNNING in between.
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}),
    RunStatus.SUCCEEDED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
}


def _transition_run(run: Run, to: RunStatus, *, clock: Clock) -> None:
    if to not in _RUN_TRANSITIONS[run.status]:
        raise InvalidTransition("run", run.status, to)
    run.status = to
    if to is RunStatus.RUNNING:
        run.finished_at = None
    elif to in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.finished_at = clock.now()


def _transition_stage(stage_run: StageRun, to: RunStatus, *, clock: Clock) -> None:
    if to not in _STAGE_TRANSITIONS[stage_run.status]:
        raise InvalidTransition(f"stage:{stage_run.stage.value}", stage_run.status, to)
    stage_run.status = to
    if to is RunStatus.RUNNING:
        stage_run.started_at = clock.now()
    elif to in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
        stage_run.finished_at = clock.now()


def _next_attempt(run: Run, stage: Stage) -> int:
    """Attempts keep counting up across a stage's whole history, replays included, so a
    new attempt can never collide with — and therefore never overwrite — a prior one.
    """
    return sum(1 for entry in run.stages if entry.stage is stage) + 1


def _spent_so_far(run: Run) -> tuple[ResourceUsage, float]:
    """Everything ``run`` has spent across every recorded stage attempt so far.

    Used to seed a fresh :class:`~periplus.orchestrator.budget.BudgetTracker` on replay,
    so the run's budget is a ceiling for the whole `Run` — replays included — rather than
    resetting every time :meth:`Hermes.replay` is called. Queries, fetches and tokens
    come from what each stage attempt itself reported spending
    (``StageRun.queries``/``fetches``/``tokens``) — not from ``Run.total_tokens``, which
    is a cost-audit figure derived from ``calls`` and not guaranteed to agree with it;
    wall clock comes from how long each attempt actually ran (``StageRun.duration_ms``),
    not the idle time between calls, so replaying a run long after it stopped is not
    itself charged against it.
    """
    usage = ResourceUsage(
        queries=sum(entry.queries for entry in run.stages),
        fetches=sum(entry.fetches for entry in run.stages),
        tokens=sum(entry.tokens for entry in run.stages),
    )
    elapsed_seconds = sum((entry.duration_ms or 0) for entry in run.stages) / 1000
    return usage, elapsed_seconds


def _attach(run: Run, stage: Stage, artifact: object) -> None:
    if stage is Stage.RESEARCH:
        run.research = artifact
    elif stage is Stage.VERIFY:
        run.verified = artifact
    elif stage is Stage.PLAN:
        run.itinerary = artifact
    elif stage is Stage.WRITE:
        run.content = artifact


def _never_cancelled() -> bool:
    return False


class Hermes:
    """Runs a configured, ordered subset of the pipeline against one brief at a time."""

    def __init__(
        self,
        *,
        adapters: Mapping[Stage, StageAdapter],
        gates: Mapping[Stage, StageGate | None] | None = None,
        retry: StageRetryPolicy | Mapping[Stage, StageRetryPolicy] | None = None,
        budget: RunBudget | None = None,
        clock: Clock | None = None,
        artifacts: ArtifactStore | None = None,
    ) -> None:
        included = [stage for stage in STAGE_ORDER if stage in adapters]
        if not included or included != list(STAGE_ORDER[: len(included)]):
            raise StageOrderError(
                "configured stages must be a contiguous prefix of the pipeline starting "
                f"at {Stage.RESEARCH.value}; got {[s.value for s in adapters]}"
            )
        self._stages = included
        self._adapters = dict(adapters)

        merged_gates: dict[Stage, StageGate | None] = dict(DEFAULT_GATES)
        if gates:
            merged_gates.update(gates)
        self._gates = {stage: merged_gates.get(stage) for stage in self._stages}

        if isinstance(retry, StageRetryPolicy):
            self._retry = {stage: retry for stage in self._stages}
        elif retry:
            default_policy = StageRetryPolicy()
            self._retry = {stage: retry.get(stage, default_policy) for stage in self._stages}
        else:
            self._retry = {stage: StageRetryPolicy() for stage in self._stages}

        self.budget = budget or RunBudget()
        self.clock = clock or SystemClock()
        self.artifacts = artifacts or InMemoryArtifactStore()

    async def start(
        self, brief: TripBrief, *, is_cancelled: Callable[[], bool] | None = None
    ) -> Run:
        """Begin a brand-new run from the trip brief, executing every configured stage
        in order until one is gated off, fails, or the pipeline completes.
        """
        run = Run(brief=brief)
        _transition_run(run, RunStatus.RUNNING, clock=self.clock)
        tracker = BudgetTracker(self.budget, self.clock)
        await self._drive(
            run,
            tracker,
            start_index=0,
            stage_input=brief,
            is_cancelled=is_cancelled or _never_cancelled,
        )
        return run

    async def replay(
        self, run: Run, *, from_stage: Stage, is_cancelled: Callable[[], bool] | None = None
    ) -> Run:
        """Resume ``run`` from a retained, gate-passed artifact.

        Stages before ``from_stage`` are never re-invoked — the artifact they produced
        is read back from the artifact store instead. ``run`` must be in a terminal
        state (it need not have failed; replaying a succeeded run re-runs from a chosen
        boundary on purpose, e.g. to retry verification with a fixed prompt).

        The budget is a ceiling for the whole ``Run``, not a fresh allowance per call: the
        tracker for this replay starts seeded with everything every prior attempt in
        ``run.stages`` already spent (see :func:`_spent_so_far`), so a stage that was
        already within budget the first time it ran does not get to spend the same budget
        again. A run that already exhausted its budget stays exhausted; replaying it
        cannot be used to bypass the ceiling one stage at a time.
        """
        if from_stage not in self._stages:
            raise ReplayError(f"{from_stage.value} is not configured in this pipeline")
        index = self._stages.index(from_stage)
        stage_input: object
        if index == 0:
            stage_input = run.brief
        else:
            boundary = self._stages[index - 1]
            retained = self.artifacts.latest_passed(run.id, boundary)
            if retained is None:
                raise ReplayError(
                    f"no retained, gate-passed artifact for {boundary.value} on run {run.id}"
                )
            stage_input = retained.artifact

        _transition_run(run, RunStatus.RUNNING, clock=self.clock)
        spent_usage, spent_seconds = _spent_so_far(run)
        tracker = BudgetTracker(
            self.budget, self.clock, usage=spent_usage, elapsed_seconds_before=spent_seconds
        )
        await self._drive(
            run,
            tracker,
            start_index=index,
            stage_input=stage_input,
            is_cancelled=is_cancelled or _never_cancelled,
        )
        return run

    async def _drive(
        self,
        run: Run,
        tracker: BudgetTracker,
        *,
        start_index: int,
        stage_input: object,
        is_cancelled: Callable[[], bool],
    ) -> None:
        current = stage_input
        for index in range(start_index, len(self._stages)):
            stage = self._stages[index]
            result = await self._attempt_stage(run, stage, current, tracker, is_cancelled)
            if result is None:
                last = run.stages[-1]
                to = RunStatus.CANCELLED if last.status is RunStatus.CANCELLED else RunStatus.FAILED
                _transition_run(run, to, clock=self.clock)
                return
            current = result.artifact
            _attach(run, stage, current)
        _transition_run(run, RunStatus.SUCCEEDED, clock=self.clock)

    async def _attempt_stage(
        self,
        run: Run,
        stage: Stage,
        stage_input: object,
        tracker: BudgetTracker,
        is_cancelled: Callable[[], bool],
    ) -> StageResult | None:
        adapter = self._adapters[stage]
        gate = self._gates.get(stage)
        policy = self._retry[stage]
        attempt = _next_attempt(run, stage)
        attempts_this_drive = 0

        while True:
            if is_cancelled():
                self._append_gated(
                    run, stage, attempt, RunStatus.CANCELLED, "cancelled before stage start"
                )
                return None
            reason = tracker.exceeded()
            if reason is not None:
                self._append_gated(
                    run, stage, attempt, RunStatus.FAILED, f"budget_exceeded: {reason}"
                )
                return None

            stage_run = StageRun(stage=stage, attempt=attempt)
            run.stages.append(stage_run)
            _transition_stage(stage_run, RunStatus.RUNNING, clock=self.clock)
            attempts_this_drive += 1

            try:
                result = await adapter.run(stage_input)
            except TransientStageError as exc:
                stage_run.error = f"transient: {exc}"
                _transition_stage(stage_run, RunStatus.FAILED, clock=self.clock)
                if attempts_this_drive < policy.max_attempts:
                    if policy.backoff_seconds:
                        await asyncio.sleep(policy.backoff_seconds * attempts_this_drive)
                    attempt += 1
                    continue
                return None
            except StageFailure as exc:
                stage_run.error = f"logical: {exc}"
                _transition_stage(stage_run, RunStatus.FAILED, clock=self.clock)
                return None
            except Exception as exc:  # noqa: BLE001 - preserve an explicit terminal record
                stage_run.error = f"internal: {type(exc).__name__}: {exc}"
                _transition_stage(stage_run, RunStatus.FAILED, clock=self.clock)
                return None

            stage_run.calls.extend(result.calls)
            stage_run.queries += result.usage.queries
            stage_run.fetches += result.usage.fetches
            stage_run.tokens += result.usage.tokens
            tracker.charge(result.usage)
            self.artifacts.put(run.id, stage, attempt, result.artifact)

            gate_reason = gate(result.artifact) if gate else None
            if gate_reason is not None:
                stage_run.error = f"gate: {gate_reason}"
                _transition_stage(stage_run, RunStatus.FAILED, clock=self.clock)
                return None

            # The stage completed and its artifact passed the gate, but its own usage —
            # or wall clock spent while it ran — may itself have pushed the run over
            # budget. This is checked here, not only before the *next* stage, so a
            # single-stage or final-stage pipeline cannot succeed having blown its
            # ceiling merely because there is no further stage left to gate off.
            overshoot = tracker.exceeded()
            if overshoot is not None:
                stage_run.error = f"budget_exceeded: {overshoot}"
                _transition_stage(stage_run, RunStatus.FAILED, clock=self.clock)
                return None

            self.artifacts.mark_passed(run.id, stage, attempt)
            _transition_stage(stage_run, RunStatus.SUCCEEDED, clock=self.clock)
            return result

    def _append_gated(
        self, run: Run, stage: Stage, attempt: int, status: RunStatus, reason: str
    ) -> None:
        """Record a stage that never started, because the run stopped before it could."""
        stage_run = StageRun(stage=stage, attempt=attempt, error=reason)
        run.stages.append(stage_run)
        _transition_stage(stage_run, status, clock=self.clock)

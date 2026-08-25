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

A third shape is neither: a stage that succeeded, passed its gate, and still produced an
artifact the *next* stage can see is not good enough. Retrying that stage is pointless —
the input has not changed — but going back further and changing the input is not. That
is a :class:`RecoveryPolicy`: one bounded, recorded backward edge in an otherwise
strictly forward pipeline. Hermes owns only the bound and the bookkeeping; what counts as
"not good enough" and what to do about it belong to the stages (see
:func:`~periplus.orchestrator.stages.unconfirmed_research`). Every pass it triggers is an
ordinary stage attempt with its own :class:`~periplus.models.StageRun`, charged against
the same budget as any other, so a run that took a second pass says so in its audit trail
rather than hiding it inside one stage.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from periplus.models import Run, RunStatus, Stage, StageRun, TripBrief
from periplus.orchestrator.artifacts import ArtifactStore, InMemoryArtifactStore
from periplus.orchestrator.budget import BudgetTracker, ResourceUsage, RunBudget
from periplus.orchestrator.clock import Clock, SystemClock
from periplus.orchestrator.errors import HermesError, StageFailure, TransientStageError
from periplus.orchestrator.stages import DEFAULT_GATES, StageAdapter, StageGate, StageResult

__all__ = [
    "STAGE_ORDER",
    "Hermes",
    "RecoveryPolicy",
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
STAGE_ORDER: tuple[Stage, ...] = (
    Stage.RESEARCH,
    Stage.VERIFY,
    Stage.PLAN,
    Stage.WRITE,
    Stage.EDIT,
    Stage.ILLUSTRATE,
)


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


@dataclass(frozen=True, slots=True)
class RecoveryPolicy:
    """One bounded backward edge: re-run an earlier stage with a directive from a later one.

    ``inspect`` is handed the gate-passed artifact of ``observes`` and returns either
    ``None`` — carry on forward, the common case — or an opaque directive. Hermes never
    looks inside that directive; it hands it, unchanged, to the ``resumes`` adapter's
    ``set_followup`` and re-runs the pipeline from there. Keeping the directive opaque is
    what stops the orchestrator from growing an opinion about research quality.

    ``max_passes`` bounds the edge per run. It is the whole termination argument: nothing
    about the second pass guarantees ``inspect`` will be satisfied by it, and a policy
    that kept asking until it was would be an unbounded spend dressed up as autonomy.
    """

    observes: Stage
    resumes: Stage
    inspect: Callable[[object], object | None]
    max_passes: int = 1

    def __post_init__(self) -> None:
        if self.max_passes < 1:
            raise ValueError("max_passes must be at least 1")


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
    elif stage is Stage.EDIT:
        run.edited = artifact
    elif stage is Stage.ILLUSTRATE:
        run.illustrated = artifact


def _never_cancelled() -> bool:
    return False


def _progress_reporter(stage_run: StageRun) -> Callable[[int, int], None]:
    """Bind a progress callback to one attempt's :class:`StageRun`.

    ``stage_run`` is the same mutable object ``RunEntry.live_run`` keeps reachable while
    the attempt is running (see ``periplus.api.runs``), so writing into it here is
    immediately visible to a caller polling ``RunSummary`` — no extra plumbing needed.
    """

    def report(processed: int, total: int) -> None:
        stage_run.progress_current = processed
        stage_run.progress_total = total

    return report


def _bundle_progress_reporter(stage_run: StageRun) -> Callable[[int, int], None]:
    """Bind a bundle-progress callback to one attempt's :class:`StageRun`.

    Same "same mutable object, no extra plumbing" trick as :func:`_progress_reporter`,
    for the ``(claims, evidence)`` signal research reports as its bundle grows.
    """

    def report(claims: int, evidence: int) -> None:
        stage_run.live_claims = claims
        stage_run.live_evidence = evidence

    return report


class Hermes:
    """Runs a configured, ordered subset of the pipeline against one brief at a time."""

    def __init__(
        self,
        *,
        adapters: Mapping[Stage, StageAdapter],
        gates: Mapping[Stage, StageGate | None] | None = None,
        retry: StageRetryPolicy | Mapping[Stage, StageRetryPolicy] | None = None,
        recovery: RecoveryPolicy | None = None,
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

        self._recovery = self._validated_recovery(recovery)
        #: Backward edges already spent, per run id. Instance state rather than a field
        #: on ``Run`` because production builds one Hermes per run — including per
        #: resume, see ``periplus.api.runs.RunStore``. A run picked back up after a
        #: crash therefore gets a fresh allowance; its budget, which is seeded from
        #: everything already spent, does not.
        self._recovery_passes: dict[str, int] = {}

        self.budget = budget or RunBudget()
        self.clock = clock or SystemClock()
        self.artifacts = artifacts or InMemoryArtifactStore()

    def _validated_recovery(self, recovery: RecoveryPolicy | None) -> RecoveryPolicy | None:
        """Reject a recovery policy this pipeline cannot actually honour, at build time.

        All three of these would otherwise be a silent no-op at run time — a backward
        edge configured, charged for in review, and never taken.
        """
        if recovery is None:
            return None
        if recovery.observes not in self._stages or recovery.resumes not in self._stages:
            raise StageOrderError(
                f"recovery policy names {recovery.observes.value}/{recovery.resumes.value}, "
                f"but this pipeline runs {[stage.value for stage in self._stages]}"
            )
        if self._stages.index(recovery.resumes) >= self._stages.index(recovery.observes):
            raise StageOrderError(
                f"recovery must resume before it observes; "
                f"{recovery.resumes.value} does not precede {recovery.observes.value}"
            )
        if not hasattr(self._adapters[recovery.resumes], "set_followup"):
            raise StageOrderError(
                f"the {recovery.resumes.value} adapter has no set_followup, so it cannot "
                "receive a recovery directive"
            )
        return recovery

    async def start(
        self,
        brief: TripBrief,
        *,
        run_id: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_run_created: Callable[[Run], None] | None = None,
    ) -> Run:
        """Begin a brand-new run from the trip brief, executing every configured stage
        in order until one is gated off, fails, or the pipeline completes.

        ``run_id`` overrides the generated :attr:`Run.id`. Omit it and one is generated
        as before; a caller that also persists this run under its own id (see
        :mod:`periplus.api.runs`) passes it explicitly, so the artifact store's keys and
        the persistence row share one identity. ``on_run_created`` receives that same
        mutable Run once, after it enters ``running`` and before the first stage begins.
        """
        run = Run(brief=brief) if run_id is None else Run(id=run_id, brief=brief)
        _transition_run(run, RunStatus.RUNNING, clock=self.clock)
        if on_run_created is not None:
            on_run_created(run)
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
        index = start_index
        while index < len(self._stages):
            stage = self._stages[index]
            result = await self._attempt_stage(run, stage, current, tracker, is_cancelled)
            if result is None:
                last = run.stages[-1]
                to = RunStatus.CANCELLED if last.status is RunStatus.CANCELLED else RunStatus.FAILED
                _transition_run(run, to, clock=self.clock)
                return
            current = result.artifact
            _attach(run, stage, current)

            if self._recovery is not None and stage is self._recovery.resumes:
                # The directive has been honoured. Clearing it here, rather than inside
                # the adapter's run(), is what lets a transiently-failed followup attempt
                # be retried as a followup instead of silently becoming a fresh sweep.
                self._adapters[stage].set_followup(None)

            jump = self._recovery_jump(run, stage, current)
            if jump is not None:
                index, current = jump
                continue
            index += 1
        _transition_run(run, RunStatus.SUCCEEDED, clock=self.clock)

    def _recovery_jump(
        self, run: Run, stage: Stage, artifact: object
    ) -> tuple[int, object] | None:
        """Decide whether this stage's artifact sends the pipeline backwards, and prepare it.

        Returns the index to continue from and the input to feed it, or ``None`` to carry
        on forward. Only ever consulted for an artifact that already passed its gate: a
        stage that failed never gets here, so a backward edge can never paper over a
        failure — it only ever acts on output that was good enough to continue with and
        still is not good enough to be worth continuing with.
        """
        policy = self._recovery
        if policy is None or stage is not policy.observes:
            return None
        used = self._recovery_passes.get(run.id, 0)
        if used >= policy.max_passes:
            return None

        directive = policy.inspect(artifact)
        if directive is None:
            return None

        index = self._stages.index(policy.resumes)
        if index == 0:
            stage_input: object = run.brief
        else:
            retained = self.artifacts.latest_passed(run.id, self._stages[index - 1])
            if retained is None:
                # The artifact store forgot the boundary this pass would resume from —
                # the same condition replay refuses on. Carrying on forward with a
                # merely-imperfect result beats failing a run that has one.
                return None
            stage_input = retained.artifact

        self._adapters[policy.resumes].set_followup(directive)
        self._recovery_passes[run.id] = used + 1
        return index, stage_input

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

            set_progress_callback = getattr(adapter, "set_progress_callback", None)
            if set_progress_callback is not None:
                set_progress_callback(_progress_reporter(stage_run))

            set_bundle_progress_callback = getattr(adapter, "set_bundle_progress_callback", None)
            if set_bundle_progress_callback is not None:
                set_bundle_progress_callback(_bundle_progress_reporter(stage_run))

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

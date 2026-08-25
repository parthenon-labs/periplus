"""In-process run registry: the thin layer between HTTP and Hermes.

A run's :class:`~periplus.orchestrator.Hermes` instance lives exactly as long as this
process does — restarting it always drops whatever ``asyncio.Task`` was driving a
mid-flight run. What survives a restart depends on which optional backends are wired in:

- ``persistence`` alone: once a run finishes, its brief, status, result and error are
  visible to every process sharing the same store, so :meth:`RunStore.get` and
  :meth:`RunStore.list` can serve history from before this process started. A run still
  mid-flight when the process dies is recorded ``running`` and then simply never
  updated — indistinguishable, from this alone, from a run about to finish any second.
- ``persistence`` and ``artifacts`` together: :meth:`RunStore.open` also resumes those
  stuck ``running`` rows, by replaying each one's retained artifacts (see
  :class:`~periplus.orchestrator.ArtifactStore`) and calling
  :meth:`~periplus.orchestrator.Hermes.replay` from the furthest stage that passed its
  gate. Without ``artifacts``, resume cannot happen — the boundary artifact to resume
  from does not exist anywhere this process can see.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from periplus.models import Run, RunStatus, Stage, StageRun, TripBrief
from periplus.orchestrator import STAGE_ORDER, Hermes

#: Builds one Hermes instance per brief. Production wiring is
#: ``lambda brief: build_hermes(brief=brief, artifacts=shared_artifacts)``; tests inject
#: a factory over fake stage adapters instead, the same substitution every other
#: live-provider seam in this codebase accepts.
HermesFactory = Callable[[TripBrief], Hermes]


class RunNotFound(KeyError):
    """No run exists for the given id."""


class _PersistedRecord(Protocol):
    """The shape a persistence backend hands back — a finished run without a task."""

    run_id: str
    brief: TripBrief
    status: RunStatus
    result: Run | None
    error: str | None


class RunPersistence(Protocol):
    """What :class:`RunStore` needs from a storage backend. ``periplus.storage`` has
    the only real implementation today; tests exercise :class:`RunStore` without one.
    """

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def save_started(self, *, run_id: str, brief: TripBrief) -> None: ...

    async def save_finished(
        self,
        *,
        run_id: str,
        brief: TripBrief,
        status: RunStatus,
        result: Run | None,
        error: str | None,
    ) -> None: ...

    async def load(self, run_id: str) -> _PersistedRecord | None: ...

    async def load_all(self) -> list[_PersistedRecord]: ...


@dataclass(slots=True)
class RunEntry:
    """One submitted run, plus whatever its background task has produced so far.

    ``task`` is ``None`` for entries recovered from persistence rather than submitted
    by this process — there is no live coroutine to hold, only the finished outcome.
    """

    run_id: str
    brief: TripBrief
    task: asyncio.Task[Run] | None = None
    live_run: Run | None = None
    result: Run | None = None
    error: str | None = None

    @property
    def status(self) -> RunStatus:
        if self.result is not None:
            return self.result.status
        if self.error is not None:
            return RunStatus.FAILED
        return RunStatus.RUNNING


class ArtifactResumeStore(Protocol):
    """What :class:`RunStore` needs from an artifact backend to resume a crashed run —
    a superset of :class:`~periplus.orchestrator.ArtifactStore` itself.
    :class:`~periplus.storage.PostgresArtifactStore` is the only real implementation;
    tests that do not exercise resume never pass one.
    """

    def latest_passed(self, run_id: str, stage: Stage): ...

    def get(self, run_id: str, stage: Stage, attempt: int): ...

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def preload(self, run_id: str) -> int: ...


class RunStore:
    """Runs each submitted brief through Hermes in its own background task.

    Submitting a trip never blocks on the pipeline itself — Hermes drives several real
    model calls per stage, easily tens of seconds. Hermes mutates one ``Run`` object in
    place as it goes; ``RunEntry.live_run`` keeps that object reachable so the HTTP layer
    can expose a small, honest stage summary while the background task is still running.
    The full artifact tree remains in ``result`` only after the task completes.
    """

    def __init__(
        self,
        hermes_factory: HermesFactory,
        *,
        persistence: RunPersistence | None = None,
        artifacts: ArtifactResumeStore | None = None,
    ) -> None:
        self._hermes_factory = hermes_factory
        self._persistence = persistence
        self._artifacts = artifacts
        self._entries: dict[str, RunEntry] = {}
        #: Strong references to in-flight persistence writes; see :meth:`_spawn_write`.
        self._writes: set[asyncio.Task[None]] = set()

    async def open(self) -> None:
        """Bring the storage backends up, if there are any, then resume anything left
        ``running`` by a process that never got to record how it ended. Call once at app
        startup; a store with no backends does nothing here.
        """
        if self._persistence is not None:
            await self._persistence.open()
        if self._artifacts is not None:
            await self._artifacts.open()
        if self._persistence is not None and self._artifacts is not None:
            await self._resume_crashed_runs()

    async def close(self) -> None:
        """Mirror of :meth:`open` for app shutdown.

        Pending persistence writes are drained *before* the backends they write through
        are closed. Shutting Postgres down underneath a half-finished ``save_finished``
        is the same lost terminal status :meth:`_spawn_write` exists to prevent, just
        arrived at from the other side.
        """
        await self.drain()
        if self._persistence is not None:
            await self._persistence.close()
        if self._artifacts is not None:
            await self._artifacts.close()

    async def drain(self) -> None:
        """Wait until no background persistence write is still in flight.

        Loops rather than awaiting one batch: a run finishing while the first batch is
        being awaited spawns its own write from a done callback, and that write is
        exactly the one worth waiting for. Exceptions are deliberately not retrieved
        here — ``asyncio.wait`` leaves them on the task, so a failed write still reaches
        the event loop's default handler instead of being silently absorbed by shutdown.
        """
        while self._writes:
            await asyncio.wait(tuple(self._writes))

    def _spawn_write(self, coro: Coroutine[object, object, None]) -> None:
        """Run a persistence write in the background, holding it reachable until it ends.

        ``asyncio.ensure_future`` on its own is not enough. The event loop keeps only a
        weak reference to a running task, so a write nobody else holds may be garbage
        collected mid-flight — at any point, not merely at process exit. For
        ``save_finished`` that is not a lost history row but a self-inflicted wound: the
        run stays recorded ``running``, and :meth:`_resume_crashed_runs` treats a
        ``running`` row from a prior process as a crash, so the next startup replays a
        run that already succeeded and pays for its model calls a second time.
        """
        task = asyncio.ensure_future(coro)
        self._writes.add(task)
        task.add_done_callback(self._writes.discard)

    def submit(self, brief: TripBrief) -> RunEntry:
        run_id = uuid4().hex
        hermes = self._hermes_factory(brief)
        entry = RunEntry(run_id=run_id, brief=brief)
        task = asyncio.ensure_future(
            hermes.start(
                brief,
                run_id=run_id,
                on_run_created=lambda run: setattr(entry, "live_run", run),
            )
        )
        entry.task = task
        task.add_done_callback(lambda done: self._finish(entry, done))
        if self._persistence is not None:
            # Not awaited: a failed write here loses this run's ability to be noticed as
            # crashed later, not anything the current process needs back. Still held
            # reachable — see _spawn_write.
            self._spawn_write(self._persistence.save_started(run_id=run_id, brief=brief))
        self._entries[run_id] = entry
        return entry

    def _finish(self, entry: RunEntry, task: asyncio.Task[Run]) -> None:
        if task.cancelled():
            entry.error = "run was cancelled"
        else:
            exc = task.exception()
            if exc is not None:
                entry.error = f"{type(exc).__name__}: {exc}"
            else:
                entry.result = task.result()
        if self._persistence is not None:
            # Not awaited — this runs inside a task's done callback, which cannot await —
            # but held reachable until it completes: this is the write that records the
            # run's terminal status, and losing it is what strands a finished run as
            # ``running`` forever. See _spawn_write.
            self._spawn_write(
                self._persistence.save_finished(
                    run_id=entry.run_id,
                    brief=entry.brief,
                    status=entry.status,
                    result=entry.result,
                    error=entry.error,
                )
            )

    async def _resume_crashed_runs(self) -> None:
        """Find every row still ``running`` from a prior process and pick each back up
        from the furthest stage that retained a gate-passed artifact.

        A row stuck ``running`` here always means the process that wrote it died before
        calling :meth:`RunPersistence.save_finished` — this process's own in-flight runs
        are never in ``_persistence`` yet at the point :meth:`open` calls this, since
        nothing has been submitted here.
        """
        assert self._persistence is not None and self._artifacts is not None
        for record in await self._persistence.load_all():
            if record.status is not RunStatus.RUNNING:
                continue
            await self._resume_one(record.run_id, record.brief)

    async def _resume_one(self, run_id: str, brief: TripBrief) -> None:
        highest_attempt = await self._artifacts.preload(run_id)
        from_stage: Stage | None = None
        for stage in STAGE_ORDER:
            if self._artifacts.latest_passed(run_id, stage) is not None:
                from_stage = stage
        if from_stage is None:
            # Died before any stage passed its gate — there is nothing to resume from;
            # a fresh start would silently pretend this attempt never happened instead.
            if self._persistence is not None:
                await self._persistence.save_finished(
                    run_id=run_id,
                    brief=brief,
                    status=RunStatus.FAILED,
                    result=None,
                    error="crashed before any stage passed its gate",
                )
            return
        next_index = STAGE_ORDER.index(from_stage) + 1
        if next_index >= len(STAGE_ORDER):
            # Every configured stage already passed; the process must have died on the
            # way to recording success. Nothing left to run — just record the outcome
            # rather than replaying a pipeline that already finished.
            if self._persistence is not None:
                await self._persistence.save_finished(
                    run_id=run_id,
                    brief=brief,
                    status=RunStatus.SUCCEEDED,
                    result=None,
                    error=None,
                )
            return
        resume_from = STAGE_ORDER[next_index]

        run = Run(id=run_id, brief=brief, status=RunStatus.FAILED)
        # The stage we are about to resume may itself have partial, gate-failed
        # attempts already retained (the crash could have landed mid-stage, not only
        # between stages) — pad placeholder history so Hermes' own attempt counter
        # continues past them instead of colliding with an attempt number the artifact
        # store already has a row for.
        for attempt in range(1, highest_attempt + 1):
            if self._artifacts.get(run_id, resume_from, attempt) is not None:
                placeholder = StageRun(stage=resume_from, attempt=attempt, status=RunStatus.FAILED)
                run.stages.append(placeholder)

        hermes = self._hermes_factory(brief)
        task = asyncio.ensure_future(hermes.replay(run, from_stage=resume_from))
        entry = RunEntry(run_id=run_id, brief=brief, task=task, live_run=run)
        task.add_done_callback(lambda done, entry=entry: self._finish(entry, done))
        self._entries[run_id] = entry

    async def get(self, run_id: str) -> RunEntry:
        entry = self._entries.get(run_id)
        if entry is not None:
            return entry
        if self._persistence is not None:
            record = await self._persistence.load(run_id)
            if record is not None:
                return RunEntry(
                    run_id=record.run_id,
                    brief=record.brief,
                    result=record.result,
                    error=record.error,
                )
        raise RunNotFound(run_id)

    async def list(self) -> list[RunEntry]:
        merged: dict[str, RunEntry] = {}
        if self._persistence is not None:
            for record in await self._persistence.load_all():
                merged[record.run_id] = RunEntry(
                    run_id=record.run_id,
                    brief=record.brief,
                    result=record.result,
                    error=record.error,
                )
        merged.update(self._entries)  # this process's live state wins over a stale row
        return list(merged.values())

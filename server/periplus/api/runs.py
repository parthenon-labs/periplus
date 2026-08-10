"""In-process run registry: the thin layer between HTTP and Hermes.

No persistence beyond this process's memory — Postgres joins once the pipeline is
proven end to end over HTTP, not before. A run's :class:`~periplus.orchestrator.Hermes`
instance, and therefore its :class:`~periplus.orchestrator.ArtifactStore`, lives exactly
as long as this process does; restarting it loses every in-flight and completed run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from uuid import uuid4

from periplus.models import Run, RunStatus, TripBrief
from periplus.orchestrator import Hermes

#: Builds one Hermes instance per brief. Production wiring is
#: ``lambda brief: build_hermes(brief=brief)``; tests inject a factory over fake stage
#: adapters instead, the same substitution every other live-provider seam in this
#: codebase accepts.
HermesFactory = Callable[[TripBrief], Hermes]


class RunNotFound(KeyError):
    """No run exists for the given id."""


@dataclass(slots=True)
class RunEntry:
    """One submitted run, plus whatever its background task has produced so far."""

    run_id: str
    brief: TripBrief
    task: asyncio.Task[Run]
    result: Run | None = None
    error: str | None = None

    @property
    def status(self) -> RunStatus:
        if self.result is not None:
            return self.result.status
        if self.error is not None:
            return RunStatus.FAILED
        return RunStatus.RUNNING


class RunStore:
    """Runs each submitted brief through Hermes in its own background task.

    Submitting a trip never blocks on the pipeline itself — Hermes drives several real
    model calls per stage, easily tens of seconds. Hermes mutates one ``Run`` object in
    place as it goes but only returns it once the whole drive finishes, so this store
    cannot report live per-stage progress today; a caller polling :meth:`get` sees
    ``running`` with no further detail until the task completes, then the full ``Run``.
    Finer-grained progress is a later refinement to Hermes itself, not a gap in this
    layer.
    """

    def __init__(self, hermes_factory: HermesFactory) -> None:
        self._hermes_factory = hermes_factory
        self._entries: dict[str, RunEntry] = {}

    def submit(self, brief: TripBrief) -> RunEntry:
        run_id = uuid4().hex
        hermes = self._hermes_factory(brief)
        task = asyncio.ensure_future(hermes.start(brief))
        entry = RunEntry(run_id=run_id, brief=brief, task=task)
        task.add_done_callback(lambda done: self._finish(entry, done))
        self._entries[run_id] = entry
        return entry

    def _finish(self, entry: RunEntry, task: asyncio.Task[Run]) -> None:
        if task.cancelled():
            entry.error = "run was cancelled"
            return
        exc = task.exception()
        if exc is not None:
            entry.error = f"{type(exc).__name__}: {exc}"
            return
        entry.result = task.result()

    def get(self, run_id: str) -> RunEntry:
        try:
            return self._entries[run_id]
        except KeyError:
            raise RunNotFound(run_id) from None

    def list(self) -> list[RunEntry]:
        return list(self._entries.values())

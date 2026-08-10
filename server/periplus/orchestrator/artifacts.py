"""Artifact retention for replay.

Every stage attempt's produced artifact is retained, whether or not it passed the
stage's gate — this is what docs/architecture.md means by "the failing artifact
preserved rather than overwritten". A rejected or superseded attempt stays retrievable
for inspection; it is simply never a valid replay boundary. Replay resumes only from an
attempt that passed its gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from periplus.models import Artifact, Stage


@dataclass(frozen=True, slots=True)
class RetainedArtifact:
    attempt: int
    artifact: Artifact


class ArtifactStore(Protocol):
    def put(self, run_id: str, stage: Stage, attempt: int, artifact: Artifact) -> None: ...

    def mark_passed(self, run_id: str, stage: Stage, attempt: int) -> None: ...

    def get(self, run_id: str, stage: Stage, attempt: int) -> Artifact | None: ...

    def latest_passed(self, run_id: str, stage: Stage) -> RetainedArtifact | None: ...


class InMemoryArtifactStore:
    """Deterministic, in-process retention. A persistent store would implement the same
    protocol against a table, without Hermes changing at all.
    """

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, Stage, int], Artifact] = {}
        self._passed: dict[tuple[str, Stage], int] = {}

    def put(self, run_id: str, stage: Stage, attempt: int, artifact: Artifact) -> None:
        key = (run_id, stage, attempt)
        if key in self._artifacts:
            raise ValueError(
                f"artifact for run {run_id}, stage {stage.value}, attempt {attempt} is "
                "already retained; retries never overwrite a prior attempt"
            )
        self._artifacts[key] = artifact

    def mark_passed(self, run_id: str, stage: Stage, attempt: int) -> None:
        key = (run_id, stage, attempt)
        if key not in self._artifacts:
            raise ValueError(
                f"no retained artifact for run {run_id}, stage {stage.value}, attempt {attempt}"
            )
        boundary = (run_id, stage)
        current = self._passed.get(boundary)
        if current is None or attempt > current:
            self._passed[boundary] = attempt

    def get(self, run_id: str, stage: Stage, attempt: int) -> Artifact | None:
        return self._artifacts.get((run_id, stage, attempt))

    def latest_passed(self, run_id: str, stage: Stage) -> RetainedArtifact | None:
        attempt = self._passed.get((run_id, stage))
        if attempt is None:
            return None
        return RetainedArtifact(attempt=attempt, artifact=self._artifacts[(run_id, stage, attempt)])

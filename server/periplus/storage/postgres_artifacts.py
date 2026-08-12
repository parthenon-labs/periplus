"""Postgres-backed artifact retention, for crash recovery.

:class:`~periplus.orchestrator.artifacts.ArtifactStore` is a synchronous protocol —
Hermes calls ``put``/``mark_passed``/``get``/``latest_passed`` without ``await``, on the
hot path of every stage. A network round trip on every call would either block the event
loop or force Hermes to change; neither is acceptable just to add durability. So this
class does not talk to Postgres directly as an ``ArtifactStore``: it wraps an
:class:`~periplus.orchestrator.artifacts.InMemoryArtifactStore` and delegates every
protocol method to it unchanged — same behaviour, same guarantees, zero surprise for
Hermes or its tests. Durability is bolted on beside that, not inside it: ``put`` and
``mark_passed`` additionally fire an async write to Postgres and do not wait for it, the
same fire-and-forget shape :class:`~periplus.storage.postgres.PostgresRunPersistence`
already uses for finished runs.

What this buys: a run's per-stage artifacts outlive the process that produced them. What
it does not buy: a torn write (process dies between the in-memory ``put`` and its
Postgres echo) can leave the two out of sync — recoverable on restart via
:meth:`preload`, at worst re-running the stage that was mid-write, never silently
corrupting one that already passed its gate.
"""

from __future__ import annotations

import asyncio

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from periplus.models import Artifact, ContentSet, Itinerary, ResearchBundle, Stage, VerifiedBundle
from periplus.orchestrator.artifacts import InMemoryArtifactStore, RetainedArtifact

_SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    run_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    artifact JSONB NOT NULL,
    passed BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, stage, attempt)
);
"""

#: Every stage's artifact type, so a row read back from JSONB can be validated into the
#: same Pydantic model Hermes produced it as — not a bare dict.
_ARTIFACT_TYPES: dict[Stage, type[Artifact]] = {
    Stage.RESEARCH: ResearchBundle,
    Stage.VERIFY: VerifiedBundle,
    Stage.PLAN: Itinerary,
    Stage.WRITE: ContentSet,
}


class PostgresArtifactStore:
    """The live :class:`ArtifactStore` Hermes runs against, plus the Postgres echo and
    the restart-time replay that makes crash recovery possible.

    One instance is shared for the process's whole lifetime — across every submitted
    run, not one per run — so a resumed run's fresh :class:`~periplus.orchestrator.Hermes`
    can be handed the exact same store a prior process's Hermes was using.
    """

    def __init__(self, database_url: str) -> None:
        self._pool = AsyncConnectionPool(conninfo=database_url, open=False)
        self._memory = InMemoryArtifactStore()

    async def open(self) -> None:
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        await self._pool.close()

    # -- ArtifactStore protocol: synchronous, delegates to the in-memory store ---------

    def put(self, run_id: str, stage: Stage, attempt: int, artifact: Artifact) -> None:
        self._memory.put(run_id, stage, attempt, artifact)
        asyncio.ensure_future(self._echo_put(run_id, stage, attempt, artifact))

    def mark_passed(self, run_id: str, stage: Stage, attempt: int) -> None:
        self._memory.mark_passed(run_id, stage, attempt)
        asyncio.ensure_future(self._echo_passed(run_id, stage, attempt))

    def get(self, run_id: str, stage: Stage, attempt: int) -> Artifact | None:
        return self._memory.get(run_id, stage, attempt)

    def latest_passed(self, run_id: str, stage: Stage) -> RetainedArtifact | None:
        return self._memory.latest_passed(run_id, stage)

    # -- Postgres echo, fire-and-forget from the two calls above -----------------------

    async def _echo_put(self, run_id: str, stage: Stage, attempt: int, artifact: Artifact) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO artifacts (run_id, stage, attempt, artifact)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id, stage, attempt) DO NOTHING
                """,
                (run_id, stage.value, attempt, Jsonb(artifact.model_dump(mode="json"))),
            )

    async def _echo_passed(self, run_id: str, stage: Stage, attempt: int) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                "UPDATE artifacts SET passed = TRUE "
                "WHERE run_id = %s AND stage = %s AND attempt = %s",
                (run_id, stage.value, attempt),
            )

    # -- Restart-time recovery ----------------------------------------------------------

    async def preload(self, run_id: str) -> int:
        """Read every retained row for ``run_id`` back from Postgres into this store's
        in-memory delegate, so a fresh process's Hermes sees exactly what the crashed
        one had retained. Returns the highest attempt number seen for any stage, so a
        caller resuming mid-stage can seed :class:`~periplus.models.Run` accordingly and
        never collide with an attempt :meth:`put` already used.

        Idempotent-ish, not exactly-once: rows already ``put`` into the in-memory
        delegate this run are skipped rather than raising, since a run can only be
        preloaded once in practice (at process start, before it is touched again), but a
        second call should not crash a caller that does it anyway.
        """
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT stage, attempt, artifact, passed FROM artifacts WHERE run_id = %s "
                "ORDER BY attempt",
                (run_id,),
            )
            rows = await cursor.fetchall()

        highest_attempt = 0
        for stage_value, attempt, artifact_json, passed in rows:
            stage = Stage(stage_value)
            highest_attempt = max(highest_attempt, attempt)
            if self._memory.get(run_id, stage, attempt) is not None:
                continue
            artifact_type = _ARTIFACT_TYPES[stage]
            artifact = artifact_type.model_validate(artifact_json)
            self._memory.put(run_id, stage, attempt, artifact)
            if passed:
                self._memory.mark_passed(run_id, stage, attempt)
        return highest_attempt


__all__ = ["PostgresArtifactStore"]

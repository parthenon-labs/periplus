"""Postgres-backed run persistence.

A run's live ``asyncio.Task`` cannot survive a process restart no matter where its
output is stored — but the fact that a run was submitted, and with which brief, can:
:meth:`save_started` writes a ``running`` row the moment a run begins, before its result
is known, so a fresh process's :func:`~periplus.api.runs.RunStore` resume path can find
rows still ``running`` from a process that never got to call :meth:`save_finished` and
treat them as crashed. :meth:`save_finished` then upserts the same row (``ON CONFLICT``)
once the outcome is known — the normal, un-crashed path. Completed and failed runs
outlive the process that produced them, are visible to every process sharing the same
database, and ``GET /runs`` reflects history from before the current process started.

This table only ever tells a caller *that* a run was mid-flight, not *how far* — the
per-stage boundary needed to resume it lives in
:class:`~periplus.storage.postgres_artifacts.PostgresArtifactStore` instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from periplus.models import Run, RunStatus, TripBrief

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    destination TEXT NOT NULL,
    status TEXT NOT NULL,
    brief JSONB NOT NULL,
    result JSONB,
    error TEXT,
    submitted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);
"""


@dataclass(slots=True)
class RunRecord:
    """One finished run read back from storage — the same shape
    :class:`~periplus.api.runs.RunEntry` exposes to callers, minus the live task no
    persisted row can have.
    """

    run_id: str
    brief: TripBrief
    status: RunStatus
    result: Run | None
    error: str | None


class PostgresRunPersistence:
    """Writes/reads finished runs through a pooled async connection.

    ``open``/``close`` bracket the pool's lifetime — call them from the app's own
    startup/shutdown (a FastAPI lifespan), not from ``__init__``, since opening a pool
    is an async operation and construction here is not. ``open`` also creates the
    ``runs`` table if it does not exist yet; there is no separate migration step for a
    schema this small.
    """

    def __init__(self, database_url: str) -> None:
        # ``check`` pings every connection the pool is about to hand out and discards
        # it for a fresh one on failure, instead of handing back a connection that
        # merely *looks* idle. Without it, a server that drops connections out from
        # under the pool — Neon suspending its compute after a long idle period is the
        # one that bit us — leaves the pool holding sockets that are dead but not
        # marked as such, so the next borrower's query fails outright (`server closed
        # the connection unexpectedly` / `consuming input failed`) instead of quietly
        # reconnecting. See ``AsyncConnectionPool.check_connection``.
        self._pool = AsyncConnectionPool(
            conninfo=database_url, open=False, check=AsyncConnectionPool.check_connection
        )

    async def open(self) -> None:
        await self._pool.open()
        async with self._pool.connection() as conn:
            await conn.execute(_SCHEMA)

    async def close(self) -> None:
        await self._pool.close()

    async def save_started(self, *, run_id: str, brief: TripBrief) -> None:
        """Record that ``run_id`` began, before anything about its outcome is known.

        ``ON CONFLICT DO NOTHING``: a run is only ever started once, so a second call
        for the same id (there should not be one) leaves the original row alone rather
        than clobbering a status a concurrent :meth:`save_finished` may have already
        written.
        """
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO runs (run_id, destination, status, brief)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (run_id) DO NOTHING
                """,
                (
                    run_id,
                    brief.destination,
                    RunStatus.RUNNING.value,
                    Jsonb(brief.model_dump(mode="json")),
                ),
            )

    async def save_finished(
        self,
        *,
        run_id: str,
        brief: TripBrief,
        status: RunStatus,
        result: Run | None,
        error: str | None,
    ) -> None:
        async with self._pool.connection() as conn:
            await conn.execute(
                """
                INSERT INTO runs (run_id, destination, status, brief, result, error, finished_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    result = EXCLUDED.result,
                    error = EXCLUDED.error,
                    finished_at = EXCLUDED.finished_at
                """,
                (
                    run_id,
                    brief.destination,
                    status.value,
                    Jsonb(brief.model_dump(mode="json")),
                    Jsonb(result.model_dump(mode="json")) if result is not None else None,
                    error,
                ),
            )

    async def load(self, run_id: str) -> RunRecord | None:
        async with self._pool.connection() as conn:
            cursor = await conn.execute(
                "SELECT run_id, brief, status, result, error FROM runs WHERE run_id = %s",
                (run_id,),
            )
            row = await cursor.fetchone()
        return self._to_record(row) if row is not None else None

    async def load_all(self) -> list[RunRecord]:
        async with self._pool.connection() as conn:
            cursor = await conn.execute("SELECT run_id, brief, status, result, error FROM runs")
            rows = await cursor.fetchall()
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _to_record(row: tuple) -> RunRecord:
        run_id, brief_json, status, result_json, error = row
        return RunRecord(
            run_id=run_id,
            brief=TripBrief.model_validate(brief_json),
            status=RunStatus(status),
            result=Run.model_validate(result_json) if result_json is not None else None,
            error=error,
        )

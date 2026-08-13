"""The FastAPI app: submit a trip, poll for the run it produces.

No auth, no rate limiting yet — this is the pipeline made reachable over HTTP, plus a
Postgres-backed record of finished runs, nothing more. Run ``uvicorn
periplus.api.app:app`` for local use; ``PERIPLUS_DATABASE_URL`` (and
``PERIPLUS_GOOGLE_MAPS_API_KEY`` and friends) still come from the environment the same
way the CLI probe reads them.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from periplus.api.runs import RunNotFound, RunStore
from periplus.api.schemas import RunResultView, RunSummary, TripBriefCreate
from periplus.config import get_settings
from periplus.models import RunStatus
from periplus.orchestrator import build_hermes
from periplus.storage import PostgresArtifactStore, PostgresEvidenceCache, PostgresRunPersistence


def create_app(*, run_store: RunStore | None = None) -> FastAPI:
    """Build the app. ``run_store`` is the seam tests use to swap in fake stage
    adapters and skip Postgres entirely; production wiring builds a real
    :class:`~periplus.orchestrator.Hermes` per submitted brief via
    :func:`~periplus.orchestrator.build_hermes`, backed by
    :class:`~periplus.storage.PostgresRunPersistence` and one shared
    :class:`~periplus.storage.PostgresArtifactStore` — shared because a run resumed
    after a crash must see the exact same retained artifacts its first attempt wrote —
    plus one shared, optional :class:`~periplus.storage.PostgresEvidenceCache`, shared
    for the opposite reason: its whole value is being consulted across runs.

    The evidence cache is assembled inside ``lifespan``, not here, and only when this
    app owns its own store. Building it means loading a local embedding model — real
    work, not free — and a test that injects its own ``run_store`` to skip Postgres
    entirely should not pay for that too; nor should merely importing this module, which
    happens whenever anything imports ``app`` below.
    """
    owns_store = run_store is None
    artifacts = PostgresArtifactStore(get_settings().database_url)
    evidence_cache: PostgresEvidenceCache | None = None

    def hermes_factory(brief):
        return build_hermes(brief=brief, artifacts=artifacts, evidence_cache=evidence_cache)

    store = run_store or RunStore(
        hermes_factory,
        persistence=PostgresRunPersistence(get_settings().database_url),
        artifacts=artifacts,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        nonlocal evidence_cache
        # A store a test injected already owns its own lifecycle (or has none to open).
        if owns_store:
            await store.open()
            from periplus.storage import build_evidence_cache

            evidence_cache = build_evidence_cache(get_settings())
            if evidence_cache is not None:
                try:
                    await evidence_cache.open()
                except Exception:  # noqa: BLE001 - the cache is optional; startup is not
                    # An unreachable Postgres or a database missing permission to create
                    # the vector extension is exactly the kind of thing this layer is
                    # meant to be optional about — see periplus.storage.postgres_evidence.
                    # ``store.open()`` above is not wrapped the same way: run persistence
                    # is not optional, so its own connectivity failure should still fail
                    # startup loudly.
                    evidence_cache = None
        try:
            yield
        finally:
            if owns_store:
                await store.close()
                if evidence_cache is not None:
                    await evidence_cache.close()

    app = FastAPI(title="Periplus", version="0.1.0", lifespan=lifespan)

    @app.post("/trips", response_model=RunSummary, status_code=202)
    async def submit_trip(payload: TripBriefCreate) -> RunSummary:
        entry = store.submit(payload.to_trip_brief())
        return RunSummary.from_entry(entry)

    @app.get("/runs", response_model=list[RunSummary])
    async def list_runs() -> list[RunSummary]:
        return [RunSummary.from_entry(entry) for entry in await store.list()]

    @app.get("/runs/{run_id}", response_model=RunSummary)
    async def get_run(run_id: str) -> RunSummary:
        try:
            entry = await store.get(run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None
        return RunSummary.from_entry(entry)

    @app.get("/runs/{run_id}/result", response_model=RunResultView)
    async def get_run_result(run_id: str) -> RunResultView:
        try:
            entry = await store.get(run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None
        if entry.status in {RunStatus.PENDING, RunStatus.RUNNING}:
            raise HTTPException(status_code=409, detail="run has not finished")
        return RunResultView.from_entry(entry)

    return app


app = create_app()

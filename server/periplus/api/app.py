"""The FastAPI app: submit a trip, poll for the run it produces.

No auth, no rate limiting, no persistence beyond process memory — this is the pipeline
made reachable over HTTP, nothing more yet. Run ``uvicorn periplus.api.app:app`` for
local use; ``PERIPLUS_GOOGLE_MAPS_API_KEY`` and friends still come from the environment
the same way the CLI probe reads them.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException

from periplus.api.runs import RunNotFound, RunStore
from periplus.api.schemas import RunView
from periplus.models import TripBrief
from periplus.orchestrator import build_hermes


def create_app(*, run_store: RunStore | None = None) -> FastAPI:
    """Build the app. ``run_store`` is the seam tests use to swap in fake stage
    adapters; production wiring builds a real :class:`~periplus.orchestrator.Hermes`
    per submitted brief via :func:`~periplus.orchestrator.build_hermes`.
    """
    store = run_store or RunStore(lambda brief: build_hermes(brief=brief))
    app = FastAPI(title="Periplus", version="0.1.0")

    @app.post("/trips", response_model=RunView, status_code=202)
    async def submit_trip(brief: TripBrief) -> RunView:
        entry = store.submit(brief)
        return RunView.from_entry(entry)

    @app.get("/runs", response_model=list[RunView])
    async def list_runs() -> list[RunView]:
        return [RunView.from_entry(entry) for entry in store.list()]

    @app.get("/runs/{run_id}", response_model=RunView)
    async def get_run(run_id: str) -> RunView:
        try:
            entry = store.get(run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run not found") from None
        return RunView.from_entry(entry)

    return app


app = create_app()

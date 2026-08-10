"""Offline tests for the HTTP layer: run store lifecycle and FastAPI wiring.

Everything here drives a one-stage Hermes built from a scripted, in-process fake
adapter — the same style :mod:`tests.test_orchestrator` uses — so no network, no real
model calls, and no wait for real work to finish.
"""

from __future__ import annotations

import time
from datetime import date

import pytest
from fastapi.testclient import TestClient

from periplus.api.app import create_app
from periplus.api.runs import RunNotFound, RunStore
from periplus.models import Claim, ClaimKind, ResearchBundle, RunStatus, Stage, TripBrief
from periplus.orchestrator import FakeClock, Hermes, StageResult


def brief(**overrides) -> TripBrief:
    values = {
        "destination": "Lisbon",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 3),
    }
    values.update(overrides)
    return TripBrief(**values)


class OneShotResearch:
    """A minimal research-stage adapter that returns one scripted bundle."""

    stage = Stage.RESEARCH

    def __init__(self, bundle: ResearchBundle) -> None:
        self._bundle = bundle

    async def run(self, stage_input: object) -> StageResult:
        return StageResult(artifact=self._bundle)


def fake_hermes_factory():
    """A one-stage Hermes (research only) that always succeeds against a fixed brief."""

    def factory(trip_brief: TripBrief) -> Hermes:
        bundle = ResearchBundle(
            brief_id=trip_brief.id,
            claims=[Claim(id="claim-1", subject="x", text="y", kind=ClaimKind.OTHER)],
            queries_run=["q"],
        )
        return Hermes(adapters={Stage.RESEARCH: OneShotResearch(bundle)}, clock=FakeClock())

    return factory


def wait_for_completion(client: TestClient, run_id: str, *, attempts: int = 50) -> dict:
    for _ in range(attempts):
        body = client.get(f"/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} never left running")


class TestRunStore:
    async def test_submit_completes_in_the_background(self):
        store = RunStore(fake_hermes_factory())
        entry = store.submit(brief())

        assert entry.status == RunStatus.RUNNING
        await entry.task

        assert entry.status == RunStatus.SUCCEEDED
        assert entry.result is not None
        assert entry.result.research is not None

    async def test_unknown_run_raises(self):
        store = RunStore(fake_hermes_factory())
        with pytest.raises(RunNotFound):
            store.get("ghost")

    async def test_list_returns_every_submitted_run(self):
        store = RunStore(fake_hermes_factory())
        first = store.submit(brief())
        second = store.submit(brief(destination="Porto"))
        await first.task
        await second.task

        assert {entry.run_id for entry in store.list()} == {first.run_id, second.run_id}


class TestApp:
    def test_submit_and_poll_reaches_a_completed_run(self):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        with TestClient(app) as client:
            response = client.post(
                "/trips",
                json={
                    "destination": "Lisbon",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-03",
                },
            )
            assert response.status_code == 202
            run_id = response.json()["id"]

            final = wait_for_completion(client, run_id)
            assert final["status"] == "succeeded"
            assert final["run"]["research"] is not None

    def test_unknown_run_returns_404(self):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        with TestClient(app) as client:
            assert client.get("/runs/ghost").status_code == 404

    def test_rejects_unknown_fields_in_trip_body(self):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        with TestClient(app) as client:
            response = client.post(
                "/trips",
                json={
                    "destination": "Lisbon",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-03",
                    "not_a_field": True,
                },
            )
            assert response.status_code == 422

    def test_list_runs_includes_the_submitted_trip(self):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        with TestClient(app) as client:
            submitted = client.post(
                "/trips",
                json={
                    "destination": "Lisbon",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-03",
                },
            ).json()

            listing = client.get("/runs").json()
            assert any(entry["id"] == submitted["id"] for entry in listing)

"""Offline tests for the HTTP layer: run store lifecycle and FastAPI wiring.

Everything here drives a one-stage Hermes built from a scripted, in-process fake
adapter — the same style :mod:`tests.test_orchestrator` uses — so no network, no real
model calls, and no wait for real work to finish. ``TestRunStorePersistence`` drives
:class:`RunStore` against an in-process fake persistence backend rather than a real
Postgres — the store's merge/fallback logic is what is under test there, not psycopg.
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import date

import pytest
from fastapi.testclient import TestClient

from periplus.api.app import create_app
from periplus.api.runs import RunNotFound, RunStore
from periplus.models import (
    Check,
    Claim,
    ClaimKind,
    ResearchBundle,
    Run,
    RunStatus,
    Stage,
    TripBrief,
    Verdict,
    VerifiedBundle,
)
from periplus.orchestrator import FakeClock, Hermes, InMemoryArtifactStore, StageResult


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
            await store.get("ghost")

    async def test_list_returns_every_submitted_run(self):
        store = RunStore(fake_hermes_factory())
        first = store.submit(brief())
        second = store.submit(brief(destination="Porto"))
        await first.task
        await second.task

        entries = await store.list()
        assert {entry.run_id for entry in entries} == {first.run_id, second.run_id}

    async def test_submit_exposes_the_live_run_before_completion(self):
        stage_started = asyncio.Event()
        release_stage = asyncio.Event()

        class BlockingResearch:
            stage = Stage.RESEARCH

            async def run(self, stage_input: TripBrief) -> StageResult:
                stage_started.set()
                await release_stage.wait()
                return StageResult(artifact=_bundle(stage_input))

        def factory(trip_brief: TripBrief) -> Hermes:
            return Hermes(adapters={Stage.RESEARCH: BlockingResearch()}, clock=FakeClock())

        store = RunStore(factory)
        entry = store.submit(brief())
        await stage_started.wait()

        assert entry.result is None
        assert entry.live_run is not None
        assert entry.live_run.id == entry.run_id
        assert entry.live_run.stages[-1].status is RunStatus.RUNNING

        release_stage.set()
        await entry.task


@dataclass(slots=True)
class _StubRecord:
    """Satisfies the structural shape a real storage backend hands back to
    :class:`RunStore` — just enough fields, no live task."""

    run_id: str
    brief: TripBrief
    status: RunStatus
    result: Run | None
    error: str | None


@dataclass
class FakeRunPersistence:
    """An in-process stand-in for :class:`~periplus.storage.PostgresRunPersistence`:
    finished runs land in a plain dict instead of Postgres, so :class:`RunStore`'s
    merge and fallback behaviour can be exercised without a real database.
    """

    rows: dict[str, _StubRecord] = field(default_factory=dict)
    opened: bool = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.opened = False

    async def save_started(self, *, run_id: str, brief: TripBrief) -> None:
        self.rows.setdefault(
            run_id,
            _StubRecord(
                run_id=run_id, brief=brief, status=RunStatus.RUNNING, result=None, error=None
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
        self.rows[run_id] = _StubRecord(
            run_id=run_id, brief=brief, status=status, result=result, error=error
        )

    async def load(self, run_id: str) -> _StubRecord | None:
        return self.rows.get(run_id)

    async def load_all(self) -> list[_StubRecord]:
        return list(self.rows.values())


class TestRunStorePersistence:
    async def test_finished_run_is_written_through(self):
        # The write is fire-and-forget from the task's done callback (see
        # RunStore._finish), so it can still be pending the instant ``entry.task``
        # resolves — a same-process caller never needs it since ``get``/``list`` read
        # the in-memory entry first, but this test has to wait for it explicitly. A row
        # also appears the moment the run is submitted (save_started), so "present" is
        # no longer enough to mean "finished" — wait for the terminal status itself.
        persistence = FakeRunPersistence()
        store = RunStore(fake_hermes_factory(), persistence=persistence)

        entry = store.submit(brief())
        await entry.task
        for _ in range(50):
            row = persistence.rows.get(entry.run_id)
            if row is not None and row.status != RunStatus.RUNNING:
                break
            await asyncio.sleep(0.01)

        assert entry.run_id in persistence.rows
        assert persistence.rows[entry.run_id].status == RunStatus.SUCCEEDED

    async def test_get_falls_back_to_a_persisted_run_not_in_memory(self):
        persistence = FakeRunPersistence()
        recovered = Run(brief=brief(), status=RunStatus.SUCCEEDED)
        persistence.rows[recovered.id] = _StubRecord(
            run_id=recovered.id,
            brief=recovered.brief,
            status=RunStatus.SUCCEEDED,
            result=recovered,
            error=None,
        )
        store = RunStore(fake_hermes_factory(), persistence=persistence)

        entry = await store.get(recovered.id)

        assert entry.result is recovered
        assert entry.task is None

    async def test_list_merges_live_and_persisted_entries(self):
        persistence = FakeRunPersistence()
        recovered = Run(brief=brief(), status=RunStatus.SUCCEEDED)
        persistence.rows[recovered.id] = _StubRecord(
            run_id=recovered.id,
            brief=recovered.brief,
            status=RunStatus.SUCCEEDED,
            result=recovered,
            error=None,
        )
        store = RunStore(fake_hermes_factory(), persistence=persistence)
        live = store.submit(brief(destination="Porto"))
        await live.task

        entries = await store.list()

        assert {entry.run_id for entry in entries} == {recovered.id, live.run_id}

    async def test_submit_records_a_started_row_before_the_task_finishes(self):
        # A one-shot fake adapter finishes too fast to reliably observe the row between
        # "started" and "finished" — gate it on an Event so the run genuinely cannot
        # complete until this test lets it.
        gate = asyncio.Event()

        class GatedResearch:
            stage = Stage.RESEARCH

            async def run(self, stage_input: object) -> StageResult:
                await gate.wait()
                return StageResult(artifact=_bundle(stage_input))

        def factory(trip_brief: TripBrief) -> Hermes:
            return Hermes(adapters={Stage.RESEARCH: GatedResearch()}, clock=FakeClock())

        persistence = FakeRunPersistence()
        store = RunStore(factory, persistence=persistence)

        entry = store.submit(brief())
        for _ in range(50):
            if entry.run_id in persistence.rows:
                break
            await asyncio.sleep(0.01)

        assert persistence.rows[entry.run_id].status == RunStatus.RUNNING

        gate.set()
        await entry.task


class OneShotVerify:
    """A minimal verify-stage adapter that always accepts what research produced."""

    stage = Stage.VERIFY

    async def run(self, stage_input: ResearchBundle) -> StageResult:
        verified_claims = [
            claim.model_copy(
                update={
                    "check": Check(
                        verdict=Verdict.SUPPORTED, confidence=1.0, reason="scripted pass"
                    )
                }
            )
            for claim in stage_input.claims
        ]
        bundle = VerifiedBundle(
            brief_id=stage_input.brief_id,
            claims=verified_claims,
            queries_run=stage_input.queries_run,
        )
        return StageResult(artifact=bundle)


@dataclass
class FakeArtifactStore:
    """An in-process stand-in for :class:`~periplus.storage.PostgresArtifactStore`: same
    surface :class:`RunStore` needs to resume a crashed run, minus any real Postgres —
    ``preload`` is a no-op since there is nowhere else the data could have gone.
    """

    memory: InMemoryArtifactStore = field(default_factory=InMemoryArtifactStore)

    def put(self, run_id, stage, attempt, artifact) -> None:
        self.memory.put(run_id, stage, attempt, artifact)

    def mark_passed(self, run_id, stage, attempt) -> None:
        self.memory.mark_passed(run_id, stage, attempt)

    def get(self, run_id, stage, attempt):
        return self.memory.get(run_id, stage, attempt)

    def latest_passed(self, run_id, stage):
        return self.memory.latest_passed(run_id, stage)

    async def open(self) -> None:
        pass

    async def close(self) -> None:
        pass

    async def preload(self, run_id: str) -> int:
        return 0


def two_stage_hermes_factory(artifacts: FakeArtifactStore | None = None):
    """Research-then-verify Hermes, both stages scripted to always pass their gate.

    ``artifacts`` mirrors production wiring (``build_hermes(brief=brief, artifacts=shared)``
    in :mod:`periplus.api.app`): the same store instance is reused across every call so a
    resumed run's fresh Hermes sees what a prior one retained, instead of each call
    getting its own empty :class:`~periplus.orchestrator.InMemoryArtifactStore`.
    """

    def factory(trip_brief: TripBrief) -> Hermes:
        return Hermes(
            adapters={
                Stage.RESEARCH: OneShotResearch(_bundle(trip_brief)),
                Stage.VERIFY: OneShotVerify(),
            },
            clock=FakeClock(),
            artifacts=artifacts,
        )

    return factory


def _bundle(trip_brief: TripBrief) -> ResearchBundle:
    return ResearchBundle(
        brief_id=trip_brief.id,
        claims=[Claim(id="claim-1", subject="x", text="y", kind=ClaimKind.OTHER)],
        queries_run=["q"],
    )


class TestRunStoreResume:
    async def test_open_resumes_a_run_stuck_running_from_the_last_passed_stage(self):
        crashed = brief(destination="Porto")
        persistence = FakeRunPersistence()
        persistence.rows[crashed.id] = _StubRecord(
            run_id=crashed.id, brief=crashed, status=RunStatus.RUNNING, result=None, error=None
        )
        artifacts = FakeArtifactStore()
        artifacts.put(crashed.id, Stage.RESEARCH, 1, _bundle(crashed))
        artifacts.mark_passed(crashed.id, Stage.RESEARCH, 1)

        store = RunStore(
            two_stage_hermes_factory(artifacts), persistence=persistence, artifacts=artifacts
        )
        await store.open()

        entry = await store.get(crashed.id)
        await entry.task

        assert entry.status == RunStatus.SUCCEEDED
        assert entry.result is not None
        assert entry.result.verified is not None
        # research was never re-run: only the retained artifact was reused as input.
        assert artifacts.get(crashed.id, Stage.RESEARCH, 2) is None

    async def test_open_marks_failed_when_nothing_ever_passed_a_gate(self):
        crashed = brief(destination="Faro")
        persistence = FakeRunPersistence()
        persistence.rows[crashed.id] = _StubRecord(
            run_id=crashed.id, brief=crashed, status=RunStatus.RUNNING, result=None, error=None
        )
        store = RunStore(
            two_stage_hermes_factory(), persistence=persistence, artifacts=FakeArtifactStore()
        )

        await store.open()

        assert crashed.id not in store._entries  # noqa: SLF001
        assert persistence.rows[crashed.id].status == RunStatus.FAILED

    async def test_open_leaves_finished_rows_alone(self):
        finished = brief(destination="Sintra")
        persistence = FakeRunPersistence()
        persistence.rows[finished.id] = _StubRecord(
            run_id=finished.id, brief=finished, status=RunStatus.SUCCEEDED, result=None, error=None
        )
        store = RunStore(
            two_stage_hermes_factory(), persistence=persistence, artifacts=FakeArtifactStore()
        )

        await store.open()

        assert finished.id not in store._entries  # noqa: SLF001


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
            submitted = response.json()
            assert "run" not in submitted
            assert submitted["brief"]["destination"] == "Lisbon"
            run_id = submitted["id"]

            final = wait_for_completion(client, run_id)
            assert final["status"] == "succeeded"
            assert final["counts"] == {
                "evidence": 0,
                "claims": 1,
                "verified_claims": 0,
                "itinerary_items": 0,
                "content_pieces": 0,
            }
            assert final["stages"][0]["stage"] == "research"
            assert final["stages"][0]["status"] == "succeeded"
            assert all(stage["status"] is None for stage in final["stages"][1:])

            result = client.get(f"/runs/{run_id}/result")
            assert result.status_code == 200
            assert result.json()["run"]["research"] is not None

    def test_running_summary_exposes_real_stage_and_result_is_not_ready(self):
        stage_started = threading.Event()
        release_stage = threading.Event()

        class BlockingResearch:
            stage = Stage.RESEARCH

            async def run(self, stage_input: TripBrief) -> StageResult:
                stage_started.set()
                await asyncio.to_thread(release_stage.wait)
                return StageResult(artifact=_bundle(stage_input))

        def factory(trip_brief: TripBrief) -> Hermes:
            return Hermes(adapters={Stage.RESEARCH: BlockingResearch()}, clock=FakeClock())

        app = create_app(run_store=RunStore(factory))
        with TestClient(app) as client:
            submitted = client.post(
                "/trips",
                json={
                    "destination": "Lisbon",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-03",
                },
            ).json()
            assert stage_started.wait(timeout=1)

            try:
                summary = client.get(f"/runs/{submitted['id']}").json()
                assert summary["status"] == "running"
                assert summary["current_stage"] == "research"
                assert summary["stages"][0]["status"] == "running"
                assert summary["stages"][0]["attempt"] == 1
                assert "run" not in summary
                assert client.get(f"/runs/{submitted['id']}/result").status_code == 409
            finally:
                release_stage.set()
            wait_for_completion(client, submitted["id"])

    def test_unknown_run_returns_404(self):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        with TestClient(app) as client:
            assert client.get("/runs/ghost").status_code == 404
            assert client.get("/runs/ghost/result").status_code == 404

    @pytest.mark.parametrize(
        ("server_owned_field", "value"),
        [("id", "client-value"), ("created_at", "2026-08-11T10:00:00Z")],
    )
    def test_rejects_server_owned_fields_in_trip_body(self, server_owned_field: str, value: str):
        app = create_app(run_store=RunStore(fake_hermes_factory()))
        body = {
            "destination": "Lisbon",
            "start_date": "2026-09-01",
            "end_date": "2026-09-03",
            server_owned_field: value,
        }
        with TestClient(app) as client:
            assert client.post("/trips", json=body).status_code == 422

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

    def test_list_runs_includes_lightweight_summaries(self):
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
            wait_for_completion(client, submitted["id"])

            listing = client.get("/runs").json()
            entry = next(item for item in listing if item["id"] == submitted["id"])
            assert entry["brief"]["destination"] == "Lisbon"
            assert "run" not in entry

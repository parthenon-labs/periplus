"""Tests for the structured logging seam and the health endpoint.

The point of both is that a run in trouble says so at the moment it is in trouble, rather
than only after the fact through ``/runs/{id}``. So these assert on what a log collector
would actually receive — parsed lines with named fields — not merely that a logger was
called.
"""

from __future__ import annotations

import json
import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient

from periplus.api.app import create_app
from periplus.api.runs import RunStore
from periplus.api.schemas import HealthView
from periplus.models import ResearchBundle, Stage, TripBrief
from periplus.observability import (
    LOGGER_NAME,
    JSONFormatter,
    TextFormatter,
    configure_logging,
)
from periplus.orchestrator import FakeClock, Hermes, StageResult


def brief(**overrides) -> TripBrief:
    values = {
        "destination": "Lisbon",
        "start_date": date(2026, 9, 1),
        "end_date": date(2026, 9, 3),
    }
    values.update(overrides)
    return TripBrief(**values)


def record(**context) -> logging.LogRecord:
    made = logging.LogRecord(
        name="periplus.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="stage succeeded",
        args=(),
        exc_info=None,
    )
    if context:
        made.context = context
    return made


class TestJSONFormatter:
    def test_emits_one_parseable_object_per_event(self):
        line = JSONFormatter().format(record(run_id="abc", stage="research", attempt=2))
        payload = json.loads(line)

        assert payload["event"] == "stage succeeded"
        assert payload["level"] == "info"
        assert payload["logger"] == "periplus.test"
        assert payload["run_id"] == "abc"
        assert payload["attempt"] == 2
        assert "ts" in payload

    def test_survives_an_event_with_no_context_at_all(self):
        payload = json.loads(JSONFormatter().format(record()))
        assert payload["event"] == "stage succeeded"

    def test_a_traceback_is_one_field_not_trailing_lines(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys

            made = record(run_id="abc")
            made.exc_info = sys.exc_info()
            line = JSONFormatter().format(made)

        assert "\n" not in line, "a multi-line record would read as several events"
        assert "boom" in json.loads(line)["error"]


class TestTextFormatter:
    def test_appends_context_as_key_values(self):
        line = TextFormatter().format(record(run_id="abc", stage="verify"))
        assert "stage succeeded" in line
        assert "run_id=abc" in line
        assert "stage=verify" in line


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def _restore(self):
        logger = logging.getLogger(LOGGER_NAME)
        handlers, level, propagate = list(logger.handlers), logger.level, logger.propagate
        yield
        logger.handlers = handlers
        logger.setLevel(level)
        logger.propagate = propagate

    def test_repeated_calls_do_not_duplicate_every_line(self):
        logger = logging.getLogger(LOGGER_NAME)
        logger.handlers = []

        configure_logging(level="INFO", log_format="json")
        configure_logging(level="DEBUG", log_format="text")

        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, TextFormatter)
        assert logger.level == logging.DEBUG

    def test_records_do_not_also_reach_the_root_logger(self):
        logger = logging.getLogger(LOGGER_NAME)
        logger.handlers = []
        configure_logging()

        assert logger.propagate is False


class OneShotResearch:
    stage = Stage.RESEARCH

    async def run(self, stage_input: object) -> StageResult:
        return StageResult(artifact=ResearchBundle(brief_id="brief-1"))


@pytest.fixture
def captured() -> list[logging.LogRecord]:
    """Records as this application's own logger tree sees them.

    Attached directly rather than through ``caplog``, which listens on the root logger:
    ``configure_logging`` sets ``propagate = False`` precisely so records do not go
    there, and whether it has run yet depends on whether some earlier test built an app.
    """
    logger = logging.getLogger(LOGGER_NAME)
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = Capture(level=logging.DEBUG)
    level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)


class TestOrchestratorEvents:
    """A stuck run has to be diagnosable from the log alone."""

    async def test_a_gated_off_stage_names_the_run_the_stage_and_the_reason(self, captured):
        hermes = Hermes(adapters={Stage.RESEARCH: OneShotResearch()}, clock=FakeClock())

        run = await hermes.start(brief())

        events = {entry.getMessage(): entry for entry in captured}
        assert "run started" in events
        assert "stage started" in events
        # research_gate rejects a bundle with no claims.
        rejected = events["stage rejected by its gate"]
        assert rejected.context["run_id"] == run.id
        assert rejected.context["stage"] == "research"
        assert rejected.context["reason"] == "research produced no grounded claims"
        assert events["run ended early"].context["status"] == "failed"

    async def test_no_prompt_or_claim_text_ever_reaches_the_log(self, captured):
        hermes = Hermes(adapters={Stage.RESEARCH: OneShotResearch()}, clock=FakeClock())

        await hermes.start(brief(destination="Lisbon"))

        for entry in captured:
            fields = set(getattr(entry, "context", {}))
            assert not fields & {"prompt", "text", "snippet", "body", "api_key"}


class TestHealthView:
    def test_status_is_derived_from_the_checks_not_set_beside_them(self):
        assert HealthView.from_checks({"persistence": "ok"}, version="0.1.0").status == "ok"
        degraded = HealthView.from_checks(
            {"persistence": "unavailable: OperationalError"}, version="0.1.0"
        )
        assert degraded.status == "degraded"

    def test_a_dependency_that_is_simply_absent_is_not_a_failure(self):
        view = HealthView.from_checks({"persistence": "not configured"}, version="0.1.0")
        assert view.is_healthy


class FailingPersistence:
    """Opens fine and then refuses to answer — the shape of a database that went away
    under a pool that still looks healthy."""

    async def open(self) -> None: ...

    async def close(self) -> None: ...

    async def save_started(self, **kwargs) -> None: ...

    async def save_finished(self, **kwargs) -> None: ...

    async def load(self, run_id: str):
        return None

    async def load_all(self):
        return []

    async def ping(self) -> None:
        raise ConnectionError("server closed the connection unexpectedly")


class TestHealthEndpoint:
    def test_reports_ok_when_there_is_nothing_that_could_be_down(self):
        app = create_app(run_store=RunStore(lambda _: None))

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {
            "status": "ok",
            "version": "0.1.0",
            "checks": {"persistence": "not configured"},
        }

    def test_an_unreachable_database_answers_503(self):
        app = create_app(run_store=RunStore(lambda _: None, persistence=FailingPersistence()))

        with TestClient(app) as client:
            response = client.get("/health")

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["persistence"] == "unavailable: ConnectionError"

    def test_the_reason_never_carries_the_connection_string(self):
        app = create_app(run_store=RunStore(lambda _: None, persistence=FailingPersistence()))

        with TestClient(app) as client:
            body = client.get("/health").json()

        # The exception's message mentions the server; only its type may be reported.
        assert "server closed" not in body["checks"]["persistence"]

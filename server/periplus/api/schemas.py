"""Request and response shapes for the HTTP layer."""

from __future__ import annotations

from datetime import date, datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from periplus.api.runs import RunEntry
from periplus.models import Budget, Pace, Party, Run, RunStatus, Stage, TripBrief
from periplus.orchestrator import STAGE_ORDER


class TripBriefCreate(BaseModel):
    """Caller-owned fields for a trip; identity and timestamps stay server-owned."""

    model_config = ConfigDict(extra="forbid")

    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    party: Party = Field(default_factory=Party)
    budget: Budget = Field(default_factory=Budget)
    interests: list[str] = Field(default_factory=list)
    must_see: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    pace: Pace = Pace.BALANCED
    base_location: str | None = None
    language: str = "en"
    notes: str | None = None

    @field_validator("destination", mode="before")
    @classmethod
    def _strip_destination(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _check_dates(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self

    def to_trip_brief(self) -> TripBrief:
        return TripBrief(**self.model_dump())


class TripBriefSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    destination: str
    start_date: date
    end_date: date


class StageProgress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: Stage
    status: RunStatus | None = None
    attempt: int | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None


class RunCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: int = 0
    claims: int = 0
    verified_claims: int = 0
    itinerary_items: int = 0
    content_pieces: int = 0

    @classmethod
    def from_run(cls, run: Run | None) -> RunCounts:
        if run is None:
            return cls()

        bundle = run.itinerary or run.verified or run.research
        claims = bundle.claims if bundle is not None else []
        evidence = bundle.evidence if bundle is not None else []
        itinerary_items = (
            sum(len(day.items) for day in run.itinerary.days) if run.itinerary is not None else 0
        )
        content_pieces = len(run.content.pieces) if run.content is not None else 0
        return cls(
            evidence=len(evidence),
            claims=len(claims),
            verified_claims=sum(claim.check is not None for claim in claims),
            itinerary_items=itinerary_items,
            content_pieces=content_pieces,
        )


class RunSummary(BaseModel):
    """Small polling payload derived from Hermes' live Run without stage artifacts."""

    model_config = ConfigDict(extra="forbid")

    id: str
    status: RunStatus
    error: str | None = None
    brief: TripBriefSummary
    current_stage: Stage | None = None
    stages: list[StageProgress]
    counts: RunCounts
    created_at: datetime
    finished_at: datetime | None = None

    @classmethod
    def from_entry(cls, entry: RunEntry) -> RunSummary:
        run = entry.result or entry.live_run
        latest_by_stage = {
            stage: run.stage_run(stage) if run is not None else None for stage in STAGE_ORDER
        }
        current_stage = None
        if run is not None:
            current = next(
                (
                    stage_run
                    for stage_run in reversed(run.stages)
                    if stage_run.status is RunStatus.RUNNING
                ),
                None,
            )
            current_stage = current.stage if current is not None else None

        stages = [
            StageProgress(
                stage=stage,
                status=stage_run.status if stage_run is not None else None,
                attempt=stage_run.attempt if stage_run is not None else None,
                started_at=stage_run.started_at if stage_run is not None else None,
                finished_at=stage_run.finished_at if stage_run is not None else None,
                error=stage_run.error if stage_run is not None else None,
            )
            for stage, stage_run in latest_by_stage.items()
        ]
        return cls(
            id=entry.run_id,
            status=entry.status,
            error=entry.error,
            brief=TripBriefSummary(
                destination=entry.brief.destination,
                start_date=entry.brief.start_date,
                end_date=entry.brief.end_date,
            ),
            current_stage=current_stage,
            stages=stages,
            counts=RunCounts.from_run(run),
            created_at=run.created_at if run is not None else entry.brief.created_at,
            finished_at=run.finished_at if run is not None else None,
        )


class RunResultView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: RunStatus
    error: str | None = None
    run: Run | None = None

    @classmethod
    def from_entry(cls, entry: RunEntry) -> RunResultView:
        return cls(id=entry.run_id, status=entry.status, error=entry.error, run=entry.result)

"""Domain model for the Periplus pipeline.

Every stage boundary in the pipeline is one of the artifacts defined here. They are
plain Pydantic models with no I/O and no model-provider awareness, so a stage can be
replayed against a stored artifact without rerunning the stages before it.

The load-bearing idea: a factual assertion never travels as prose. It travels as a
``Claim`` bound to the ``Evidence`` it came from, and it does not reach a traveller
until a ``Verdict`` has been assigned by a stage that did not produce it.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from enum import StrEnum
from typing import Annotated
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


def _uuid() -> str:
    return uuid4().hex


def _now() -> datetime:
    return datetime.now(UTC)


class Artifact(BaseModel):
    """Base for everything persisted at a stage boundary."""

    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# --------------------------------------------------------------------------------------
# Brief — what the traveller asked for
# --------------------------------------------------------------------------------------


class Pace(StrEnum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    PACKED = "packed"


class Party(Artifact):
    """Who is travelling. Shapes both what is proposed and what is ruled out."""

    adults: int = Field(default=1, ge=0)
    children: int = Field(default=0, ge=0)
    child_ages: list[int] = Field(default_factory=list)
    mobility_notes: str | None = None

    @property
    def size(self) -> int:
        return self.adults + self.children


class Budget(Artifact):
    currency: str = Field(default="AUD", min_length=3, max_length=3)
    total: float | None = Field(default=None, ge=0)
    per_day: float | None = Field(default=None, ge=0)


class TripBrief(Artifact):
    """The pipeline's input. Everything downstream is traceable to one of these."""

    id: str = Field(default_factory=_uuid)
    created_at: datetime = Field(default_factory=_now)

    destination: str
    start_date: date
    end_date: date
    party: Party = Field(default_factory=Party)
    budget: Budget = Field(default_factory=Budget)

    interests: list[str] = Field(default_factory=list)
    must_see: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    pace: Pace = Pace.BALANCED
    base_location: str | None = Field(
        default=None, description="Hotel or neighbourhood days start and end from."
    )
    language: str = Field(default="en", description="Language of the produced content.")
    notes: str | None = None

    @model_validator(mode="after")
    def _check_dates(self) -> TripBrief:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self

    @property
    def nights(self) -> int:
        return (self.end_date - self.start_date).days

    @property
    def days(self) -> int:
        return self.nights + 1


# --------------------------------------------------------------------------------------
# Evidence and claims — the provenance layer
# --------------------------------------------------------------------------------------


class SourceKind(StrEnum):
    OFFICIAL = "official"
    """Operator's own site: the venue, the transit authority, the park service."""
    GOVERNMENT = "government"
    ENCYCLOPEDIA = "encyclopedia"
    GUIDE = "guide"
    """Editorial travel media."""
    REVIEW = "review"
    """User-generated: forums, review platforms, social."""
    MAP = "map"
    UNKNOWN = "unknown"


class Evidence(Artifact):
    """A retrieved passage, kept verbatim so verification has something to check against."""

    id: str = Field(default_factory=_uuid)
    url: HttpUrl
    title: str | None = None
    snippet: str = Field(description="Verbatim excerpt. Never a model paraphrase.")
    source_kind: SourceKind = SourceKind.UNKNOWN
    published_at: date | None = Field(
        default=None, description="Publication or last-updated date, when the page states one."
    )
    fetched_at: datetime = Field(default_factory=_now)
    query: str | None = Field(default=None, description="Search query that surfaced this.")

    @property
    def domain(self) -> str:
        return self.url.host or ""


class ClaimKind(StrEnum):
    """Determines how volatile a claim is, and therefore how fast its evidence rots."""

    HOURS = "hours"
    PRICE = "price"
    AVAILABILITY = "availability"
    """Seasonal closure, booking requirement, capacity limit."""
    TRANSIT = "transit"
    LOCATION = "location"
    DESCRIPTION = "description"
    SAFETY = "safety"
    OTHER = "other"


#: How old evidence may be before a claim of this kind is downgraded to ``stale``.
FRESHNESS_DAYS: dict[ClaimKind, int | None] = {
    ClaimKind.HOURS: 180,
    ClaimKind.PRICE: 180,
    ClaimKind.AVAILABILITY: 90,
    ClaimKind.TRANSIT: 365,
    ClaimKind.SAFETY: 90,
    ClaimKind.LOCATION: None,
    ClaimKind.DESCRIPTION: None,
    ClaimKind.OTHER: 365,
}


class Verdict(StrEnum):
    SUPPORTED = "supported"
    PARTIAL = "partial"
    CONTRADICTED = "contradicted"
    UNSUPPORTED = "unsupported"
    """Evidence exists but does not address the claim."""
    NO_EVIDENCE = "no_evidence"
    STALE = "stale"
    """Evidence supports the claim but predates its freshness window."""


#: Verdicts a claim may hold and still be scheduled into an itinerary.
USABLE_VERDICTS: frozenset[Verdict] = frozenset(
    {Verdict.SUPPORTED, Verdict.PARTIAL, Verdict.STALE}
)


class Check(Artifact):
    """The Auditor's judgement on one claim. Written by verification, never by research."""

    verdict: Verdict
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="One sentence, grounded in the cited evidence.")
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    model: str | None = None
    checked_at: datetime = Field(default_factory=_now)


class Claim(Artifact):
    """One falsifiable assertion, with its provenance and — after verification — its verdict."""

    id: str = Field(default_factory=_uuid)
    subject: str = Field(description="What the claim is about, e.g. 'Museo del Prado'.")
    text: str = Field(description="A single assertion. Split anything compound.")
    kind: ClaimKind = ClaimKind.OTHER
    evidence_ids: list[str] = Field(default_factory=list)
    check: Check | None = None

    @property
    def verdict(self) -> Verdict:
        if self.check is not None:
            return self.check.verdict
        return Verdict.NO_EVIDENCE if not self.evidence_ids else Verdict.UNSUPPORTED

    @property
    def is_verified(self) -> bool:
        return self.check is not None

    @property
    def is_usable(self) -> bool:
        return self.is_verified and self.verdict in USABLE_VERDICTS

    def freshness_days(self) -> int | None:
        return FRESHNESS_DAYS.get(self.kind)


# --------------------------------------------------------------------------------------
# Places — what research found
# --------------------------------------------------------------------------------------


class PlaceKind(StrEnum):
    SIGHT = "sight"
    MUSEUM = "museum"
    OUTDOORS = "outdoors"
    FOOD = "food"
    NIGHTLIFE = "nightlife"
    SHOPPING = "shopping"
    LODGING = "lodging"
    TRANSPORT = "transport"
    EVENT = "event"
    OTHER = "other"


Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class GeoPoint(Artifact):
    lat: Latitude
    lon: Longitude


class Place(Artifact):
    id: str = Field(default_factory=_uuid)
    name: str
    kind: PlaceKind = PlaceKind.OTHER
    address: str | None = None
    point: GeoPoint | None = None
    summary: str | None = Field(
        default=None, description="Why this belongs in this trip, in one or two sentences."
    )
    claim_ids: list[str] = Field(default_factory=list)
    typical_duration_minutes: int | None = Field(default=None, ge=0)
    tags: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------
# Stage artifacts
# --------------------------------------------------------------------------------------


class Stage(StrEnum):
    RESEARCH = "research"
    VERIFY = "verify"
    PLAN = "plan"
    WRITE = "write"


class ResearchBundle(Artifact):
    """Output of the Explorer. Claims here are unverified by construction."""

    brief_id: str
    places: list[Place] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    queries_run: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(
        default_factory=list, description="What the Explorer looked for and could not find."
    )
    created_at: datetime = Field(default_factory=_now)

    def evidence_by_id(self) -> dict[str, Evidence]:
        return {e.id: e for e in self.evidence}

    def claims_for(self, place: Place) -> list[Claim]:
        wanted = set(place.claim_ids)
        return [c for c in self.claims if c.id in wanted]


class VerifiedBundle(ResearchBundle):
    """Output of the Auditor: the same bundle, with every claim carrying a check."""

    verified_at: datetime = Field(default_factory=_now)

    @property
    def usable_claims(self) -> list[Claim]:
        return [c for c in self.claims if c.is_usable]

    @property
    def rejected_claims(self) -> list[Claim]:
        return [c for c in self.claims if not c.is_usable]

    def verdict_counts(self) -> dict[Verdict, int]:
        counts: dict[Verdict, int] = {v: 0 for v in Verdict}
        for claim in self.claims:
            counts[claim.verdict] += 1
        return counts


class Transfer(Artifact):
    """How the traveller gets from the previous item to this one."""

    mode: str = Field(description="walk, metro, bus, train, ferry, taxi, drive")
    minutes: int = Field(ge=0)
    detail: str | None = None
    claim_ids: list[str] = Field(default_factory=list)


class ItineraryItem(Artifact):
    id: str = Field(default_factory=_uuid)
    day: int = Field(ge=1)
    start: time | None = None
    end: time | None = None
    title: str
    place_id: str | None = None
    notes: str | None = None
    claim_ids: list[str] = Field(
        default_factory=list, description="Every fact this item rests on."
    )
    transfer_in: Transfer | None = None
    booking_required: bool = False
    estimated_cost: float | None = Field(default=None, ge=0)


class ItineraryDay(Artifact):
    day: int = Field(ge=1)
    date: date
    theme: str | None = None
    items: list[ItineraryItem] = Field(default_factory=list)


class Itinerary(Artifact):
    """Output of the Navigator. Every item is traceable to usable claims."""

    id: str = Field(default_factory=_uuid)
    brief_id: str
    destination: str
    days: list[ItineraryDay] = Field(default_factory=list)
    places: list[Place] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    caveats: list[str] = Field(
        default_factory=list,
        description="Contradicted or stale facts the traveller should re-check on the day.",
    )
    created_at: datetime = Field(default_factory=_now)


class ContentPiece(Artifact):
    kind: str = Field(description="itinerary_doc, article, thread, captions, checklist")
    title: str | None = None
    body: str
    word_count: int | None = Field(default=None, ge=0)


class ContentSet(Artifact):
    """Output of the Chronicler: the human-facing artifacts."""

    itinerary_id: str
    pieces: list[ContentPiece] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_now)


# --------------------------------------------------------------------------------------
# Run bookkeeping — owned by Hermes
# --------------------------------------------------------------------------------------


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ModelCall(Artifact):
    """One recorded LLM invocation. The audit trail behind every produced fact."""

    id: str = Field(default_factory=_uuid)
    stage: Stage
    model: str
    prompt_hash: str
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    error: str | None = None
    called_at: datetime = Field(default_factory=_now)


class StageRun(Artifact):
    stage: Stage
    status: RunStatus = RunStatus.PENDING
    attempt: int = Field(default=1, ge=1)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    error: str | None = None
    calls: list[ModelCall] = Field(default_factory=list)

    @property
    def duration_ms(self) -> int | None:
        if self.started_at is None or self.finished_at is None:
            return None
        return int((self.finished_at - self.started_at).total_seconds() * 1000)


class Run(Artifact):
    """One end-to-end execution, with each stage boundary retained for replay."""

    id: str = Field(default_factory=_uuid)
    brief: TripBrief
    status: RunStatus = RunStatus.PENDING
    stages: list[StageRun] = Field(default_factory=list)
    research: ResearchBundle | None = None
    verified: VerifiedBundle | None = None
    itinerary: Itinerary | None = None
    content: ContentSet | None = None
    created_at: datetime = Field(default_factory=_now)
    finished_at: datetime | None = None

    def stage_run(self, stage: Stage) -> StageRun | None:
        for entry in reversed(self.stages):
            if entry.stage is stage:
                return entry
        return None

    @property
    def total_tokens(self) -> int:
        return sum(
            call.prompt_tokens + call.completion_tokens
            for entry in self.stages
            for call in entry.calls
        )

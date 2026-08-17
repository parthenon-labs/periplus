"""What a run is worth, as numbers.

Everything here is a pure function of a finished :class:`~periplus.models.Run`. No I/O, no
provider awareness, no knowledge of how the run was produced — which is the whole point:
the same metrics describe a run driven by scripted replies offline and a run driven by a
live provider, so a threshold set on one is meaningful against the other.

Two families of number live here, and confusing them is the fastest way to make an eval
suite useless:

* **Grounding metrics** — verdict distribution, gap rate, citation integrity. These
  describe whether the pipeline let an unverified fact through. ``gap_rate`` is
  deliberately defined as *everything outside*
  :data:`~periplus.models.USABLE_VERDICTS`, so it moves the moment that set changes rather
  than silently drifting from it.
* **Cost metrics** — tokens, spend, latency, retries. These come from the
  :class:`~periplus.models.ModelCall` ledger the pipeline already records, not from a
  re-estimate, so an eval report and a production run cost the same way.

:meth:`RunMetrics.flat` exists so a threshold can name any metric as a string
(``"gap_rate"``, ``"verdict.contradicted"``, ``"stage.verify.tokens"``) without this module
and the case format having to agree on a schema. A typo in a case file then fails loudly as
an unknown metric instead of quietly passing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from periplus.models import (
    USABLE_VERDICTS,
    Claim,
    ContentSet,
    IllustratedContentSet,
    Itinerary,
    ResearchBundle,
    Run,
    RunStatus,
    Stage,
    Verdict,
)

__all__ = [
    "CitationFailure",
    "CitationFailureKind",
    "RunMetrics",
    "StageMetrics",
    "citation_failures",
]


class CitationFailureKind(StrEnum):
    """The four ways provenance can be broken, kept distinct so a report says which."""

    DANGLING_EVIDENCE = "dangling_evidence"
    """A claim cites an evidence ID that is not in the bundle it travelled with."""
    DANGLING_CLAIM = "dangling_claim"
    """A downstream artifact cites a claim ID that artifact does not carry."""
    UNUSABLE_CLAIM = "unusable_claim"
    """A downstream artifact states as fact a claim whose verdict forbids it."""
    UNVERIFIED_CLAIM = "unverified_claim"
    """A claim reached a post-verification artifact with no verdict at all."""


@dataclass(frozen=True, slots=True)
class CitationFailure:
    kind: CitationFailureKind
    where: str
    """Artifact and element, e.g. ``"itinerary.day3.item"`` or ``"content.piece"``."""
    detail: str

    def __str__(self) -> str:
        return f"{self.kind.value} at {self.where}: {self.detail}"


@dataclass(frozen=True, slots=True)
class StageMetrics:
    stage: Stage
    attempts: int = 0
    failed_attempts: int = 0
    status: RunStatus | None = None
    """Status of the last attempt — what the run actually ended up with for this stage."""
    calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    queries: int = 0
    fetches: int = 0

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _usable(claim: Claim) -> bool:
    return claim.is_verified and claim.verdict in USABLE_VERDICTS


def _check_claim_refs(
    claim_ids: list[str],
    *,
    available: dict[str, Claim],
    where: str,
    require_usable: bool,
) -> list[CitationFailure]:
    """Resolve one artifact element's claim references against what it carries.

    ``require_usable`` is False only before verification has run: a research bundle's own
    claims are unverified by construction, and calling that a citation failure would make
    every research-only eval case fail for doing exactly what it promises.
    """
    failures: list[CitationFailure] = []
    for claim_id in claim_ids:
        claim = available.get(claim_id)
        if claim is None:
            failures.append(
                CitationFailure(
                    kind=CitationFailureKind.DANGLING_CLAIM,
                    where=where,
                    detail=f"claim {claim_id} is not carried by this artifact",
                )
            )
            continue
        if not require_usable:
            continue
        if not claim.is_verified:
            failures.append(
                CitationFailure(
                    kind=CitationFailureKind.UNVERIFIED_CLAIM,
                    where=where,
                    detail=f"claim {claim_id} ({claim.subject}) carries no verdict",
                )
            )
        elif claim.verdict not in USABLE_VERDICTS:
            failures.append(
                CitationFailure(
                    kind=CitationFailureKind.UNUSABLE_CLAIM,
                    where=where,
                    detail=(
                        f"claim {claim_id} ({claim.subject}) is stated as fact with "
                        f"verdict {claim.verdict.value}"
                    ),
                )
            )
    return failures


def _bundle_failures(bundle: ResearchBundle, *, label: str) -> list[CitationFailure]:
    known_evidence = {item.id for item in bundle.evidence}
    failures: list[CitationFailure] = []
    for claim in bundle.claims:
        for evidence_id in claim.evidence_ids:
            if evidence_id not in known_evidence:
                failures.append(
                    CitationFailure(
                        kind=CitationFailureKind.DANGLING_EVIDENCE,
                        where=f"{label}.claim",
                        detail=f"claim {claim.id} ({claim.subject}) cites absent {evidence_id}",
                    )
                )
    return failures


def _itinerary_failures(itinerary: Itinerary) -> list[CitationFailure]:
    available = {claim.id: claim for claim in itinerary.claims}
    failures = _bundle_failures(
        ResearchBundle(
            brief_id=itinerary.brief_id,
            claims=itinerary.claims,
            evidence=itinerary.evidence,
        ),
        label="itinerary",
    )
    for day in itinerary.days:
        for item in day.items:
            where = f"itinerary.day{day.day}.{item.title}"
            failures.extend(
                _check_claim_refs(
                    item.claim_ids, available=available, where=where, require_usable=True
                )
            )
            if item.transfer_in is not None:
                failures.extend(
                    _check_claim_refs(
                        item.transfer_in.claim_ids,
                        available=available,
                        where=f"{where}.transfer",
                        require_usable=True,
                    )
                )
    return failures


def _content_failures(content: ContentSet, *, label: str) -> list[CitationFailure]:
    available = {claim.id: claim for claim in content.claims}
    failures: list[CitationFailure] = []
    for piece in content.pieces:
        failures.extend(
            _check_claim_refs(
                piece.claim_ids,
                available=available,
                where=f"{label}.{piece.kind}",
                require_usable=True,
            )
        )
    if isinstance(content, IllustratedContentSet):
        for image in content.images:
            failures.extend(
                _check_claim_refs(
                    image.claim_ids,
                    available=available,
                    where=f"{label}.image.{image.subject}",
                    require_usable=True,
                )
            )
    return failures


def citation_failures(run: Run) -> list[CitationFailure]:
    """Every broken provenance link in ``run``, across whichever stages actually ran.

    This is the invariant the whole pipeline exists to hold: nothing states a fact it
    cannot trace to a passage, and nothing downstream of verification states a fact whose
    verdict forbids it. An eval case asserting ``citation_failures == 0`` is asserting that
    invariant survived a real run, which is a stronger claim than any prompt wording.
    """
    failures: list[CitationFailure] = []

    # The research bundle is checked for dangling evidence only. Its claims have no
    # verdicts yet by design, so usability is not a question that applies to it.
    if run.research is not None and run.verified is None:
        failures.extend(_bundle_failures(run.research, label="research"))
    if run.verified is not None:
        failures.extend(_bundle_failures(run.verified, label="verified"))
        for claim in run.verified.claims:
            if not claim.is_verified:
                failures.append(
                    CitationFailure(
                        kind=CitationFailureKind.UNVERIFIED_CLAIM,
                        where="verified.claim",
                        detail=f"claim {claim.id} ({claim.subject}) left verification unchecked",
                    )
                )
    if run.itinerary is not None:
        failures.extend(_itinerary_failures(run.itinerary))
    if run.content is not None:
        failures.extend(_content_failures(run.content, label="content"))
    if run.edited is not None:
        failures.extend(_content_failures(run.edited, label="edited"))
    if run.illustrated is not None:
        failures.extend(_content_failures(run.illustrated, label="illustrated"))
    return failures


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """One run, reduced to the numbers an eval threshold can be set against."""

    run_id: str
    status: RunStatus
    stages_completed: tuple[Stage, ...] = ()

    claims: int = 0
    evidence: int = 0
    places: int = 0
    verdicts: dict[Verdict, int] = field(default_factory=dict)
    unverified_claims: int = 0
    reported_gaps: int = 0

    citation_failures: tuple[CitationFailure, ...] = ()

    itinerary_items: int = 0
    content_pieces: int = 0
    images: int = 0

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    model_calls: int = 0
    failed_model_calls: int = 0
    stage_retries: int = 0
    queries: int = 0
    fetches: int = 0

    stages: dict[Stage, StageMetrics] = field(default_factory=dict)
    prompt_hashes: dict[Stage, tuple[str, ...]] = field(default_factory=dict)
    """Distinct prompt digests per stage, in first-seen order.

    Carried into the report so "the metric moved" can always be traced to "the prompt
    changed" — the pairing that makes a before/after diff evidence rather than an anecdote.
    """

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def usable_claims(self) -> int:
        return sum(count for verdict, count in self.verdicts.items() if verdict in USABLE_VERDICTS)

    @property
    def gap_rate(self) -> float:
        """Share of claims a traveller may not be shown. Zero claims is a full gap, not 0.0.

        A run that produced nothing has not achieved a perfect gap rate, and returning 0.0
        there would let an empty run satisfy a ``gap_rate <= 0.3`` threshold — the exact
        regression an eval suite must catch.
        """
        if not self.claims:
            return 1.0
        return round((self.claims - self.usable_claims) / self.claims, 6)

    @property
    def usable_rate(self) -> float:
        if not self.claims:
            return 0.0
        return round(self.usable_claims / self.claims, 6)

    @property
    def citation_failure_count(self) -> int:
        return len(self.citation_failures)

    def flat(self) -> dict[str, float]:
        """Every metric as a flat, threshold-addressable mapping.

        Names are stable and are the contract a case file writes against:
        ``gap_rate``, ``verdict.<name>``, ``stage.<name>.<field>``.
        """
        values: dict[str, float] = {
            "claims": self.claims,
            "evidence": self.evidence,
            "places": self.places,
            "unverified_claims": self.unverified_claims,
            "reported_gaps": self.reported_gaps,
            "usable_claims": self.usable_claims,
            "gap_rate": self.gap_rate,
            "usable_rate": self.usable_rate,
            "citation_failures": self.citation_failure_count,
            "itinerary_items": self.itinerary_items,
            "content_pieces": self.content_pieces,
            "images": self.images,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "model_calls": self.model_calls,
            "failed_model_calls": self.failed_model_calls,
            "stage_retries": self.stage_retries,
            "queries": self.queries,
            "fetches": self.fetches,
        }
        for verdict in Verdict:
            values[f"verdict.{verdict.value}"] = self.verdicts.get(verdict, 0)
        for stage, metrics in self.stages.items():
            prefix = f"stage.{stage.value}"
            values[f"{prefix}.attempts"] = metrics.attempts
            values[f"{prefix}.failed_attempts"] = metrics.failed_attempts
            values[f"{prefix}.calls"] = metrics.calls
            values[f"{prefix}.failed_calls"] = metrics.failed_calls
            values[f"{prefix}.tokens"] = metrics.tokens
            values[f"{prefix}.cost_usd"] = metrics.cost_usd
            values[f"{prefix}.latency_ms"] = metrics.latency_ms
            values[f"{prefix}.queries"] = metrics.queries
            values[f"{prefix}.fetches"] = metrics.fetches
        return values


def _stage_metrics(run: Run) -> dict[Stage, StageMetrics]:
    """Fold every recorded attempt into one entry per stage.

    Attempts accumulate (a retried stage cost both attempts), while ``status`` reflects
    only the last one — the state the run actually finished that stage in.
    """
    ordered: dict[Stage, StageMetrics] = {}
    for entry in run.stages:
        current = ordered.get(entry.stage, StageMetrics(stage=entry.stage))
        failed = entry.status in {RunStatus.FAILED, RunStatus.CANCELLED}
        ordered[entry.stage] = StageMetrics(
            stage=entry.stage,
            attempts=current.attempts + 1,
            failed_attempts=current.failed_attempts + (1 if failed else 0),
            status=entry.status,
            calls=current.calls + len(entry.calls),
            failed_calls=current.failed_calls + sum(1 for c in entry.calls if c.error),
            prompt_tokens=current.prompt_tokens + sum(c.prompt_tokens for c in entry.calls),
            completion_tokens=current.completion_tokens
            + sum(c.completion_tokens for c in entry.calls),
            cost_usd=round(current.cost_usd + sum(c.cost_usd or 0.0 for c in entry.calls), 8),
            latency_ms=current.latency_ms + sum(c.latency_ms for c in entry.calls),
            queries=current.queries + entry.queries,
            fetches=current.fetches + entry.fetches,
        )
    return ordered


def _prompt_hashes(run: Run) -> dict[Stage, tuple[str, ...]]:
    seen: dict[Stage, list[str]] = {}
    for entry in run.stages:
        bucket = seen.setdefault(entry.stage, [])
        for call in entry.calls:
            if call.prompt_hash not in bucket:
                bucket.append(call.prompt_hash)
    return {stage: tuple(hashes) for stage, hashes in seen.items()}


def measure(run: Run) -> RunMetrics:
    """Reduce a finished run to :class:`RunMetrics`.

    Safe on a partial run: a run that failed at verify still yields research counts and
    the cost it already burned, which is exactly what a report needs in order to say the
    case failed *and* what it cost to find out.
    """
    bundle: ResearchBundle | None = run.verified or run.research
    verdicts: dict[Verdict, int] = {}
    unverified = 0
    if run.verified is not None:
        verdicts = run.verified.verdict_counts()
        unverified = sum(1 for claim in run.verified.claims if not claim.is_verified)

    stages = _stage_metrics(run)
    completed = tuple(
        stage for stage, metrics in stages.items() if metrics.status is RunStatus.SUCCEEDED
    )
    content = run.illustrated or run.edited or run.content

    return RunMetrics(
        run_id=run.id,
        status=run.status,
        stages_completed=completed,
        claims=len(bundle.claims) if bundle else 0,
        evidence=len(bundle.evidence) if bundle else 0,
        places=len(bundle.places) if bundle else 0,
        verdicts=verdicts,
        unverified_claims=unverified,
        reported_gaps=len(bundle.gaps) if bundle else 0,
        citation_failures=tuple(citation_failures(run)),
        itinerary_items=(
            sum(len(day.items) for day in run.itinerary.days) if run.itinerary else 0
        ),
        content_pieces=len(content.pieces) if content else 0,
        images=len(run.illustrated.images) if run.illustrated else 0,
        prompt_tokens=sum(m.prompt_tokens for m in stages.values()),
        completion_tokens=sum(m.completion_tokens for m in stages.values()),
        cost_usd=round(sum(m.cost_usd for m in stages.values()), 8),
        latency_ms=sum(m.latency_ms for m in stages.values()),
        model_calls=sum(m.calls for m in stages.values()),
        failed_model_calls=sum(m.failed_calls for m in stages.values()),
        # One attempt per stage is the expected shape; everything beyond that is a retry.
        stage_retries=sum(max(0, m.attempts - 1) for m in stages.values()),
        queries=sum(m.queries for m in stages.values()),
        fetches=sum(m.fetches for m in stages.values()),
        stages=stages,
        prompt_hashes=_prompt_hashes(run),
    )

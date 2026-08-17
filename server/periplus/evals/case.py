"""The golden set: what a case declares, and what it asserts.

A case is a JSON file, not Python. That is deliberate — a golden set that needs a code
change to grow stops growing, and a case authored as data can be diffed, reviewed and
generated. :func:`load_cases` reads a directory of them.

What a case pins down:

* the **brief** — the pipeline's real input;
* the **corpus** — the exact pages retrieval will serve, so no network is involved and no
  source can change underneath the suite;
* the **model** — how the stages' model calls are answered offline (see
  :mod:`periplus.evals.offline`);
* the **as_of date** — frozen, so freshness downgrades are a property of the case rather
  than of the day it is run;
* the **expectations** — thresholds on :class:`~periplus.evals.metrics.RunMetrics`.

Expectations are ranges, never literal output. Asserting the exact prose a model returns
produces a suite that fails every day for no reason and is switched off within a week; a
range ("gap rate no higher than 0.4", "citation failures exactly 0") keeps failing only
when something actually broke.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from periplus.agents.verification import SemanticVerdict
from periplus.models import SourceKind, Stage, TripBrief

__all__ = [
    "CaseError",
    "CorpusPage",
    "EvalCase",
    "Expectation",
    "ModelScript",
    "VerdictLabel",
    "load_case",
    "load_cases",
]

#: Stages an eval case may drive. Hermes requires a contiguous prefix of the pipeline, so a
#: case naming ``["research", "plan"]`` is rejected at load time rather than at run time.
_STAGE_BY_NAME = {stage.value: stage for stage in Stage}


class CaseError(ValueError):
    """A case file is malformed. Always names the file, never fails silently."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CorpusPage(Strict):
    """One page retrieval will serve for this case, verbatim.

    ``published_at`` and ``fetched_at`` are what make freshness deterministic: with both
    pinned and the run's clock frozen at ``EvalCase.as_of``, whether a claim is downgraded
    to ``stale`` is a fact about the case rather than about today's date.
    """

    url: str
    text: str
    title: str | None = None
    published_at: date | None = None
    fetched_at: datetime | None = None
    source_kind: SourceKind = SourceKind.UNKNOWN
    query: str | None = None
    note: str | None = Field(
        default=None,
        description="Why this page is in the corpus — e.g. 'carries an injected instruction'.",
    )


class VerdictLabel(Strict):
    """Ground truth for one claim, applied by substring match.

    ``match`` is checked, case-insensitively, against ``"<subject> :: <claim text>"``. The
    first label that matches wins, so a case can register a broad rule and a narrow
    override without depending on file order.
    """

    match: str = Field(min_length=1)
    verdict: SemanticVerdict
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    reason: str = Field(default="Labelled by the eval golden set.", min_length=1)
    cite_supporting: bool = Field(
        default=True,
        description="Whether to cite the claim's own evidence as supporting. False leaves "
        "the citation lists empty, which is how an 'evidence present but irrelevant' "
        "label is expressed.",
    )


class ModelScript(Strict):
    """How this case answers the two kinds of model call it makes.

    Research is answered from a fixed list — one reply per extraction batch — because the
    thing under test there is the pipeline's exact-quote grounding, and a fixed reply is
    the only way to hand it a quote that is deliberately *almost* right.

    Verification is answered by a labelled oracle instead of a fixed list, because claim
    IDs are minted during research and cannot be written into a file in advance. The oracle
    is a perfect auditor by construction — which is the point: with model judgement held
    fixed, any movement in the verdict distribution is the pipeline's own doing (freshness
    downgrades, no-evidence handling, batch rejection), not the model's mood.
    """

    research: list[dict | str] = Field(default_factory=list)
    labels: list[VerdictLabel] = Field(default_factory=list)
    default_label: VerdictLabel | None = Field(
        default=None,
        description="Applied to claims no label matched. Absent means 'unsupported, "
        "no citations', and every fall-through is reported as a case warning.",
    )

    def research_replies(self) -> list[str]:
        return [
            reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False)
            for reply in self.research
        ]


class Expectation(Strict):
    """One threshold on one metric. At least one bound is required."""

    metric: str = Field(min_length=1)
    min: float | None = None
    max: float | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _needs_a_bound(self) -> Expectation:
        if self.min is None and self.max is None:
            raise ValueError(f"expectation on {self.metric!r} sets neither min nor max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError(f"expectation on {self.metric!r} has min above max")
        return self

    def describe(self) -> str:
        if self.min is not None and self.max is not None:
            if self.min == self.max:
                return f"{self.metric} == {self.min:g}"
            return f"{self.min:g} <= {self.metric} <= {self.max:g}"
        if self.max is not None:
            return f"{self.metric} <= {self.max:g}"
        return f"{self.metric} >= {self.min:g}"

    def check(self, values: dict[str, float]) -> str | None:
        """Return a failure reason, or ``None`` when satisfied.

        An unknown metric name is a failure, not a pass. A threshold that silently
        evaluates to nothing is worse than no threshold at all: the report would show a
        green case that asserted nothing.
        """
        if self.metric not in values:
            return f"unknown metric {self.metric!r}"
        actual = values[self.metric]
        if self.min is not None and actual < self.min:
            return f"{self.metric} = {actual:g}, expected >= {self.min:g}"
        if self.max is not None and actual > self.max:
            return f"{self.metric} = {actual:g}, expected <= {self.max:g}"
        return None


@dataclass(frozen=True, slots=True)
class EvalCase:
    id: str
    brief: TripBrief
    corpus: tuple[CorpusPage, ...]
    model: ModelScript
    expectations: tuple[Expectation, ...]
    stages: tuple[Stage, ...]
    as_of: datetime
    description: str | None = None
    tags: tuple[str, ...] = ()
    source_path: Path | None = field(default=None, compare=False)

    @property
    def stage_names(self) -> str:
        return " -> ".join(stage.value for stage in self.stages)


def _parse_stages(names: Sequence[str], *, where: str) -> tuple[Stage, ...]:
    stages: list[Stage] = []
    for name in names:
        stage = _STAGE_BY_NAME.get(name)
        if stage is None:
            raise CaseError(f"{where}: unknown stage {name!r}")
        stages.append(stage)
    if not stages:
        raise CaseError(f"{where}: no stages configured")
    from periplus.orchestrator.hermes import STAGE_ORDER

    if tuple(stages) != STAGE_ORDER[: len(stages)]:
        raise CaseError(
            f"{where}: stages must be a contiguous prefix of "
            f"{[s.value for s in STAGE_ORDER]}; got {names}"
        )
    return tuple(stages)


def _parse_as_of(raw: object, *, where: str) -> datetime:
    if raw is None:
        raise CaseError(f"{where}: as_of is required — an eval case may not depend on today")
    if not isinstance(raw, str):
        raise CaseError(f"{where}: as_of must be an ISO date or datetime string")
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise CaseError(f"{where}: as_of is not ISO-8601: {exc}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def load_case(path: Path) -> EvalCase:
    """Read one case file. Raises :class:`CaseError` naming ``path`` on any problem."""
    where = path.name
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CaseError(f"{where}: cannot be read as JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CaseError(f"{where}: top level must be a JSON object")

    unknown = set(payload) - {
        "id",
        "description",
        "tags",
        "stages",
        "as_of",
        "brief",
        "corpus",
        "model",
        "expect",
    }
    if unknown:
        raise CaseError(f"{where}: unknown keys {sorted(unknown)}")

    case_id = payload.get("id") or path.stem
    as_of = _parse_as_of(payload.get("as_of"), where=where)
    try:
        # A brief's generated id and created_at reach the research prompt verbatim, so
        # leaving them to their defaults would make the assembled prompt — and therefore
        # anything derived from it — different on every run of the same case. Pinned here
        # unless the case states them, so a case is byte-identical run to run.
        brief_payload = {
            "id": case_id,
            "created_at": as_of.isoformat(),
            **payload["brief"],
        }
        brief = TripBrief(**brief_payload)
        corpus = tuple(CorpusPage(**page) for page in payload.get("corpus", []))
        model = ModelScript(**payload.get("model", {}))
        expectations = tuple(Expectation(**item) for item in payload.get("expect", []))
    except KeyError as exc:
        raise CaseError(f"{where}: missing required key {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise CaseError(f"{where}: {exc}") from exc

    if not corpus:
        raise CaseError(f"{where}: a case with no corpus tests nothing")
    if not expectations:
        raise CaseError(f"{where}: a case with no expectations tests nothing")

    return EvalCase(
        id=case_id,
        description=payload.get("description"),
        tags=tuple(payload.get("tags", [])),
        brief=brief,
        corpus=corpus,
        model=model,
        expectations=expectations,
        stages=_parse_stages(payload.get("stages", ["research", "verify"]), where=where),
        as_of=as_of,
        source_path=path,
    )


def load_cases(directory: Path, *, only: Iterable[str] | None = None) -> list[EvalCase]:
    """Load every ``*.json`` case in ``directory``, sorted by id for a stable report order.

    ``only`` filters by case id or tag, so a single case can be re-run without editing
    files. An empty directory raises rather than reporting a vacuous all-green suite.
    """
    if not directory.is_dir():
        raise CaseError(f"{directory} is not a directory")
    cases = [load_case(path) for path in sorted(directory.glob("*.json"))]
    if not cases:
        raise CaseError(f"{directory} contains no case files")
    if only is not None:
        wanted = set(only)
        cases = [case for case in cases if case.id in wanted or wanted & set(case.tags)]
        if not cases:
            raise CaseError(f"no case in {directory} matches {sorted(wanted)}")
    return sorted(cases, key=lambda case: case.id)

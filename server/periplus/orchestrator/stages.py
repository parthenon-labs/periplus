"""Narrow protocols and adapters bridging Explorer, Auditor and Navigator into Hermes.

Hermes drives a :class:`StageAdapter` without knowing anything about retrieval, model
calls, or batching. An adapter's only obligation is to run one stage on the artifact the
previous stage produced and report what it used. All research, verification and planning
behaviour still lives in :class:`~periplus.agents.research.ResearchAgent`,
:class:`~periplus.agents.verification.VerificationAgent` and
:class:`~periplus.agents.navigation.NavigationAgent`; the adapters below only translate
between Hermes's generic pipeline and each agent's own narrow contract.

The adapters also own one judgement the agents deliberately do not make. Every agent
below degrades rather than raises: a model call lost to a rate limit or a 5xx becomes a
gap, a caveat or a recorded failure, never an exception, because an agent's job is to
report honestly on what it managed to produce. That leaves nobody to say "this stage was
robbed by the provider and is worth simply running again" — which is what
:class:`~periplus.orchestrator.hermes.StageRetryPolicy` retries. So each adapter reads
its outcome's ``transient_failures`` count and, when the artifact it is holding would be
rejected by that stage's default gate anyway, raises
:class:`~periplus.orchestrator.errors.TransientStageError` instead of handing the
artifact back.

Both halves of that condition matter. Without the counter, every gate failure would look
retryable, including the ones caused by a prompt or an input that will fail identically
next time. Without the gate check, a stage that lost one batch out of ten but still
produced a usable artifact would be thrown away and, once the retry budget ran out, take
the whole run down with it — strictly worse than shipping what it had. Together they mean
converting to a transient error can only ever replace a run that was going to fail with
one that might not. (A pipeline that explicitly disables a default gate via
``gates={Stage.X: None}`` is the one case where these can disagree: the adapter still
declines an artifact the default gate would have rejected.)
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from periplus.agents.content import ContentAgent
from periplus.agents.editor import EditorAgent
from periplus.agents.illustration import IllustrationAgent
from periplus.agents.navigation import NavigationAgent
from periplus.agents.research import ResearchAgent, ResearchFollowup
from periplus.agents.verification import VerificationAgent
from periplus.models import (
    Artifact,
    ContentSet,
    Itinerary,
    ModelCall,
    ResearchBundle,
    Stage,
    TripBrief,
    Verdict,
    VerifiedBundle,
)
from periplus.orchestrator.budget import ResourceUsage
from periplus.orchestrator.clock import Clock
from periplus.orchestrator.errors import TransientStageError


@dataclass(slots=True)
class StageResult:
    """What a stage attempt produced, plus what it cost."""

    artifact: Artifact
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    calls: list[ModelCall] = field(default_factory=list)


class StageAdapter(Protocol):
    """One pipeline stage, as Hermes sees it: an input artifact in, a result out.

    Implementations raise :class:`~periplus.orchestrator.hermes.TransientStageError` for
    a failure worth retrying with the same input, and
    :class:`~periplus.orchestrator.hermes.StageFailure` for one that would not change on
    retry. Anything else propagates — Hermes does not guess at failures it was not told
    about.

    ``set_progress_callback`` is deliberately not part of this protocol: Hermes looks it
    up with ``getattr(adapter, "set_progress_callback", None)`` before every attempt and
    calls it only when present, so an adapter that has no meaningful notion of partial
    progress (Navigator, Chronicler, Illustrator today) needs no method at all rather
    than a no-op stub. An adapter that does implement it should store the callback and
    invoke it with ``(processed, total)`` as its wrapped agent makes progress.

    ``set_bundle_progress_callback`` is the same story for the one adapter (research)
    whose wrapped agent builds up a growing artifact worth reporting on its own terms —
    invoked with ``(claims, evidence)`` as the bundle grows, ahead of dedup/trim.
    """

    stage: Stage

    async def run(self, stage_input: object) -> StageResult: ...


#: Inspects a produced artifact; returns a rejection reason, or ``None`` if it may pass.
StageGate = Callable[[object], "str | None"]


def research_gate(bundle: ResearchBundle) -> str | None:
    if not bundle.claims:
        return "research produced no grounded claims"
    return None


def verification_gate(bundle: VerifiedBundle) -> str | None:
    if not bundle.claims:
        return "verification received no claims"
    if not all(claim.is_verified for claim in bundle.claims):
        return "not every claim received a verdict"
    return None


def planning_gate(itinerary: Itinerary) -> str | None:
    if not any(day.items for day in itinerary.days):
        return "planning scheduled no items"
    return None


def content_gate(content: ContentSet) -> str | None:
    if not content.pieces:
        return "writing produced no content pieces"
    return None


#: Reuses content_gate outright: Editor's artifact is the same ContentSet shape Chronicler
#: produces, just revised, so "no pieces survived" is exactly as fatal here as it is
#: after writing — an Editor pass that guts every piece is a bug worth failing the run
#: over, not a silent empty result.
edit_gate = content_gate


#: The verdicts that mean a claim is settled and a second look at its subject would buy
#: nothing. Deliberately narrower than :data:`~periplus.models.USABLE_VERDICTS`, which
#: also contains ``stale``: a stale claim is one the evidence supports but no longer
#: recently enough, which is exactly the hole a fresh, targeted search can fill.
CONFIRMED_VERDICTS: frozenset[Verdict] = frozenset({Verdict.SUPPORTED, Verdict.PARTIAL})


def unconfirmed_research(
    verified: VerifiedBundle, *, max_subjects: int = 5
) -> ResearchFollowup | None:
    """What a second, targeted research pass should go looking for — or ``None``.

    A claim Auditor could not confirm is not a failure to retry. The verification stage
    did its job: it read the evidence attached to that claim and reported honestly that
    the evidence does not settle it. Running the identical stage again would produce the
    identical verdict, because the input has not changed. What has to change is the
    reading material, and that means going back to Explorer — for those subjects only.

    Returns ``None`` when every claim came back confirmed, which is the common case and
    costs a run nothing. Subjects are returned in first-appearance order and capped, so
    the same bundle always produces the same followup, and a bundle where everything is
    unconfirmed cannot turn a bounded second pass into an unbounded one.
    """
    subjects: list[str] = []
    seen: set[str] = set()
    for claim in verified.claims:
        if claim.verdict in CONFIRMED_VERDICTS:
            continue
        subject = claim.subject.strip()
        key = subject.casefold()
        if not subject or key in seen:
            continue
        seen.add(key)
        subjects.append(subject)
        if len(subjects) == max_subjects:
            break
    if not subjects:
        return None
    return ResearchFollowup(subjects=tuple(subjects), base=verified)


#: The default gate per stage. Passing ``gates={Stage.X: None}`` to :class:`Hermes`
#: disables one explicitly rather than silently.
#:
#: Stage.ILLUSTRATE has no entry, and therefore no gate at all: zero images is a valid,
#: intentional artifact whenever no image provider is configured or nothing in the
#: content set was illustratable — see IllustrationAgent's graceful-degrade contract.
#: Gating it the way content_gate gates an empty ContentSet would turn that deliberate
#: degrade into a failed run.
DEFAULT_GATES: dict[Stage, StageGate | None] = {
    Stage.RESEARCH: research_gate,
    Stage.VERIFY: verification_gate,
    Stage.PLAN: planning_gate,
    Stage.WRITE: content_gate,
    Stage.EDIT: edit_gate,
}


def _reject_if_only_the_provider_failed(
    transient_failures: int, gate_reason: str | None, stage: Stage
) -> None:
    """Raise when a stage lost calls to the provider *and* has nothing that would pass.

    ``gate_reason`` is the stage's own default gate applied to the artifact in hand, so
    the artifact is only ever discarded when it was headed for rejection regardless —
    see this module's docstring for why both conditions are required.
    """
    if transient_failures and gate_reason is not None:
        raise TransientStageError(
            f"{stage.value}: {gate_reason}, after {transient_failures} model call(s) "
            "failed at the provider"
        )


class ResearchStageAdapter:
    """Wraps :class:`ResearchAgent` behind the narrow :class:`StageAdapter` protocol."""

    stage = Stage.RESEARCH

    def __init__(self, agent: ResearchAgent) -> None:
        self._agent = agent
        self._on_progress: Callable[[int, int], None] | None = None
        self._on_bundle_progress: Callable[[int, int], None] | None = None
        self._followup: ResearchFollowup | None = None

    def set_progress_callback(self, callback: Callable[[int, int], None] | None) -> None:
        self._on_progress = callback

    def set_bundle_progress_callback(self, callback: Callable[[int, int], None] | None) -> None:
        self._on_bundle_progress = callback

    def set_followup(self, followup: ResearchFollowup | None) -> None:
        """Aim the next attempt at a specific set of unconfirmed subjects, or clear it.

        Held across attempts rather than consumed by :meth:`run`, so a followup attempt
        that fails transiently and is retried is still a followup attempt — consuming it
        on entry would quietly turn the retry into a full second sweep that discards the
        first pass's bundle. Hermes clears it once the attempt succeeds.
        """
        self._followup = followup

    async def run(self, stage_input: TripBrief) -> StageResult:
        outcome = await self._agent.research(
            stage_input,
            followup=self._followup,
            on_progress=self._on_progress,
            on_bundle_progress=self._on_bundle_progress,
        )
        _reject_if_only_the_provider_failed(
            outcome.transient_failures, research_gate(outcome.bundle), Stage.RESEARCH
        )
        # Charge exact retrieval-attempt counts, not an approximation from what made it
        # into the bundle: a failed query still cost a search call, and a fetched page
        # that was blocked, too thin to use, or simply cited by no claim still cost a
        # fetch. Counting only successful queries or accepted evidence URLs undercounts
        # both.
        usage = ResourceUsage(
            queries=outcome.queries_attempted,
            fetches=outcome.fetch_attempts,
            tokens=outcome.total_tokens,
        )
        return StageResult(artifact=outcome.bundle, usage=usage, calls=list(outcome.calls))


class VerificationStageAdapter:
    """Wraps :class:`VerificationAgent`, translating a bundle into its narrow input.

    Auditor's own contract takes only claims and evidence, never a bundle — see
    docs/architecture.md. The freshness ``as_of`` date comes from the same injected
    :class:`Clock` Hermes uses for budgets, so a replay is deterministic end to end.
    """

    stage = Stage.VERIFY

    def __init__(self, agent: VerificationAgent, *, clock: Clock) -> None:
        self._agent = agent
        self._clock = clock
        self._on_progress: Callable[[int, int], None] | None = None

    def set_progress_callback(self, callback: Callable[[int, int], None] | None) -> None:
        self._on_progress = callback

    async def run(self, stage_input: ResearchBundle) -> StageResult:
        outcome = await self._agent.verify(
            stage_input.claims,
            stage_input.evidence,
            as_of=self._clock.now().date(),
            on_progress=self._on_progress,
        )
        verified = outcome.to_bundle(stage_input)
        _reject_if_only_the_provider_failed(
            outcome.transient_failures, verification_gate(verified), Stage.VERIFY
        )
        usage = ResourceUsage(tokens=outcome.total_tokens)
        return StageResult(artifact=verified, usage=usage, calls=list(outcome.calls))


class NavigationStageAdapter:
    """Wraps :class:`NavigationAgent`. Needs the trip brief alongside the verified bundle,
    so it is constructed once per brief rather than reused across runs like the others.
    """

    stage = Stage.PLAN

    def __init__(self, agent: NavigationAgent, *, brief: TripBrief) -> None:
        self._agent = agent
        self._brief = brief

    async def run(self, stage_input: VerifiedBundle) -> StageResult:
        outcome = await self._agent.plan(self._brief, stage_input)
        _reject_if_only_the_provider_failed(
            outcome.transient_failures, planning_gate(outcome.itinerary), Stage.PLAN
        )
        usage = ResourceUsage(tokens=outcome.total_tokens)
        return StageResult(artifact=outcome.itinerary, usage=usage, calls=list(outcome.calls))


class ContentStageAdapter:
    """Wraps :class:`ContentAgent`. Needs the trip brief alongside the itinerary, for the
    same reason :class:`NavigationStageAdapter` does — Chronicler's prompt wants the
    traveller's language and interests, not just the previous stage's artifact.
    """

    stage = Stage.WRITE

    def __init__(self, agent: ContentAgent, *, brief: TripBrief) -> None:
        self._agent = agent
        self._brief = brief

    async def run(self, stage_input: Itinerary) -> StageResult:
        outcome = await self._agent.write(self._brief, stage_input)
        _reject_if_only_the_provider_failed(
            outcome.transient_failures, content_gate(outcome.content), Stage.WRITE
        )
        usage = ResourceUsage(tokens=outcome.total_tokens)
        return StageResult(artifact=outcome.content, usage=usage, calls=list(outcome.calls))


class EditorStageAdapter:
    """Wraps :class:`EditorAgent` behind the narrow :class:`StageAdapter` protocol.

    Like :class:`IllustrationStageAdapter` below, and unlike
    :class:`NavigationStageAdapter` and :class:`ContentStageAdapter`, this adapter needs
    nothing bound in at construction time: Editor revises exactly the claims and pieces
    already sitting on the :class:`~periplus.models.ContentSet` Chronicler produced, not
    the brief or the itinerary two stage boundaries back.
    """

    stage = Stage.EDIT

    def __init__(self, agent: EditorAgent) -> None:
        self._agent = agent

    async def run(self, stage_input: ContentSet) -> StageResult:
        outcome = await self._agent.edit(stage_input)
        usage = ResourceUsage(tokens=outcome.total_tokens)
        return StageResult(artifact=outcome.content, usage=usage, calls=list(outcome.calls))


class IllustrationStageAdapter:
    """Wraps :class:`IllustrationAgent` behind the narrow :class:`StageAdapter` protocol.

    Unlike :class:`NavigationStageAdapter` and :class:`ContentStageAdapter`, this adapter
    needs nothing bound in at construction time: Illustrator's prompts come from the
    claims Editor (or, if Editor is not configured, Chronicler directly) already carried
    onto its :class:`~periplus.models.ContentSet` (see ``ContentSet.claims``), not from
    the brief or the itinerary two stage boundaries back.
    """

    stage = Stage.ILLUSTRATE

    def __init__(self, agent: IllustrationAgent) -> None:
        self._agent = agent

    async def run(self, stage_input: ContentSet) -> StageResult:
        outcome = await self._agent.illustrate(stage_input)
        # Image generation is not metered in any of ResourceUsage's dimensions today —
        # it is neither an LLM call (tokens), a search query, nor a page fetch — so
        # nothing is charged against the run budget here. A future cost dimension for
        # image calls would be the place to close that gap, not a misuse of the three
        # that already exist.
        return StageResult(artifact=outcome.illustrated, calls=list(outcome.calls))

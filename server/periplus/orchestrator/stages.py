"""Narrow protocols and adapters bridging Explorer and Auditor into Hermes.

Hermes drives a :class:`StageAdapter` without knowing anything about retrieval, model
calls, or batching. An adapter's only obligation is to run one stage on the artifact the
previous stage produced and report what it used. All research and verification
behaviour still lives in :class:`~periplus.agents.research.ResearchAgent` and
:class:`~periplus.agents.verification.VerificationAgent`; the adapters below only
translate between Hermes's generic pipeline and each agent's own narrow contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from periplus.agents.research import ResearchAgent
from periplus.agents.verification import VerificationAgent
from periplus.models import Artifact, ModelCall, ResearchBundle, Stage, TripBrief, VerifiedBundle
from periplus.orchestrator.budget import ResourceUsage
from periplus.orchestrator.clock import Clock


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


#: The default gate per stage. Passing ``gates={Stage.X: None}`` to :class:`Hermes`
#: disables one explicitly rather than silently.
DEFAULT_GATES: dict[Stage, StageGate | None] = {
    Stage.RESEARCH: research_gate,
    Stage.VERIFY: verification_gate,
}


class ResearchStageAdapter:
    """Wraps :class:`ResearchAgent` behind the narrow :class:`StageAdapter` protocol."""

    stage = Stage.RESEARCH

    def __init__(self, agent: ResearchAgent) -> None:
        self._agent = agent

    async def run(self, stage_input: TripBrief) -> StageResult:
        outcome = await self._agent.research(stage_input)
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

    async def run(self, stage_input: ResearchBundle) -> StageResult:
        outcome = await self._agent.verify(
            stage_input.claims, stage_input.evidence, as_of=self._clock.now().date()
        )
        verified = outcome.to_bundle(stage_input)
        usage = ResourceUsage(tokens=outcome.total_tokens)
        return StageResult(artifact=verified, usage=usage, calls=list(outcome.calls))

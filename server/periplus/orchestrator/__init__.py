"""Hermes: the orchestrator.

Owns run state, strict stage order, budgets, bounded retries, artifact retention and
replay, and the model-call audit trail — everything docs/architecture.md assigns to
"Orchestration: Hermes". It knows nothing about how a stage does its work; see
:mod:`periplus.orchestrator.stages` for the narrow adapters that plug Explorer and
Auditor in.

Import :class:`Hermes` and, for production wiring, :func:`build_hermes`. Tests build a
``Hermes`` directly from fake :class:`~periplus.orchestrator.stages.StageAdapter`
implementations plus :class:`~periplus.orchestrator.clock.FakeClock`, with no network and
no real sleeping.
"""

from __future__ import annotations

from periplus.models import Stage
from periplus.orchestrator.artifacts import ArtifactStore, InMemoryArtifactStore, RetainedArtifact
from periplus.orchestrator.budget import BudgetTracker, ResourceUsage, RunBudget
from periplus.orchestrator.clock import Clock, FakeClock, SystemClock
from periplus.orchestrator.hermes import (
    STAGE_ORDER,
    Hermes,
    HermesError,
    InvalidTransition,
    ReplayError,
    StageFailure,
    StageOrderError,
    StageRetryPolicy,
    TransientStageError,
)
from periplus.orchestrator.stages import (
    DEFAULT_GATES,
    ContentStageAdapter,
    NavigationStageAdapter,
    ResearchStageAdapter,
    StageAdapter,
    StageGate,
    StageResult,
    VerificationStageAdapter,
    content_gate,
    planning_gate,
    research_gate,
    verification_gate,
)

__all__ = [
    "DEFAULT_GATES",
    "STAGE_ORDER",
    "ArtifactStore",
    "BudgetTracker",
    "Clock",
    "ContentStageAdapter",
    "FakeClock",
    "Hermes",
    "HermesError",
    "InMemoryArtifactStore",
    "InvalidTransition",
    "NavigationStageAdapter",
    "ReplayError",
    "ResearchStageAdapter",
    "ResourceUsage",
    "RetainedArtifact",
    "RunBudget",
    "StageAdapter",
    "StageFailure",
    "StageGate",
    "StageOrderError",
    "StageResult",
    "StageRetryPolicy",
    "SystemClock",
    "TransientStageError",
    "VerificationStageAdapter",
    "build_hermes",
    "content_gate",
    "planning_gate",
    "research_gate",
    "verification_gate",
]


def build_hermes(settings=None, *, brief=None) -> Hermes:
    """Assemble Hermes with live stage adapters from runtime settings.

    Research and verify need nothing beyond ``settings`` and are wired unconditionally.
    Navigator and Chronicler's contracts need the trip brief too — see
    :class:`~periplus.orchestrator.stages.NavigationStageAdapter` and
    :class:`~periplus.orchestrator.stages.ContentStageAdapter` — so plan and write only
    join the pipeline when a ``brief`` is supplied, i.e. call this once per brief when a
    plan is wanted, the same way :class:`Hermes` itself already runs one ``Run`` per
    brief.
    """
    from periplus.agents import (
        build_content_agent,
        build_navigation_agent,
        build_research_agent,
        build_verification_agent,
    )
    from periplus.config import get_settings

    settings = settings or get_settings()
    clock = SystemClock()
    adapters = {
        Stage.RESEARCH: ResearchStageAdapter(build_research_agent(settings)),
        Stage.VERIFY: VerificationStageAdapter(build_verification_agent(settings), clock=clock),
    }
    if brief is not None:
        adapters[Stage.PLAN] = NavigationStageAdapter(build_navigation_agent(settings), brief=brief)
        adapters[Stage.WRITE] = ContentStageAdapter(build_content_agent(settings), brief=brief)
    return Hermes(
        adapters=adapters,
        retry=StageRetryPolicy(
            max_attempts=settings.stage_max_attempts,
            backoff_seconds=settings.stage_retry_backoff_seconds,
        ),
        budget=RunBudget(
            max_queries=settings.max_run_queries,
            max_fetches=settings.max_run_fetches,
            max_tokens=settings.max_run_tokens,
            max_wall_clock_seconds=settings.max_run_wall_clock_seconds,
        ),
        clock=clock,
    )

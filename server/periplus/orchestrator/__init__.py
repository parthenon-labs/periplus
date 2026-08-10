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
    ResearchStageAdapter,
    StageAdapter,
    StageGate,
    StageResult,
    VerificationStageAdapter,
    research_gate,
    verification_gate,
)

__all__ = [
    "DEFAULT_GATES",
    "STAGE_ORDER",
    "ArtifactStore",
    "BudgetTracker",
    "Clock",
    "FakeClock",
    "Hermes",
    "HermesError",
    "InMemoryArtifactStore",
    "InvalidTransition",
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
    "research_gate",
    "verification_gate",
]


def build_hermes(settings=None) -> Hermes:
    """Assemble Hermes with live Explorer/Auditor adapters from runtime settings."""
    from periplus.agents import build_research_agent, build_verification_agent
    from periplus.config import get_settings

    settings = settings or get_settings()
    clock = SystemClock()
    return Hermes(
        adapters={
            Stage.RESEARCH: ResearchStageAdapter(build_research_agent(settings)),
            Stage.VERIFY: VerificationStageAdapter(build_verification_agent(settings), clock=clock),
        },
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

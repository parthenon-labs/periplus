"""Agent stages.

Stages depend only on the model and retrieval seams. Provider clients, persistence and
end-to-end run state belong outside this package; Hermes will wire those pieces together.
"""

from periplus.agents.navigation import (
    ItemDraft,
    ItineraryDraft,
    NavigationAgent,
    NavigationOutcome,
)
from periplus.agents.research import (
    ResearchAgent,
    ResearchExtraction,
    ResearchOutcome,
    build_research_queries,
)
from periplus.agents.verification import (
    Auditor,
    SemanticVerdict,
    VerificationAgent,
    VerificationBatch,
    VerificationDecision,
    VerificationFailure,
    VerificationOutcome,
    evidence_is_stale,
)

__all__ = [
    "Auditor",
    "ItemDraft",
    "ItineraryDraft",
    "NavigationAgent",
    "NavigationOutcome",
    "ResearchAgent",
    "ResearchExtraction",
    "ResearchOutcome",
    "SemanticVerdict",
    "VerificationAgent",
    "VerificationBatch",
    "VerificationDecision",
    "VerificationFailure",
    "VerificationOutcome",
    "build_navigation_agent",
    "build_research_agent",
    "build_research_queries",
    "build_verification_agent",
    "evidence_is_stale",
]


def build_verification_agent(settings=None) -> VerificationAgent:
    """Assemble Auditor with its bounded, deterministic verification policy."""
    from periplus.config import get_settings
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage

    settings = settings or get_settings()
    return VerificationAgent(
        llm=build_client(settings),
        policy=policy_for(Stage.VERIFY, settings),
        claims_per_batch=settings.verification_claims_per_batch,
        chars_per_batch=settings.verification_chars_per_batch,
        max_claims=settings.max_verification_claims,
        max_input_chars=settings.max_verification_input_chars,
        max_evidence_per_claim=settings.max_evidence_per_claim,
    )


def build_navigation_agent(settings=None) -> NavigationAgent:
    """Assemble Navigator with a live distance provider, if a Maps key is configured."""
    from periplus.config import get_settings
    from periplus.geo import build_distance_provider
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage

    settings = settings or get_settings()
    return NavigationAgent(
        llm=build_client(settings),
        policy=policy_for(Stage.PLAN, settings),
        distance=build_distance_provider(settings) if settings.has_maps_key else None,
        max_places=settings.max_navigation_places,
        max_claims=settings.max_navigation_claims,
    )


def build_research_agent(settings=None) -> ResearchAgent:
    """Assemble Explorer's live dependencies from runtime settings."""
    from periplus.config import get_settings
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage
    from periplus.retrieval import build_retriever

    settings = settings or get_settings()
    return ResearchAgent(
        llm=build_client(settings),
        retriever=build_retriever(settings),
        policy=policy_for(Stage.RESEARCH, settings),
        max_queries=settings.max_research_queries,
        max_documents_per_batch=settings.research_documents_per_batch,
        max_chars_per_batch=settings.research_chars_per_batch,
        max_total_documents=settings.max_research_documents,
        max_total_document_chars=settings.max_research_document_chars,
        max_evidence_per_claim=settings.max_evidence_per_claim,
    )

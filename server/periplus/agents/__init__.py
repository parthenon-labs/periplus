"""Agent stages.

Stages depend only on the model and retrieval seams. Provider clients, persistence and
end-to-end run state belong outside this package; Hermes will wire those pieces together.
"""

from periplus.agents.content import (
    Chronicler,
    ContentAgent,
    ContentDraft,
    ContentOutcome,
    PieceDraft,
)
from periplus.agents.illustration import IllustrationAgent, IllustrationOutcome
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
    "Chronicler",
    "ContentAgent",
    "ContentDraft",
    "ContentOutcome",
    "IllustrationAgent",
    "IllustrationOutcome",
    "ItemDraft",
    "ItineraryDraft",
    "NavigationAgent",
    "NavigationOutcome",
    "PieceDraft",
    "ResearchAgent",
    "ResearchExtraction",
    "ResearchOutcome",
    "SemanticVerdict",
    "VerificationAgent",
    "VerificationBatch",
    "VerificationDecision",
    "VerificationFailure",
    "VerificationOutcome",
    "build_content_agent",
    "build_illustration_agent",
    "build_navigation_agent",
    "build_research_agent",
    "build_research_queries",
    "build_verification_agent",
    "evidence_is_stale",
]


def build_illustration_agent(settings=None) -> IllustrationAgent:
    """Assemble Illustrator with a live image provider, if an Agnes or OpenAI key is
    configured — Agnes is preferred when both are set, per
    :func:`periplus.media.build_image_provider`.
    """
    from periplus.config import get_settings
    from periplus.media import build_image_provider

    settings = settings or get_settings()
    has_image_key = settings.has_agnes_image_key or settings.has_openai_image_key
    return IllustrationAgent(
        provider=build_image_provider(settings) if has_image_key else None,
        max_images=settings.max_illustrations,
        size=settings.illustration_image_size,
        quality=settings.illustration_image_quality,
    )


def build_content_agent(settings=None) -> ContentAgent:
    """Assemble Chronicler with its bounded, deterministic writing policy."""
    from periplus.config import get_settings
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage

    settings = settings or get_settings()
    return ContentAgent(
        llm=build_client(settings),
        policy=policy_for(Stage.WRITE, settings),
        max_items=settings.max_content_items,
        max_claims=settings.max_content_claims,
        max_pieces=settings.max_content_pieces,
        max_piece_chars=settings.max_content_piece_chars,
    )


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
    """Assemble Navigator with a live distance provider, if a geo key is configured."""
    from periplus.config import get_settings
    from periplus.geo import build_distance_provider
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage

    settings = settings or get_settings()
    has_distance_provider = settings.has_ors_key or settings.has_maps_key
    return NavigationAgent(
        llm=build_client(settings),
        policy=policy_for(Stage.PLAN, settings),
        distance=build_distance_provider(settings) if has_distance_provider else None,
        max_places=settings.max_navigation_places,
        max_claims=settings.max_navigation_claims,
    )


def build_research_agent(settings=None, *, evidence_cache=None) -> ResearchAgent:
    """Assemble Explorer's live dependencies from runtime settings.

    ``evidence_cache`` is optional and defaults to ``None`` (no semantic cache, exactly
    prior behaviour) — production wiring passes one shared, already-opened
    :class:`~periplus.storage.PostgresEvidenceCache` in from
    :func:`periplus.orchestrator.build_hermes`, the same "assembled once, passed down"
    shape :class:`~periplus.storage.PostgresArtifactStore` already uses.
    """
    from periplus.config import get_settings
    from periplus.llm import build_client, policy_for
    from periplus.models import Stage
    from periplus.retrieval import build_retriever

    settings = settings or get_settings()
    return ResearchAgent(
        llm=build_client(settings),
        retriever=build_retriever(settings, evidence_cache=evidence_cache),
        policy=policy_for(Stage.RESEARCH, settings),
        max_queries=settings.max_research_queries,
        max_documents_per_batch=settings.research_documents_per_batch,
        max_chars_per_batch=settings.research_chars_per_batch,
        max_total_documents=settings.max_research_documents,
        max_total_document_chars=settings.max_research_document_chars,
        max_evidence_per_claim=settings.max_evidence_per_claim,
        # Same ceiling Verification enforces (`build_verification_agent`), on purpose:
        # trimming here is what stops research ever handing verification more claims
        # than its gate can pass as a whole.
        max_claims=settings.max_verification_claims,
    )

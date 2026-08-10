"""Agent stages.

Stages depend only on the model and retrieval seams. Provider clients, persistence and
end-to-end run state belong outside this package; Hermes will wire those pieces together.
"""

from periplus.agents.research import (
    ResearchAgent,
    ResearchExtraction,
    ResearchOutcome,
    build_research_queries,
)

__all__ = [
    "ResearchAgent",
    "ResearchExtraction",
    "ResearchOutcome",
    "build_research_agent",
    "build_research_queries",
]


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
        max_evidence_per_claim=settings.max_evidence_per_claim,
    )

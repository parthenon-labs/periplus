"""Runtime configuration.

Everything the pipeline needs from the environment lands here, including the per-stage
model policy. Research and writing want a capable model that reasons; verification wants
a cheap, literal one that does not embellish. Keeping that as configuration rather than
agent code means the trade-off can be retuned without touching a prompt.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from periplus.models import Stage

# A bare ".env" resolves against the process working directory, so `uvicorn` started
# from server/ silently ignored the repository-root .env the README tells you to
# create — and since every key defaults to empty rather than being required, that
# failed as a working server with no model access instead of as a startup error.
# Anchor both candidate locations to this file instead. Later entries win, so a
# server/.env still overrides the shared one at the repository root.
_SERVER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _SERVER_DIR.parent
ENV_FILES = (_REPO_ROOT / ".env", _SERVER_DIR / ".env")

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_STRONG_MODEL = "deepseek-v4-pro"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERIPLUS_",
        env_file=ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Model access -----------------------------------------------------------------
    llm_base_url: str = DEEPSEEK_BASE_URL
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = DEFAULT_MODEL
    """Fallback model for any stage without an explicit override."""

    llm_model_research: str | None = DEFAULT_STRONG_MODEL
    llm_model_verify: str | None = None
    llm_model_plan: str | None = DEFAULT_STRONG_MODEL
    llm_model_write: str | None = DEFAULT_STRONG_MODEL
    llm_model_edit: str | None = DEFAULT_STRONG_MODEL

    llm_timeout_seconds: float = Field(default=120.0, gt=0)
    llm_max_attempts: int = Field(
        default=3, ge=1, description="Total attempts per structured call, repairs included."
    )
    llm_retry_backoff_seconds: float = Field(default=1.0, ge=0)

    # --- Search ------------------------------------------------------------------------
    tavily_api_key: SecretStr = SecretStr("")
    search_depth: str = Field(
        default="basic", description="Tavily depth: ultra-fast, fast, basic, advanced."
    )
    results_per_query: int = Field(default=6, ge=1, le=20)

    # --- Geo ----------------------------------------------------------------------------
    # OpenRouteService is preferred when both are set: no billing account, only an
    # email-verified key. Google Maps remains supported for whoever already has a key.
    google_maps_api_key: SecretStr = SecretStr("")
    ors_api_key: SecretStr = SecretStr("")

    # --- Illustration --------------------------------------------------------------------
    # Agnes is preferred when both are set: an OpenAI-compatible Images API that returns
    # a URL instead of base64 (see periplus.media.images), no different in kind from the
    # geo package's OpenRouteService-over-Google-Maps preference above.
    openai_api_key: SecretStr = SecretStr("")
    agnes_api_key: SecretStr = SecretStr("")
    illustration_image_model: str = "gpt-image-1"
    agnes_image_model: str = "agnes-image-2.1-flash"
    # Agnes's own troubleshooting docs put generation at "a few seconds to tens of
    # seconds" depending on prompt/size/load, and recommend a 60-360s client timeout —
    # a different animal from OpenAI's typically-faster response, so it gets its own
    # field rather than reusing request_timeout_seconds (30s default), the same way
    # llm_timeout_seconds above already gets its own field instead of sharing it.
    agnes_image_timeout_seconds: float = Field(default=120.0, gt=0)
    illustration_image_size: str = "1024x1024"
    illustration_image_quality: str = Field(
        default="auto", description="One of low, medium, high, auto."
    )
    max_illustrations: int = Field(
        default=6, ge=1, description="Ceiling on distinct subjects illustrated per run."
    )
    illustration_max_attempts: int = Field(
        default=2,
        ge=1,
        description="Attempts per illustrated subject, transient provider failures only; "
        "1 disables retry. Both image providers run with the SDK's own retries off, so "
        "this is the only retry in that path — see periplus.agents.illustration.",
    )
    illustration_retry_backoff_seconds: float = Field(default=1.0, ge=0)

    # --- Fetching ----------------------------------------------------------------------
    page_cache_enabled: bool = True
    page_cache_dir: str = "data/pages"
    page_cache_ttl_days: int = Field(
        default=30, ge=0, description="0 keeps cached pages indefinitely."
    )
    max_page_bytes: int = Field(default=2_000_000, gt=0)
    per_host_delay_seconds: float = Field(default=1.0, ge=0)
    max_concurrent_fetches: int = Field(default=6, ge=1)
    obey_robots: bool = True
    max_chars_per_document: int = Field(default=24_000, gt=0)

    # --- Storage ----------------------------------------------------------------------
    database_url: str = "postgresql://localhost/periplus"

    # --- Evidence cache ------------------------------------------------------------
    # A pgvector-backed semantic cache in front of retrieval: reuse a near-identical
    # source instead of re-fetching it. Off by an unmet dependency, not just a flag —
    # see periplus.embeddings.build_embedder. Never a paid embedding API; local
    # sentence-transformers only, by cost constraint.
    evidence_cache_enabled: bool = True
    evidence_embedding_model: str = "all-MiniLM-L6-v2"
    evidence_cache_similarity_threshold: float = Field(
        default=0.90,
        ge=0.0,
        le=1.0,
        description="Cosine similarity above which a cached source is reused instead of "
        "re-fetched. Higher is more conservative about what counts as 'the same source'.",
    )

    # --- Run limits -------------------------------------------------------------------
    max_research_queries: int = Field(default=12, ge=1)
    max_research_documents: int = Field(default=24, ge=1)
    max_research_document_chars: int = Field(default=120_000, ge=1)
    research_documents_per_batch: int = Field(default=6, ge=1)
    research_chars_per_batch: int = Field(default=60_000, ge=1)
    max_evidence_per_claim: int = Field(default=5, ge=1)
    verification_claims_per_batch: int = Field(default=8, ge=1)
    verification_chars_per_batch: int = Field(default=30_000, ge=1)
    max_verification_claims: int = Field(default=100, ge=1)
    """Also the ceiling Explorer trims claims to (`build_research_agent`), so research
    never hands verification more than its all-or-nothing gate can pass in one run."""
    max_verification_input_chars: int = Field(default=120_000, ge=1)
    max_navigation_places: int = Field(default=40, ge=1)
    max_navigation_claims: int = Field(default=200, ge=1)
    max_content_items: int = Field(default=60, ge=1)
    max_content_claims: int = Field(default=200, ge=1)
    max_content_pieces: int = Field(default=12, ge=1)
    max_content_piece_chars: int = Field(default=8_000, ge=1)
    max_edit_pieces: int = Field(default=12, ge=1)
    max_edit_piece_chars: int = Field(default=8_000, ge=1)
    max_edit_input_chars: int = Field(default=40_000, ge=1)
    request_timeout_seconds: float = Field(default=30.0, gt=0)
    user_agent: str = "periplus/0.1 (+https://github.com/parthenon-labs/periplus)"

    # --- Orchestration (Hermes) --------------------------------------------------------
    # Run-wide ceilings across every stage, checked between stages rather than mid-call.
    # ``None`` (the default) is unbounded.
    max_run_queries: int | None = Field(default=None, ge=1)
    max_run_fetches: int | None = Field(default=None, ge=1)
    max_run_tokens: int | None = Field(default=None, ge=1)
    max_run_wall_clock_seconds: float | None = Field(default=None, gt=0)
    stage_max_attempts: int = Field(
        default=2, ge=1, description="Bounded stage-level retries; 1 disables retry."
    )
    stage_retry_backoff_seconds: float = Field(default=1.0, ge=0)

    # One bounded backward edge: when verification leaves claims unconfirmed, research
    # runs a second, targeted pass over those subjects and verification re-runs over the
    # merged bundle. Off makes the pipeline strictly forward again. Both passes are
    # ordinary stage attempts charged against the run budget above, so max_run_tokens and
    # friends remain the real ceiling on what this can cost.
    research_followup_enabled: bool = True
    max_followup_subjects: int = Field(
        default=5,
        ge=1,
        description="Unconfirmed subjects a followup pass may re-research. The cap is "
        "what keeps a bundle where nothing was confirmed from turning one bounded "
        "second pass into a full second sweep.",
    )

    @property
    def has_api_key(self) -> bool:
        return bool(self.llm_api_key.get_secret_value())

    @property
    def has_search_key(self) -> bool:
        return bool(self.tavily_api_key.get_secret_value())

    @property
    def has_maps_key(self) -> bool:
        return bool(self.google_maps_api_key.get_secret_value())

    @property
    def has_ors_key(self) -> bool:
        return bool(self.ors_api_key.get_secret_value())

    @property
    def has_openai_image_key(self) -> bool:
        return bool(self.openai_api_key.get_secret_value())

    @property
    def has_agnes_image_key(self) -> bool:
        return bool(self.agnes_api_key.get_secret_value())

    def model_for(self, stage: Stage) -> str:
        override = {
            Stage.RESEARCH: self.llm_model_research,
            Stage.VERIFY: self.llm_model_verify,
            Stage.PLAN: self.llm_model_plan,
            Stage.WRITE: self.llm_model_write,
            Stage.EDIT: self.llm_model_edit,
        }[stage]
        return override or self.llm_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

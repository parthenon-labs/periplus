"""Runtime configuration.

Everything the pipeline needs from the environment lands here, including the per-stage
model policy. Research and writing want a capable model that reasons; verification wants
a cheap, literal one that does not embellish. Keeping that as configuration rather than
agent code means the trade-off can be retuned without touching a prompt.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from periplus.models import Stage

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
DEFAULT_STRONG_MODEL = "deepseek-v4-pro"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PERIPLUS_",
        env_file=".env",
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

    def model_for(self, stage: Stage) -> str:
        override = {
            Stage.RESEARCH: self.llm_model_research,
            Stage.VERIFY: self.llm_model_verify,
            Stage.PLAN: self.llm_model_plan,
            Stage.WRITE: self.llm_model_write,
        }[stage]
        return override or self.llm_model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

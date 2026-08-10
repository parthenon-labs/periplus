"""Model access for the pipeline.

Import ``build_client`` and ``policy_for``; do not reach for a provider SDK from agent
code. The per-stage policies below encode a deliberate asymmetry:

* **research** and **plan** think hard — they are synthesising and sequencing.
* **verify** does not think at all, and runs at temperature zero. Its job is to compare a
  sentence against a snippet. Extended reasoning there produces justification, not
  judgement, which is exactly the failure the verification stage exists to prevent.
* **write** thinks a little; prose quality is not a reasoning problem.
"""

from __future__ import annotations

from periplus.config import Settings, get_settings
from periplus.llm.base import (
    Completion,
    LLMClient,
    LLMError,
    Message,
    RawResponse,
    StagePolicy,
    StructuredOutputError,
    Thinking,
    TransientLLMError,
    extract_json_object,
)
from periplus.llm.openai_compatible import DeepSeekClient, OpenAICompatibleClient
from periplus.llm.pricing import estimate_cost_usd
from periplus.llm.scripted import ScriptedClient
from periplus.models import Stage

__all__ = [
    "Completion",
    "DeepSeekClient",
    "LLMClient",
    "LLMError",
    "Message",
    "OpenAICompatibleClient",
    "RawResponse",
    "ScriptedClient",
    "StagePolicy",
    "StructuredOutputError",
    "Thinking",
    "TransientLLMError",
    "build_client",
    "estimate_cost_usd",
    "extract_json_object",
    "policy_for",
]

#: Thinking budget and sampling per stage. See the module docstring for the reasoning.
STAGE_THINKING: dict[Stage, tuple[Thinking, float | None]] = {
    Stage.RESEARCH: (Thinking.HIGH, None),
    Stage.VERIFY: (Thinking.OFF, 0.0),
    Stage.PLAN: (Thinking.HIGH, None),
    Stage.WRITE: (Thinking.LOW, None),
}


def policy_for(stage: Stage, settings: Settings | None = None) -> StagePolicy:
    settings = settings or get_settings()
    thinking, temperature = STAGE_THINKING[stage]
    return StagePolicy(
        model=settings.model_for(stage),
        thinking=thinking,
        temperature=temperature,
    )


def build_client(settings: Settings | None = None) -> LLMClient:
    """Construct the configured live client. Raises if no API key is present."""
    settings = settings or get_settings()
    return DeepSeekClient(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key.get_secret_value(),
        timeout_seconds=settings.llm_timeout_seconds,
        max_attempts=settings.llm_max_attempts,
        retry_backoff_seconds=settings.llm_retry_backoff_seconds,
    )

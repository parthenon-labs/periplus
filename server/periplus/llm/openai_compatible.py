"""Client for any OpenAI-compatible chat completions endpoint.

Deliberately thin: request assembly, error classification, usage normalisation. Anything
a specific vendor adds on top belongs in a subclass.
"""

from __future__ import annotations

from typing import Any

from periplus.llm.base import (
    LLMClient,
    LLMError,
    Message,
    RawResponse,
    StagePolicy,
    Thinking,
    TransientLLMError,
)


class OpenAICompatibleClient(LLMClient):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float = 120.0,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        super().__init__(max_attempts=max_attempts, retry_backoff_seconds=retry_backoff_seconds)
        try:
            from openai import AsyncOpenAI
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
            raise LLMError("the 'openai' package is required for live model calls") from exc

        if not api_key:
            raise LLMError("no API key configured; set PERIPLUS_LLM_API_KEY")

        # Retries are handled by LLMClient.structured, which also records each attempt in
        # the audit trail. Letting the SDK retry underneath would hide calls we bill for.
        self._client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=0,
        )

    async def _chat(
        self,
        messages: list[Message],
        policy: StagePolicy,
        *,
        json_object: bool,
    ) -> RawResponse:
        kwargs: dict[str, Any] = {
            "model": policy.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_object:
            kwargs["response_format"] = {"type": "json_object"}
        if policy.max_output_tokens is not None:
            kwargs["max_tokens"] = policy.max_output_tokens
        kwargs.update(self._policy_kwargs(policy))

        try:
            response = await self._client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise self._classify(exc) from exc

        return self._normalise(response, fallback_model=policy.model)

    @staticmethod
    def _policy_kwargs(policy: StagePolicy) -> dict[str, Any]:
        """Vendor-neutral defaults. Subclasses map thinking onto their own parameters."""
        if policy.temperature is not None:
            return {"temperature": policy.temperature}
        return {}

    async def aclose(self) -> None:
        await self._client.close()

    @staticmethod
    def _normalise(response: Any, *, fallback_model: str) -> RawResponse:
        choice = response.choices[0].message
        usage = getattr(response, "usage", None)
        cached = 0
        if usage is not None:
            # DeepSeek reports the prefix-cache split; most others do not.
            cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
            details = getattr(usage, "prompt_tokens_details", None)
            if not cached and details is not None:
                cached = getattr(details, "cached_tokens", 0) or 0

        return RawResponse(
            text=choice.content or "",
            model=getattr(response, "model", None) or fallback_model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(usage, "completion_tokens", 0) or 0,
            cached_prompt_tokens=cached,
            reasoning=getattr(choice, "reasoning_content", None),
        )

    @staticmethod
    def _classify(exc: Exception) -> LLMError:
        """Decide whether replaying the identical prompt could plausibly succeed."""
        import openai

        transient = (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
        if isinstance(exc, transient):
            return TransientLLMError(str(exc))
        if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
            return TransientLLMError(str(exc))
        return LLMError(str(exc))


class DeepSeekClient(OpenAICompatibleClient):
    """DeepSeek exposes thinking as an explicit toggle plus an effort dial.

    Thinking mode silently ignores ``temperature``, so it is only sent when thinking is
    off — sending it otherwise reads like a knob that works when it does nothing.
    """

    _EFFORT = {Thinking.LOW: "low", Thinking.HIGH: "high", Thinking.MAX: "max"}

    @staticmethod
    def _policy_kwargs(policy: StagePolicy) -> dict[str, Any]:
        if policy.thinking is Thinking.OFF:
            kwargs: dict[str, Any] = {"extra_body": {"thinking": {"type": "disabled"}}}
            if policy.temperature is not None:
                kwargs["temperature"] = policy.temperature
            return kwargs

        return {
            "extra_body": {"thinking": {"type": "enabled"}},
            "reasoning_effort": DeepSeekClient._EFFORT[policy.thinking],
        }

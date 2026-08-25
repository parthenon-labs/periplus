"""The model seam.

Agents never talk to a provider SDK. They call :meth:`LLMClient.structured` with a
Pydantic schema and get back a validated object, or an exception — never a string they
have to parse themselves and never a half-parsed dict.

This module owns the parts that are the same for every provider: prompt assembly, JSON
extraction, schema validation, the repair loop, and recording each attempt as a
:class:`~periplus.models.ModelCall` for the audit trail. Provider specifics live in
subclasses, which implement :meth:`LLMClient._chat` and nothing else.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from periplus.llm.pricing import estimate_cost_usd
from periplus.models import ModelCall, Stage
from periplus.observability import get_logger

T = TypeVar("T", bound=BaseModel)

logger = get_logger("llm")


class Thinking(StrEnum):
    """How much chain-of-thought to spend before answering.

    ``OFF`` is not a degraded mode. Verification wants a literal reader that compares a
    sentence to a snippet; extended reasoning there mostly invents justifications for a
    conclusion it has already drifted towards.
    """

    OFF = "off"
    LOW = "low"
    HIGH = "high"
    MAX = "max"


@dataclass(frozen=True, slots=True)
class StagePolicy:
    """Everything about *how* a stage calls the model, separated from *what* it asks."""

    model: str
    thinking: Thinking = Thinking.HIGH
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class Message:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class RawResponse:
    """A provider reply, normalised. ``reasoning`` is kept for debugging, never parsed."""

    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    reasoning: str | None = None


class LLMError(RuntimeError):
    """Base for every failure originating at the model seam."""


class TransientLLMError(LLMError):
    """Rate limits, timeouts, 5xx — worth retrying with the same prompt."""


class StructuredOutputError(LLMError):
    """The model never produced output matching the requested schema."""

    def __init__(self, message: str, *, attempts: list[ModelCall], last_text: str | None = None):
        super().__init__(message)
        self.attempts = attempts
        self.last_text = last_text

    @property
    def is_transient(self) -> bool:
        """Whether every attempt died at the provider, before any reply was seen.

        :meth:`LLMClient.structured` only ever sets ``last_text`` once a response came
        back, so its absence means no attempt got that far: rate limits, timeouts or
        5xx — the same prompt may well succeed on a later attempt. Exhausting the
        attempt budget on schema-validation failures instead leaves ``last_text`` set,
        and that is the prompt's problem, not the provider's; repeating it would repeat
        the failure. Callers that must decide whether retrying the surrounding unit of
        work is worth anything ask this, rather than reading the message.
        """
        return self.last_text is None


@dataclass(slots=True)
class Completion(Generic[T]):
    """A validated result plus the calls it took to get there, repairs included."""

    value: T
    calls: list[ModelCall] = field(default_factory=list)

    @property
    def call(self) -> ModelCall:
        return self.calls[-1]

    @property
    def total_tokens(self) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls)


def prompt_hash(messages: list[Message]) -> str:
    """Stable short digest of a prompt, so repeated calls can be grouped in the trail."""
    digest = hashlib.sha256()
    for message in messages:
        digest.update(message.role.encode())
        digest.update(b"\x00")
        digest.update(message.content.encode())
        digest.update(b"\x00")
    return digest.hexdigest()[:16]


def extract_json_object(text: str) -> str:
    """Pull the first complete JSON object out of a model reply.

    JSON mode is not a guarantee. Models still wrap output in code fences, prefix it with
    a sentence, or append a closing remark — and in thinking mode a stray brace inside the
    visible answer is common enough to matter. Scanning for the first balanced object,
    while respecting string literals and escapes, survives all three.
    """
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in response")

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]

    raise ValueError("unterminated JSON object in response")


def schema_instruction(schema: type[BaseModel]) -> str:
    """The contract appended to a system prompt so the model knows the exact shape."""
    return (
        "Reply with a single JSON object and nothing else. No prose, no code fences.\n"
        "It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


class LLMClient(ABC):
    """Provider-agnostic entry point. Subclasses implement :meth:`_chat` only."""

    def __init__(self, *, max_attempts: int = 3, retry_backoff_seconds: float = 1.0) -> None:
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    @abstractmethod
    async def _chat(
        self,
        messages: list[Message],
        policy: StagePolicy,
        *,
        json_object: bool,
    ) -> RawResponse:
        """Send one request. Raise :class:`TransientLLMError` for retryable failures."""

    async def structured(
        self,
        *,
        stage: Stage,
        schema: type[T],
        system: str,
        user: str,
        policy: StagePolicy,
    ) -> Completion[T]:
        """Call the model and return an instance of ``schema``.

        Two failure modes are retried, and they are retried differently. A transient
        provider failure replays the identical prompt, because the prompt was never the
        problem. Malformed or invalid output replays with the bad output and the exact
        validation error appended, because the model needs to see what it got wrong.
        """
        messages = [
            Message("system", f"{system.strip()}\n\n{schema_instruction(schema)}"),
            Message("user", user),
        ]
        calls: list[ModelCall] = []
        last_text: str | None = None
        last_error: Exception | None = None

        for attempt in range(1, self.max_attempts + 1):
            started = time.perf_counter()
            try:
                response = await self._chat(messages, policy, json_object=True)
            except TransientLLMError as exc:
                calls.append(self._record(stage, policy.model, messages, started, error=str(exc)))
                last_error = exc
                logger.warning(
                    "model call failed at the provider",
                    extra={
                        "context": {
                            "stage": stage.value,
                            "model": policy.model,
                            "attempt": attempt,
                            "of": self.max_attempts,
                            "error": str(exc),
                        }
                    },
                )
                if attempt == self.max_attempts:
                    break
                await asyncio.sleep(self.retry_backoff_seconds * attempt)
                continue

            call = self._record(
                stage, response.model or policy.model, messages, started, response=response
            )
            calls.append(call)
            last_text = response.text

            try:
                value = schema.model_validate_json(extract_json_object(response.text))
            except (ValueError, ValidationError) as exc:
                last_error = exc
                call.error = f"schema validation failed: {exc}"
                logger.warning(
                    "model output did not validate",
                    extra={
                        "context": {
                            "stage": stage.value,
                            "model": call.model,
                            "attempt": attempt,
                            "of": self.max_attempts,
                            "error": str(exc)[:200],
                        }
                    },
                )
                if attempt == self.max_attempts:
                    break
                messages = [
                    *messages,
                    Message("assistant", response.text),
                    Message(
                        "user",
                        "That reply did not validate against the JSON Schema. "
                        f"Error:\n{exc}\n\n"
                        "Return the corrected JSON object only.",
                    ),
                ]
                continue

            return Completion(value=value, calls=calls)

        logger.error(
            "model call exhausted its attempts",
            extra={
                "context": {
                    "stage": stage.value,
                    "model": policy.model,
                    "attempts": self.max_attempts,
                    "transient": last_text is None,
                }
            },
        )
        raise StructuredOutputError(
            f"{stage.value} stage: no valid response after {self.max_attempts} attempts "
            f"({last_error})",
            attempts=calls,
            last_text=last_text,
        )

    @staticmethod
    def _record(
        stage: Stage,
        model: str,
        messages: list[Message],
        started: float,
        *,
        response: RawResponse | None = None,
        error: str | None = None,
    ) -> ModelCall:
        latency_ms = int((time.perf_counter() - started) * 1000)
        if response is None:
            return ModelCall(
                stage=stage,
                model=model,
                prompt_hash=prompt_hash(messages),
                latency_ms=latency_ms,
                error=error,
            )
        return ModelCall(
            stage=stage,
            model=model,
            prompt_hash=prompt_hash(messages),
            prompt_tokens=response.prompt_tokens,
            completion_tokens=response.completion_tokens,
            latency_ms=latency_ms,
            cost_usd=estimate_cost_usd(
                model,
                prompt_tokens=response.prompt_tokens,
                completion_tokens=response.completion_tokens,
                cached_prompt_tokens=response.cached_prompt_tokens,
            ),
            error=error,
        )

    async def aclose(self) -> None:
        """Release provider resources. Safe to call on clients that hold none."""
        return None

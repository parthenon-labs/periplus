"""Tests for the model seam.

No network, no API key. The point of these is that agents can be built and tested
against a client that never leaves the process.
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, Field

from periplus.config import Settings
from periplus.llm import policy_for
from periplus.llm.base import (
    LLMError,
    Message,
    StagePolicy,
    StructuredOutputError,
    Thinking,
    TransientLLMError,
    extract_json_object,
    prompt_hash,
)
from periplus.llm.openai_compatible import DeepSeekClient
from periplus.llm.pricing import estimate_cost_usd
from periplus.llm.scripted import ScriptedClient
from periplus.models import Stage


class Answer(BaseModel):
    subject: str
    verdict: str
    confidence: float = Field(ge=0.0, le=1.0)


VALID = json.dumps({"subject": "Tram 28", "verdict": "supported", "confidence": 0.9})


def call(client: ScriptedClient, **overrides):
    kwargs = {
        "stage": Stage.VERIFY,
        "schema": Answer,
        "system": "You check claims against evidence.",
        "user": "Claim: Tram 28 runs from Martim Moniz.",
        "policy": StagePolicy(model="deepseek-v4-flash", thinking=Thinking.OFF),
    }
    kwargs.update(overrides)
    return client.structured(**kwargs)


class TestJSONExtraction:
    def test_plain_object(self):
        assert extract_json_object('{"a": 1}') == '{"a": 1}'

    def test_ignores_prose_around_the_object(self):
        text = 'Sure! Here you go:\n{"a": 1}\nHope that helps.'
        assert extract_json_object(text) == '{"a": 1}'

    def test_strips_code_fences(self):
        assert extract_json_object('```json\n{"a": 1}\n```') == '{"a": 1}'

    def test_handles_nested_objects(self):
        text = '{"a": {"b": {"c": 1}}}'
        assert extract_json_object(text) == text

    def test_braces_inside_strings_do_not_close_the_object(self):
        text = '{"a": "} not the end {", "b": 2}'
        assert extract_json_object(text) == text

    def test_escaped_quote_inside_string(self):
        text = '{"a": "she said \\"} \\" and left", "b": 2}'
        assert json.loads(extract_json_object(text))["b"] == 2

    def test_raises_when_absent(self):
        with pytest.raises(ValueError, match="no JSON object"):
            extract_json_object("I would rather not.")

    def test_raises_when_unterminated(self):
        with pytest.raises(ValueError, match="unterminated"):
            extract_json_object('{"a": 1')


class TestStructuredCalls:
    async def test_returns_validated_model(self):
        client = ScriptedClient([VALID])
        result = await call(client)
        assert isinstance(result.value, Answer)
        assert result.value.subject == "Tram 28"
        assert client.call_count == 1

    async def test_schema_is_pushed_into_the_system_prompt(self):
        client = ScriptedClient([VALID])
        await call(client)
        system = client.last_request[0].content
        assert "JSON Schema" in system
        assert "confidence" in system

    async def test_policy_reaches_the_provider(self):
        client = ScriptedClient([VALID])
        policy = StagePolicy(model="deepseek-v4-pro", thinking=Thinking.MAX)
        await call(client, policy=policy)
        assert client.policies[0] is policy

    async def test_records_one_call_per_attempt(self):
        client = ScriptedClient([VALID])
        result = await call(client)
        assert len(result.calls) == 1
        assert result.call.stage is Stage.VERIFY
        assert result.call.prompt_hash


class TestRepairLoop:
    async def test_retries_invalid_json_with_the_error_attached(self):
        client = ScriptedClient(["not json at all", VALID])
        result = await call(client)

        assert result.value.subject == "Tram 28"
        assert client.call_count == 2
        repair = client.last_request[-1].content
        assert "did not validate" in repair

    async def test_retries_schema_violations(self):
        """Valid JSON, wrong shape — confidence is out of range."""
        bad = json.dumps({"subject": "x", "verdict": "supported", "confidence": 7})
        client = ScriptedClient([bad, VALID])
        result = await call(client)
        assert result.value.confidence == 0.9
        assert client.call_count == 2

    async def test_failed_attempts_stay_in_the_audit_trail(self):
        client = ScriptedClient(["nonsense", VALID])
        result = await call(client)
        assert len(result.calls) == 2
        assert result.calls[0].error is not None
        assert result.calls[1].error is None

    async def test_gives_up_after_max_attempts(self):
        client = ScriptedClient(["no", "still no", "nope"], max_attempts=3)
        with pytest.raises(StructuredOutputError) as excinfo:
            await call(client)

        assert client.call_count == 3
        assert len(excinfo.value.attempts) == 3
        assert excinfo.value.last_text == "nope"


class TestTransientRetries:
    async def test_replays_the_same_prompt_after_a_transient_failure(self):
        client = ScriptedClient([TransientLLMError("429"), VALID])
        result = await call(client)

        assert result.value.subject == "Tram 28"
        assert client.requests[0] == client.requests[1], "prompt must not be mutated on retry"

    async def test_transient_failures_are_recorded(self):
        client = ScriptedClient([TransientLLMError("timeout"), VALID])
        result = await call(client)
        assert result.calls[0].error == "timeout"
        assert result.calls[0].prompt_tokens == 0

    async def test_permanent_errors_are_not_retried(self):
        client = ScriptedClient([LLMError("401 unauthorised"), VALID])
        with pytest.raises(LLMError, match="401"):
            await call(client)
        assert client.call_count == 1


class TestExhaustionIsTransientOrNot:
    """``StructuredOutputError.is_transient`` is what tells a caller whether repeating
    the same unit of work could plausibly go differently. Stage adapters read it to
    decide between a retryable stage failure and a logical one.
    """

    async def test_exhausting_on_provider_failures_is_transient(self):
        client = ScriptedClient(
            [TransientLLMError("429"), TransientLLMError("503")], max_attempts=2
        )
        with pytest.raises(StructuredOutputError) as excinfo:
            await call(client)

        assert excinfo.value.last_text is None
        assert excinfo.value.is_transient is True

    async def test_exhausting_on_unusable_output_is_not(self):
        client = ScriptedClient(["no", "still no"], max_attempts=2)
        with pytest.raises(StructuredOutputError) as excinfo:
            await call(client)

        assert excinfo.value.is_transient is False

    async def test_a_reply_that_arrived_at_all_makes_exhaustion_non_transient(self):
        # The provider was flaky *and* the one reply that got through was unusable. The
        # prompt is implicated, so this is not something to hand back for a free retry.
        client = ScriptedClient(["not json", TransientLLMError("429")], max_attempts=2)
        with pytest.raises(StructuredOutputError) as excinfo:
            await call(client)

        assert excinfo.value.is_transient is False


class TestStagePolicies:
    def test_verification_does_not_think(self):
        """The whole point of a separate auditor is that it reads, not reasons."""
        policy = policy_for(Stage.VERIFY, Settings())
        assert policy.thinking is Thinking.OFF
        assert policy.temperature == 0.0

    def test_research_thinks(self):
        assert policy_for(Stage.RESEARCH, Settings()).thinking is Thinking.HIGH

    def test_stage_overrides_beat_the_default_model(self):
        settings = Settings(llm_model="fallback", llm_model_verify=None, llm_model_research="big")
        assert settings.model_for(Stage.RESEARCH) == "big"
        assert settings.model_for(Stage.VERIFY) == "fallback"


class TestDeepSeekParameters:
    def test_thinking_off_disables_and_allows_temperature(self):
        kwargs = DeepSeekClient._policy_kwargs(
            StagePolicy(model="m", thinking=Thinking.OFF, temperature=0.0)
        )
        assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
        assert kwargs["temperature"] == 0.0

    def test_thinking_on_sends_effort_and_drops_temperature(self):
        """DeepSeek ignores temperature in thinking mode; sending it implies otherwise."""
        kwargs = DeepSeekClient._policy_kwargs(
            StagePolicy(model="m", thinking=Thinking.MAX, temperature=0.7)
        )
        assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
        assert kwargs["reasoning_effort"] == "max"
        assert "temperature" not in kwargs


class TestPricing:
    def test_cached_prompt_tokens_are_charged_at_the_lower_rate(self):
        full = estimate_cost_usd("deepseek-v4-flash", prompt_tokens=1000, completion_tokens=0)
        cached = estimate_cost_usd(
            "deepseek-v4-flash",
            prompt_tokens=1000,
            completion_tokens=0,
            cached_prompt_tokens=1000,
        )
        assert cached is not None and full is not None
        assert cached < full

    def test_unknown_model_has_no_estimate(self):
        assert estimate_cost_usd("some-other-model", prompt_tokens=10, completion_tokens=10) is None

    def test_cached_tokens_cannot_exceed_the_prompt(self):
        clamped = estimate_cost_usd(
            "deepseek-v4-flash",
            prompt_tokens=100,
            completion_tokens=0,
            cached_prompt_tokens=10_000,
        )
        floor = estimate_cost_usd(
            "deepseek-v4-flash", prompt_tokens=100, completion_tokens=0, cached_prompt_tokens=100
        )
        assert clamped == floor


class TestPromptHash:
    def test_is_stable_for_identical_prompts(self):
        a = [Message("system", "s"), Message("user", "u")]
        b = [Message("system", "s"), Message("user", "u")]
        assert prompt_hash(a) == prompt_hash(b)

    def test_changes_when_content_moves_between_messages(self):
        a = [Message("system", "ab"), Message("user", "c")]
        b = [Message("system", "a"), Message("user", "bc")]
        assert prompt_hash(a) != prompt_hash(b)

"""A scripted client for tests and offline development.

Agent behaviour is worth testing without a network, an API key or a bill. Anywhere a
real client is accepted, this one can stand in: hand it the replies to give, then inspect
what it was actually asked.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from periplus.llm.base import (
    LLMClient,
    LLMError,
    Message,
    RawResponse,
    StagePolicy,
)

Reply = str | RawResponse | Exception | Callable[[list[Message]], "str | RawResponse"]


class ScriptedClient(LLMClient):
    """Returns queued replies in order. Exceptions in the queue are raised in place."""

    def __init__(
        self,
        replies: Sequence[Reply],
        *,
        model: str = "scripted",
        max_attempts: int = 3,
    ) -> None:
        super().__init__(max_attempts=max_attempts, retry_backoff_seconds=0.0)
        self._replies = list(replies)
        self._model = model
        self.requests: list[list[Message]] = []
        self.policies: list[StagePolicy] = []

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> list[Message]:
        return self.requests[-1]

    @property
    def exhausted(self) -> bool:
        return not self._replies

    async def _chat(
        self,
        messages: list[Message],
        policy: StagePolicy,
        *,
        json_object: bool,
    ) -> RawResponse:
        self.requests.append(list(messages))
        self.policies.append(policy)

        if not self._replies:
            raise LLMError("ScriptedClient ran out of replies")

        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if callable(reply):
            reply = reply(messages)
        if isinstance(reply, RawResponse):
            return reply
        return RawResponse(
            text=reply,
            model=self._model,
            prompt_tokens=sum(len(m.content) for m in messages) // 4,
            completion_tokens=len(reply) // 4,
        )

"""Token pricing, used to attach an estimated cost to every recorded model call.

Rates are USD per million tokens, taken from the DeepSeek pricing page on 2026-08-10.
DeepSeek has announced a significant increase "in the near future", so treat any figure
produced here as an estimate for budgeting and relative comparison — not as billing.
"""

from __future__ import annotations

from dataclasses import dataclass

MILLION = 1_000_000


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    cache_hit_input: float
    cache_miss_input: float
    output: float


PRICES: dict[str, Price] = {
    "deepseek-v4-flash": Price(cache_hit_input=0.0028, cache_miss_input=0.14, output=0.28),
    "deepseek-v4-pro": Price(cache_hit_input=0.003625, cache_miss_input=0.435, output=0.87),
}


def estimate_cost_usd(
    model: str,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
) -> float | None:
    """Return the estimated cost of one call, or ``None`` for an unpriced model.

    ``cached_prompt_tokens`` is the portion of the prompt served from the provider's
    prefix cache, which is roughly fifty times cheaper. The pipeline reuses long stable
    system prompts, so the split is worth tracking rather than averaging away.
    """
    price = PRICES.get(model)
    if price is None:
        return None

    cached = max(0, min(cached_prompt_tokens, prompt_tokens))
    fresh = prompt_tokens - cached
    return (
        cached * price.cache_hit_input
        + fresh * price.cache_miss_input
        + completion_tokens * price.output
    ) / MILLION

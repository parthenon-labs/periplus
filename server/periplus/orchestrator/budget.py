"""Run-wide budgets: queries, fetches, tokens and wall clock, all deterministic.

A :class:`RunBudget` is a set of ceilings, each optional. A :class:`BudgetTracker` is the
running total for one run, charged by stage adapters and checked by Hermes both right
after a stage attempt completes and before the next one starts — see
:mod:`periplus.orchestrator.hermes` for exactly where. Checking never happens inside an
adapter's own ``run()`` call; nothing here can preempt a stage already in flight, only
stop it from being counted as having passed, or stop the next attempt from starting.

A budget is a ceiling for the whole :class:`~periplus.models.Run`, replays included: a
:class:`BudgetTracker` built for a replay is seeded with everything the run already spent
(see ``Hermes.replay``), not reset to zero. Resuming a run repeatedly cannot be used to
spend past a configured ceiling one stage at a time.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from periplus.orchestrator.clock import Clock


@dataclass(frozen=True, slots=True)
class RunBudget:
    """``None`` means unbounded for that dimension."""

    max_queries: int | None = None
    max_fetches: int | None = None
    max_tokens: int | None = None
    max_wall_clock_seconds: float | None = None


@dataclass(slots=True)
class ResourceUsage:
    """What one stage attempt spent. Accumulated into a :class:`BudgetTracker`."""

    queries: int = 0
    fetches: int = 0
    tokens: int = 0

    def __add__(self, other: ResourceUsage) -> ResourceUsage:
        return ResourceUsage(
            queries=self.queries + other.queries,
            fetches=self.fetches + other.fetches,
            tokens=self.tokens + other.tokens,
        )


@dataclass(slots=True)
class BudgetTracker:
    """Accumulates :class:`ResourceUsage` against a :class:`RunBudget` for one run.

    ``usage`` and ``elapsed_seconds_before`` seed the tracker with what a run already
    spent before this tracker existed — everything but wall clock is seeded through
    ``usage`` directly; wall clock is seeded separately because it is measured live off
    ``clock``, not accumulated by ``charge()``. Both default to zero for a brand-new run.
    """

    budget: RunBudget
    clock: Clock
    usage: ResourceUsage = field(default_factory=ResourceUsage)
    elapsed_seconds_before: float = 0.0
    _started_monotonic: float = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._started_monotonic = self.clock.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        return self.elapsed_seconds_before + (self.clock.monotonic() - self._started_monotonic)

    def charge(self, usage: ResourceUsage) -> None:
        self.usage = self.usage + usage

    def exceeded(self) -> str | None:
        """The first breached ceiling, as a human-readable reason, or ``None``."""
        budget = self.budget
        if budget.max_queries is not None and self.usage.queries > budget.max_queries:
            return f"queries {self.usage.queries} > budget {budget.max_queries}"
        if budget.max_fetches is not None and self.usage.fetches > budget.max_fetches:
            return f"fetches {self.usage.fetches} > budget {budget.max_fetches}"
        if budget.max_tokens is not None and self.usage.tokens > budget.max_tokens:
            return f"tokens {self.usage.tokens} > budget {budget.max_tokens}"
        if (
            budget.max_wall_clock_seconds is not None
            and self.elapsed_seconds > budget.max_wall_clock_seconds
        ):
            return (
                f"wall clock {self.elapsed_seconds:.1f}s > budget {budget.max_wall_clock_seconds}s"
            )
        return None

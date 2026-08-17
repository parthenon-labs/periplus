"""Run a case through the real pipeline and score it.

The harness assembles the production :class:`~periplus.orchestrator.hermes.Hermes` with the
production agents, adapters and gates, substituting only the two outside-the-process seams
(see :mod:`periplus.evals.offline`). A case's result is therefore evidence about the
pipeline, not about the harness.

The clock is frozen at the case's ``as_of``. That single decision is what makes freshness
downgrades, run timestamps and the wall-clock budget reproducible: run the same case on any
day, in any timezone, and get the same verdict distribution.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from periplus.agents.research import ResearchAgent
from periplus.agents.verification import VerificationAgent
from periplus.evals.case import EvalCase, Expectation
from periplus.evals.metrics import RunMetrics, measure
from periplus.evals.offline import CorpusRetriever, VerdictOracle
from periplus.evals.prompts import system_prompt_digests
from periplus.llm import ScriptedClient, StagePolicy, Thinking
from periplus.models import Run, RunStatus, Stage
from periplus.orchestrator.clock import FakeClock
from periplus.orchestrator.hermes import Hermes
from periplus.orchestrator.stages import ResearchStageAdapter, VerificationStageAdapter

__all__ = ["CaseResult", "HarnessError", "SuiteResult", "run_case", "run_suite"]

#: Stages the offline harness can drive today. The later four need their own model script
#: (a Navigator itinerary, a Chronicler draft) before a case can assert anything about them;
#: a case naming one gets a clear error rather than an empty artifact.
SUPPORTED_STAGES: frozenset[Stage] = frozenset({Stage.RESEARCH, Stage.VERIFY})


class HarnessError(RuntimeError):
    """The harness cannot run this case as configured."""


@dataclass(frozen=True, slots=True)
class ExpectationResult:
    expectation: Expectation
    failure: str | None

    @property
    def passed(self) -> bool:
        return self.failure is None


@dataclass(slots=True)
class CaseResult:
    case_id: str
    metrics: RunMetrics | None
    expectations: list[ExpectationResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    wall_seconds: float = 0.0
    prompt_digests: dict[str, str] = field(default_factory=dict)
    """System-prompt digests per stage — what a later diff attributes a move to."""
    run: Run | None = field(default=None, repr=False)
    """Kept so a failing case can be inspected in a REPL; never serialised into a report."""

    @property
    def passed(self) -> bool:
        return self.error is None and all(item.passed for item in self.expectations)

    @property
    def failures(self) -> list[str]:
        if self.error is not None:
            return [self.error]
        return [item.failure for item in self.expectations if item.failure is not None]


@dataclass(slots=True)
class SuiteResult:
    cases: list[CaseResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(case.passed for case in self.cases)

    @property
    def failed_cases(self) -> list[CaseResult]:
        return [case for case in self.cases if not case.passed]

    def totals(self) -> dict[str, float]:
        """Suite-wide cost, so a report can answer "what did this evaluation cost"."""
        measured = [case.metrics for case in self.cases if case.metrics is not None]
        return {
            "cases": len(self.cases),
            "passed": sum(1 for case in self.cases if case.passed),
            "failed": len(self.failed_cases),
            "claims": sum(m.claims for m in measured),
            "tokens": sum(m.tokens for m in measured),
            "cost_usd": round(sum(m.cost_usd for m in measured), 8),
            "model_calls": sum(m.model_calls for m in measured),
            "citation_failures": sum(m.citation_failure_count for m in measured),
            "wall_seconds": round(sum(case.wall_seconds for case in self.cases), 3),
        }


def _build_hermes(case: EvalCase) -> tuple[Hermes, VerdictOracle, ScriptedClient]:
    unsupported = [stage for stage in case.stages if stage not in SUPPORTED_STAGES]
    if unsupported:
        raise HarnessError(
            f"case {case.id}: the offline harness drives "
            f"{sorted(s.value for s in SUPPORTED_STAGES)} only; "
            f"{[s.value for s in unsupported]} would need a model script of its own"
        )

    clock = FakeClock(start=case.as_of)
    research_llm = ScriptedClient(case.model.research_replies(), model="eval-scripted")
    oracle = VerdictOracle(case.model.labels, default=case.model.default_label)

    adapters: dict[Stage, object] = {}
    if Stage.RESEARCH in case.stages:
        adapters[Stage.RESEARCH] = ResearchStageAdapter(
            ResearchAgent(
                llm=research_llm,
                retriever=CorpusRetriever(case.corpus),
                policy=StagePolicy(model="eval-scripted", thinking=Thinking.HIGH),
            )
        )
    if Stage.VERIFY in case.stages:
        adapters[Stage.VERIFY] = VerificationStageAdapter(
            VerificationAgent(
                llm=oracle,
                policy=StagePolicy(model="eval-oracle", thinking=Thinking.OFF),
            ),
            clock=clock,
        )

    return Hermes(adapters=adapters, clock=clock), oracle, research_llm


async def run_case(case: EvalCase) -> CaseResult:
    """Execute one case and score it against its expectations.

    A crash inside the pipeline is recorded as a failed case, never re-raised: one broken
    case must not take the suite — and therefore the report — down with it.
    """
    started = time.perf_counter()
    digests = system_prompt_digests(case.stages)
    try:
        hermes, oracle, research_llm = _build_hermes(case)
    except HarnessError as exc:
        return CaseResult(
            case_id=case.id, metrics=None, error=str(exc), prompt_digests=digests
        )

    warnings: list[str] = []
    try:
        run = await hermes.start(case.brief)
    except Exception as exc:  # noqa: BLE001 - a case failure is data, not a crash
        return CaseResult(
            case_id=case.id,
            metrics=None,
            error=f"pipeline raised {type(exc).__name__}: {exc}",
            wall_seconds=round(time.perf_counter() - started, 4),
            prompt_digests=digests,
        )

    metrics = measure(run)
    values = metrics.flat()
    results = [
        ExpectationResult(expectation=item, failure=item.check(values))
        for item in case.expectations
    ]

    # Both of these mean "the case file and the pipeline have drifted apart". Neither fails
    # the case on its own — the expectations decide that — but a silent drift is how a
    # golden set rots into a suite that asserts nothing.
    if oracle.unmatched:
        warnings.append(
            f"{len(oracle.unmatched)} claim(s) matched no verdict label and used the "
            f"default: {oracle.unmatched[:3]}"
        )
    if not research_llm.exhausted:
        warnings.append(
            "scripted research replies were left unused: fewer extraction batches ran "
            "than the case expects"
        )
    if run.status is not RunStatus.SUCCEEDED:
        last = run.stages[-1] if run.stages else None
        warnings.append(
            f"run ended {run.status.value}"
            + (f" at {last.stage.value}: {last.error}" if last and last.error else "")
        )

    return CaseResult(
        case_id=case.id,
        metrics=metrics,
        expectations=results,
        warnings=warnings,
        wall_seconds=round(time.perf_counter() - started, 4),
        prompt_digests=digests,
        run=run,
    )


async def run_suite(cases: list[EvalCase]) -> SuiteResult:
    """Run every case in order. Sequential on purpose: cost figures stay comparable."""
    return SuiteResult(cases=[await run_case(case) for case in cases])

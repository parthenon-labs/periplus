"""Offline evaluation for the Periplus pipeline.

Why this package exists: every other quality claim about this system is unfalsifiable
without it. "The grounding is stricter now", "this prompt is better", "the retrieval change
helped" — none of those are statements until the same fixed inputs produce a number before
and after. The unit tests prove the pipeline does what it was told; the eval suite is what
says whether what it was told is any good.

What it measures, stated plainly so nothing is oversold:

* **With the offline oracle** (the default, free, no network) model judgement is held
  constant, so the suite measures the *pipeline*: exact-quote grounding, freshness
  downgrades, no-evidence handling, batch and budget ceilings, gate rejection, and
  end-to-end citation integrity. This is a regression harness, and it catches the class of
  bug that silently lets an unverified fact reach a traveller.
* **What it does not measure** is prompt quality. Judging whether a prompt edit made the
  model a better auditor needs the real provider answering the same corpora; the metrics
  and report layers here are provider-agnostic on purpose so that mode can be added
  without rewriting the golden set.

Entry points: :func:`periplus.evals.harness.run_suite` in code,
``python -m periplus.evals`` on the command line.
"""

from periplus.evals.case import EvalCase, Expectation, load_case, load_cases
from periplus.evals.harness import CaseResult, SuiteResult, run_case, run_suite
from periplus.evals.metrics import CitationFailure, RunMetrics, citation_failures, measure
from periplus.evals.report import compare, render_markdown, to_payload, write_reports

__all__ = [
    "CaseResult",
    "CitationFailure",
    "EvalCase",
    "Expectation",
    "RunMetrics",
    "SuiteResult",
    "citation_failures",
    "compare",
    "load_case",
    "load_cases",
    "measure",
    "render_markdown",
    "run_case",
    "run_suite",
    "to_payload",
    "write_reports",
]

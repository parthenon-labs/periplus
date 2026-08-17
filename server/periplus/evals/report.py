"""Two artifacts per suite run: one for machines, one for a human reading a diff.

The JSON report is the record — committable, comparable, and the input to
:func:`compare`. The Markdown report is what gets pasted into a pull request, because "the
gap rate went from 0.42 to 0.18 and cost rose 6%" is a sentence a reviewer can act on,
while a raw metrics dump is not.

:func:`compare` is the load-bearing function here. A single report says what the pipeline
does; two reports say what a change *did*. Every prompt edit, every retrieval tweak, every
model swap is supposed to end with that diff attached — otherwise the change is a belief,
not a result.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from periplus.evals.harness import CaseResult, SuiteResult

__all__ = ["MetricDelta", "compare", "render_markdown", "to_payload", "write_reports"]

#: Metrics where a smaller number is an improvement. Anything not listed is reported as a
#: bare delta with no verdict attached — better silent than confidently backwards.
_LOWER_IS_BETTER = frozenset(
    {
        "gap_rate",
        "citation_failures",
        "unverified_claims",
        "reported_gaps",
        "failed_model_calls",
        "stage_retries",
        "tokens",
        "prompt_tokens",
        "completion_tokens",
        "cost_usd",
        "verdict.no_evidence",
        "verdict.unsupported",
        "verdict.contradicted",
    }
)

_HIGHER_IS_BETTER = frozenset({"usable_rate", "usable_claims", "verdict.supported"})

#: What the Markdown summary table shows per case. Everything else stays in the JSON — a
#: report nobody reads is as useless as no report.
_HEADLINE_METRICS = ("claims", "gap_rate", "citation_failures", "tokens", "cost_usd")


def _case_payload(case: CaseResult) -> dict[str, Any]:
    metrics = case.metrics
    payload: dict[str, Any] = {
        "case_id": case.case_id,
        "passed": case.passed,
        "error": case.error,
        "warnings": list(case.warnings),
        "wall_seconds": case.wall_seconds,
        # The attribution key: pairing this with the numbers is what lets a later diff say
        # *why* a metric moved. Without it, "gap rate improved" is unattributable.
        "prompt_digests": dict(case.prompt_digests),
        "expectations": [
            {
                "assertion": item.expectation.describe(),
                "passed": item.passed,
                "failure": item.failure,
                "note": item.expectation.note,
            }
            for item in case.expectations
        ],
    }
    if metrics is None:
        payload["metrics"] = None
        return payload
    payload["metrics"] = metrics.flat()
    payload["status"] = metrics.status.value
    payload["stages_completed"] = [stage.value for stage in metrics.stages_completed]
    payload["citation_failure_detail"] = [str(item) for item in metrics.citation_failures]
    # ModelCall.prompt_hash is deliberately *not* serialised here. It varies run to run
    # (verification prompts carry freshly minted claim IDs), so committing it would make
    # every re-run of an unchanged suite produce a dirty diff — see
    # :mod:`periplus.evals.prompts`. It stays on RunMetrics for in-process inspection.
    return payload


def to_payload(suite: SuiteResult, *, label: str | None = None, generated_at: datetime) -> dict:
    """The JSON report. ``generated_at`` is injected so a caller owns the timestamp."""
    return {
        "schema": "periplus.evals.report/1",
        "label": label,
        "generated_at": generated_at.isoformat(),
        "passed": suite.passed,
        "totals": suite.totals(),
        "cases": [_case_payload(case) for case in suite.cases],
    }


def _format(value: float) -> str:
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.4g}"
    return f"{int(value)}"


def render_markdown(payload: dict) -> str:
    """The human-facing report: verdict first, then per-case numbers, then what broke."""
    totals = payload["totals"]
    verdict = "PASS" if payload["passed"] else "FAIL"
    lines = [
        f"# Periplus eval report — {verdict}",
        "",
        f"- generated: `{payload['generated_at']}`",
    ]
    if payload.get("label"):
        lines.append(f"- label: `{payload['label']}`")
    lines += [
        f"- cases: **{_format(totals['passed'])}/{_format(totals['cases'])} passed**",
        f"- claims: {_format(totals['claims'])} · "
        f"citation failures: **{_format(totals['citation_failures'])}**",
        f"- cost: {_format(totals['tokens'])} tokens · ${totals['cost_usd']:.6f} · "
        f"{_format(totals['model_calls'])} model calls · {totals['wall_seconds']:g}s",
        "",
        "## Cases",
        "",
        "| case | result | " + " | ".join(_HEADLINE_METRICS) + " |",
        "| --- | --- | " + " | ".join("---" for _ in _HEADLINE_METRICS) + " |",
    ]
    for case in payload["cases"]:
        metrics = case["metrics"] or {}
        cells = [_format(metrics[name]) if name in metrics else "—" for name in _HEADLINE_METRICS]
        mark = "pass" if case["passed"] else "**FAIL**"
        lines.append(f"| `{case['case_id']}` | {mark} | " + " | ".join(cells) + " |")

    problems = [case for case in payload["cases"] if not case["passed"] or case["warnings"]]
    if problems:
        lines += ["", "## Detail", ""]
        for case in problems:
            lines.append(f"### `{case['case_id']}`")
            if case["error"]:
                lines.append(f"- error: {case['error']}")
            for item in case["expectations"]:
                if not item["passed"]:
                    lines.append(f"- failed: `{item['assertion']}` — {item['failure']}")
            for detail in case.get("citation_failure_detail", []):
                lines.append(f"- citation: {detail}")
            for warning in case["warnings"]:
                lines.append(f"- warning: {warning}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_reports(
    payload: dict, *, directory: Path, stem: str = "report"
) -> tuple[Path, Path]:
    """Write ``<stem>.json`` and ``<stem>.md`` into ``directory``, creating it if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, markdown_path


@dataclass(frozen=True, slots=True)
class MetricDelta:
    case_id: str
    metric: str
    before: float | None
    after: float | None
    """``None`` on either side means the case or metric exists in only one report."""

    @property
    def delta(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return round(self.after - self.before, 8)

    @property
    def direction(self) -> str:
        """``better``, ``worse``, ``same``, or ``unknown`` for metrics with no polarity."""
        change = self.delta
        if change is None:
            return "unknown"
        if change == 0:
            return "same"
        if self.metric in _LOWER_IS_BETTER:
            return "better" if change < 0 else "worse"
        if self.metric in _HIGHER_IS_BETTER:
            return "better" if change > 0 else "worse"
        return "unknown"


def compare(before: dict, after: dict, *, metrics: tuple[str, ...] = _HEADLINE_METRICS) -> str:
    """A Markdown diff of two reports — the sentence a change is defended with.

    Cases present in only one report are listed rather than dropped: a comparison that
    quietly ignores a removed case can show an improvement that is really just a missing
    test.
    """
    before_cases = {case["case_id"]: case for case in before["cases"]}
    after_cases = {case["case_id"]: case for case in after["cases"]}
    shared = sorted(set(before_cases) & set(after_cases))

    lines = [
        "# Eval diff",
        "",
        f"- before: `{before['generated_at']}` ({before.get('label') or 'unlabelled'})",
        f"- after:  `{after['generated_at']}` ({after.get('label') or 'unlabelled'})",
        "",
        "| case | metric | before | after | delta | |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for case_id in shared:
        before_metrics = before_cases[case_id]["metrics"] or {}
        after_metrics = after_cases[case_id]["metrics"] or {}
        for metric in metrics:
            item = MetricDelta(
                case_id=case_id,
                metric=metric,
                before=before_metrics.get(metric),
                after=after_metrics.get(metric),
            )
            if item.direction == "same":
                continue
            change = item.delta
            before_cell = _format(item.before) if item.before is not None else "—"
            after_cell = _format(item.after) if item.after is not None else "—"
            sign = "+" if change and change > 0 else ""
            delta_cell = f"{sign}{_format(change)}" if change is not None else "—"
            lines.append(
                f"| `{case_id}` | {metric} | {before_cell} | {after_cell} | "
                f"{delta_cell} | {item.direction} |"
            )

    only_before = sorted(set(before_cases) - set(after_cases))
    only_after = sorted(set(after_cases) - set(before_cases))
    if only_before:
        lines += ["", f"Cases present only before: {', '.join(only_before)}"]
    if only_after:
        lines += ["", f"Cases present only after: {', '.join(only_after)}"]

    prompt_moves = [
        case_id
        for case_id in shared
        if before_cases[case_id].get("prompt_digests") != after_cases[case_id].get("prompt_digests")
    ]
    if prompt_moves:
        lines += ["", f"System prompts changed for: {', '.join(prompt_moves)}"]
    else:
        lines += ["", "System prompts unchanged — any movement above is not a prompt edit."]
    return "\n".join(lines).rstrip() + "\n"

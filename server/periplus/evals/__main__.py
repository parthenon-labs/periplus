"""Run the eval suite from the command line.

    python -m periplus.evals                          # run every case, print the summary
    python -m periplus.evals --out evals/reports       # also write report.json / report.md
    python -m periplus.evals --only injected-instruction
    python -m periplus.evals --diff evals/reports/baseline.json

Exit code is 0 only when every expectation held, so this is usable as a CI gate. It needs
no API key, no network and no database: the whole suite runs against fixed corpora and a
labelled oracle (see :mod:`periplus.evals.offline`).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from periplus.evals.case import CaseError, load_cases
from periplus.evals.harness import run_suite
from periplus.evals.report import compare, render_markdown, to_payload, write_reports

DEFAULT_CASES = Path("evals/cases")


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the Periplus offline eval suite.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES,
        help=f"Case directory (default {DEFAULT_CASES})",
    )
    parser.add_argument(
        "--only",
        nargs="+",
        metavar="ID_OR_TAG",
        help="Run only cases with these ids or tags.",
    )
    parser.add_argument("--out", type=Path, help="Directory to write report.json and report.md to.")
    parser.add_argument(
        "--stem", default="report", help="Report filename stem (default: report)."
    )
    parser.add_argument(
        "--label", help="Free-text label recorded in the report, e.g. a prompt version."
    )
    parser.add_argument(
        "--diff",
        type=Path,
        metavar="BASELINE_JSON",
        help="Print a Markdown diff of this run against an earlier report.",
    )
    parser.add_argument("--quiet", action="store_true", help="Print only the verdict line.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        cases = load_cases(args.cases, only=args.only)
    except CaseError as exc:
        print(f"case error: {exc}", file=sys.stderr)
        return 2

    suite = asyncio.run(run_suite(cases))
    payload = to_payload(suite, label=args.label, generated_at=datetime.now(UTC))

    if not args.quiet:
        print(render_markdown(payload))

    if args.out:
        json_path, markdown_path = write_reports(payload, directory=args.out, stem=args.stem)
        print(f"wrote {json_path} and {markdown_path}")

    if args.diff:
        try:
            baseline = json.loads(args.diff.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"cannot read baseline {args.diff}: {exc}", file=sys.stderr)
            return 2
        print(compare(baseline, payload))

    totals = suite.totals()
    verdict = "PASS" if suite.passed else "FAIL"
    print(
        f"{verdict}: {totals['passed']:.0f}/{totals['cases']:.0f} cases · "
        f"{totals['tokens']:.0f} tokens · ${totals['cost_usd']:.6f}"
    )
    return 0 if suite.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

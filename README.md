# Periplus

A multi-agent pipeline that researches, verifies, and produces travel itineraries and content.

> A *periplus* is an ancient Greek sailing log — ports, landmarks, distances and
> observations recorded leg by leg, in the order you meet them. That is exactly what a
> good itinerary is: a sourced, executable document. Hence the name.

> **Status: early construction.** The full pipeline exists — Explorer, Auditor,
> Navigator, Chronicler and Hermes — plus an HTTP layer over it with finished runs
> persisted to Postgres, and a React web client to submit a trip and read the result.
> An Illustrator stage (grounded images per verified claim) is in progress on a feature
> branch. Nothing here is production ready yet, and the roadmap below marks what exists.

## Why this exists

LLMs write beautiful travel plans that are quietly wrong — a museum that closed in 2019,
a ferry that only runs in summer, an opening time hallucinated into existence. The plan
reads perfectly and fails on the ground.

Periplus treats that as the core engineering problem rather than a disclaimer. Every
factual assertion in an itinerary is a **claim**, every claim carries the **evidence** it
came from, and no claim reaches the traveller without passing a separate verification
stage that can mark it *supported*, *partial*, *contradicted*, *unsupported*,
*no evidence* or *stale*.

The output is not "a plan the model believes". It is a plan you can audit, line by line.

## The pipeline

```
brief ──▶ research ──▶ verify ──▶ plan ──▶ write ──▶ itinerary + content
              │           │         │        │
           evidence     verdicts  schedule  drafts
```

| Stage        | Agent      | Responsibility                                                              |
| ------------ | ---------- | --------------------------------------------------------------------------- |
| **research** | Explorer   | Gathers candidate places and facts from the open web; emits claims + evidence |
| **verify**   | Auditor    | Independently re-checks each claim against its evidence; assigns a verdict    |
| **plan**     | Navigator  | Turns verified claims into a day-by-day, travel-time-aware schedule           |
| **write**    | Chronicler | Produces the human-facing artifacts: itinerary doc, article, social copy      |

**Hermes** is the orchestrator that runs them — the traveller's god and the messenger
between stages. It owns run state, retries, budgets and the audit trail.

The separation is deliberate: the agent that *found* a fact never gets to *bless* it.
Verification runs as a distinct pass with its own prompt, its own model call and no
access to the researcher's reasoning — only to the claim and the raw evidence.

## Design commitments

- **Provenance or it did not happen.** A claim with no evidence cannot be promoted.
- **Verdicts are first class.** `unsupported` is a legitimate output shipped to the user,
  not a failure to hide.
- **Deterministic seams.** Every stage boundary is a typed, serialisable artifact, so any
  stage can be replayed, diffed or swapped without rerunning the ones before it.
- **Provider-agnostic.** Model access goes through one OpenAI-compatible seam.

## Stack

Python 3.11 · FastAPI · Pydantic v2 · PostgreSQL + pgvector · React 19 · Vite · Tailwind

Models are reached through one OpenAI-compatible seam, configured per stage. The default
is DeepSeek — `deepseek-v4-pro` where the work is synthesis, `deepseek-v4-flash` with
thinking disabled for verification. Pointing the whole pipeline at another provider is a
base URL and a model name.

Search is a separate seam, since these models do not browse. Tavily is the default;
fetching, cleaning and provenance are handled here rather than delegated.

## Running the tests

```bash
cd server
python3.11 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

No API key needed, and no network: `ScriptedClient` stands in for the model provider and
`ScriptedSearch` for the search provider, with page fetches served by an in-process HTTP
transport. Auditor verdict tests use checked-in JSON fixtures under
`server/tests/fixtures/verification`.

## Verifying claims offline

Auditor's public input is deliberately narrow: a list of `Claim` objects and a list of
`Evidence` objects. It never receives the trip brief, places, search queries or Explorer
reasoning. Each model input groups a claim with all of its cited evidence; output is
rejected if a supporting or conflicting ID was not in that group.

```python
from datetime import date
from pathlib import Path

from periplus.agents import VerificationAgent
from periplus.llm import ScriptedClient, StagePolicy, Thinking

fixture_json = Path("tests/fixtures/verification/supported.json").read_text()
client = ScriptedClient([fixture_json])
auditor = VerificationAgent(
    llm=client,
    policy=StagePolicy(model="scripted", thinking=Thinking.OFF, temperature=0.0),
)
outcome = await auditor.verify(claims, evidence, as_of=date(2026, 8, 10))
verified_bundle = outcome.to_bundle(research_bundle)
```

`no_evidence` is assigned without a model call. `stale` is never accepted from model
output: deterministic code applies `ClaimKind`'s `FRESHNESS_DAYS` window to supporting
evidence dates, preferring `published_at` and falling back to the observed `fetched_at`.
Batch count, per-batch characters, whole-stage claim/input totals, and evidence per claim
all have configuration ceilings. Anything that cannot be checked remains unverified and
is listed in `VerificationOutcome.failures` (and in bundle gaps when reattached).

## Orchestrating a run offline

Hermes drives a run through whichever prefix of `research → verify → plan → write` has
an adapter registered. It owns run/stage state,
strict stage order, per-run budgets, bounded stage-level retries, and replay from a
retained artifact boundary; it knows nothing about how a stage does its work.

```python
from periplus.agents import build_research_agent, build_verification_agent
from periplus.models import Stage
from periplus.orchestrator import (
    Hermes,
    ResearchStageAdapter,
    RunBudget,
    StageRetryPolicy,
    SystemClock,
    VerificationStageAdapter,
)

clock = SystemClock()
hermes = Hermes(
    adapters={
        Stage.RESEARCH: ResearchStageAdapter(build_research_agent()),
        Stage.VERIFY: VerificationStageAdapter(build_verification_agent(), clock=clock),
    },
    retry=StageRetryPolicy(max_attempts=2, backoff_seconds=1.0),
    budget=RunBudget(max_tokens=200_000, max_wall_clock_seconds=600),
    clock=clock,
)
run = await hermes.start(brief)
```

A stage that produces no usable output — no claims, or a claim nobody verified — fails
its gate rather than passing silently downstream; a stage adapter that raises a
transient error is retried up to its bound, one that raises a logical failure is not. A
budget is a ceiling for the whole run: it is checked the moment a stage attempt finishes
(so a stage that pushes cumulative usage over the ceiling fails outright, even if it is
the last configured stage) and again before the next attempt starts, but never mid-call,
so exhausting it stops downstream work without cutting off a stage already running.
Every attempt's artifact is retained, whether it passed its gate or not, so
`hermes.replay(run, from_stage=Stage.VERIFY)` re-verifies from the retained research
artifact without calling Explorer again — and continues drawing down the same run-wide
budget rather than getting a fresh one, so replaying cannot be used to bypass a ceiling
one stage at a time. Tests use scripted stage adapters and `FakeClock`; nothing here
needs a network or a real sleep.

## Reading the open web

With `PERIPLUS_TAVILY_API_KEY` set in `server/.env`:

```bash
cd server
.venv/bin/python -m periplus.probe "Museo del Prado opening hours" --subject "Museo del Prado"
```

Prints each source it found with its kind, declared date and size in tokens, plus the
pages it failed to read and why. Costs one search credit; every page it fetches is cached
on disk, so repeating the same query is free and offline.

## Roadmap

- [x] Domain model — briefs, claims, evidence, verdicts, itineraries, artifacts
- [x] Architecture and stage contracts
- [x] Model seam — structured output, repair loop, per-stage policy, cost accounting
- [x] Retrieval — search seam, polite fetching, boilerplate stripping, page cache, provenance
- [x] Research agent — bounded queries/sources, batched extraction, exact-quote evidence binding
- [x] Verification agent — grouped evidence, strict cited IDs, deterministic freshness
- [x] Hermes orchestrator — stage gates, budgets, bounded retries, replay
- [x] Planner — Navigator, day-by-day scheduling grounded in real travel times (Google Maps or OpenRouteService seam)
- [x] Writer — Chronicler, content pieces grounded in verified claims
- [x] HTTP API — submit a trip, poll the run
- [x] Run persistence — finished runs survive a restart (Postgres)
- [x] Crash recovery — a run still mid-flight when the process dies is resumed on the next process's startup from the last stage that passed its gate (`PostgresArtifactStore` + `RunStore._resume_crashed_runs`); budget accounting resets on a resumed run rather than carrying over exactly, since the crashed attempt's per-stage usage figures were not themselves persisted — a known approximation, not a correctness gap
- [x] Web client — submit a trip, poll progress, read the result (React 19 + Vite + Tailwind)
- [ ] Illustrator — a stage after Chronicler that generates a grounded image per verified claim (Agnes or OpenAI Images behind one seam); implemented on `feature/illustration-stage`, not yet merged to `main`

## Repository layout

```
docs/            architecture and design notes
server/          Python backend
  periplus/      library: models, llm, retrieval, agents, orchestrator, api
    probe.py     command-line retrieval probe
web/             React client — submit a trip, poll progress, read the result
```

## Licence

MIT — see [LICENSE](LICENSE).

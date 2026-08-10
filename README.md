# Periplus

A multi-agent pipeline that researches, verifies, and produces travel itineraries and content.

> A *periplus* is an ancient Greek sailing log — ports, landmarks, distances and
> observations recorded leg by leg, in the order you meet them. That is exactly what a
> good itinerary is: a sourced, executable document. Hence the name.

> **Status: early construction.** The domain model and architecture are settled; the
> agents, API and web client are being built in the open. Nothing here is production
> ready yet, and the roadmap below marks what actually exists.

## Why this exists

LLMs write beautiful travel plans that are quietly wrong — a museum that closed in 2019,
a ferry that only runs in summer, an opening time hallucinated into existence. The plan
reads perfectly and fails on the ground.

Periplus treats that as the core engineering problem rather than a disclaimer. Every
factual assertion in an itinerary is a **claim**, every claim carries the **evidence** it
came from, and no claim reaches the traveller without passing a separate verification
stage that can mark it *supported*, *contradicted*, *unsupported* or *stale*.

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

## Roadmap

- [x] Domain model — briefs, claims, evidence, verdicts, itineraries, artifacts
- [x] Architecture and stage contracts
- [ ] Model seam and configuration
- [ ] Research agent and web sources
- [ ] Verification agent and verdict rules
- [ ] Hermes orchestrator and run persistence
- [ ] Planner and travel-time constraints
- [ ] Writer and content artifacts
- [ ] HTTP API
- [ ] Web client

## Repository layout

```
docs/            architecture and design notes
server/          Python backend
  periplus/      library: models, agents, orchestrator, sources, api
  cli.py         local entry point
web/             React client (not yet started)
```

## Licence

MIT — see [LICENSE](LICENSE).

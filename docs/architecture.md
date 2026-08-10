# Architecture

## The problem being solved

A travel plan produced by a language model is fluent and unfalsifiable at the same time.
It states opening hours, prices, transit times and seasonal closures with identical
confidence whether it read them or invented them. The reader cannot tell which is which,
and the cost of being wrong is paid on the pavement in a foreign city.

Periplus makes the distinction structural rather than stylistic. Facts do not travel
through the pipeline as prose. They travel as `Claim` objects, each bound to the
`Evidence` it came from, each carrying a `Verdict` assigned by a stage that did not
produce it.

## Stage contracts

Every stage consumes one typed artifact and produces another. The boundaries are
serialisable, which means any stage can be replayed against a stored input without
rerunning the stages before it — the property that makes the pipeline debuggable and the
prompts iterable.

```
TripBrief          ──research──▶  ResearchBundle
ResearchBundle     ──verify────▶  VerifiedBundle
VerifiedBundle     ──plan──────▶  Itinerary
Itinerary          ──write─────▶  ContentSet
```

| Artifact         | Produced by | Contains                                                    |
| ---------------- | ----------- | ----------------------------------------------------------- |
| `TripBrief`      | user        | destination, dates, party, interests, constraints, budget    |
| `ResearchBundle` | Explorer    | candidate places, claims, evidence                           |
| `VerifiedBundle` | Auditor     | the same claims, each with a verdict and confidence          |
| `Itinerary`      | Navigator   | day-by-day scheduled items, each referencing verified claims |
| `ContentSet`     | Chronicler  | itinerary document, long-form article, social copy           |

## Why verification is a separate agent

The obvious implementation is one agent that researches and self-checks. It does not
work: a model asked to grade its own output grades the reasoning it already committed to,
not the evidence.

So the Auditor receives only the claim text and the raw evidence snippets. It never sees
the Explorer's chain of thought, its queries, or its confidence. It answers one question
per claim — *does this evidence support this statement?* — and returns a verdict:

| Verdict       | Meaning                                                             |
| ------------- | ------------------------------------------------------------------- |
| `supported`   | evidence directly states the claim                                   |
| `partial`     | evidence supports part of the claim, or an adjacent weaker form      |
| `contradicted`| evidence states something incompatible with the claim                |
| `unsupported` | evidence is present but does not address the claim                   |
| `no_evidence` | no evidence was attached                                            |
| `stale`       | evidence supports the claim but is older than the claim's freshness  |

Claims that are volatile by nature — opening hours, prices, seasonal schedules — carry a
freshness window. Evidence older than that window downgrades to `stale` regardless of how
well it matches. A price verified against a 2019 page is not verified.

Nothing is silently dropped. A `contradicted` opening time is shown to the traveller as a
contradiction, because knowing a fact is disputed is more useful than not seeing it.

## Orchestration: Hermes

Hermes owns everything that is not a stage:

- **Run state** — a `Run` record per execution, with the artifact emitted at each stage
  boundary persisted for replay.
- **Budgets** — caps on queries, fetches, tokens and wall clock, enforced per run.
- **Retries** — stage-level, with the failing artifact preserved rather than overwritten.
- **Audit trail** — every model call recorded with its stage, prompt hash and cost.

The name is deliberate and internal: Hermes is the god of travellers and the messenger
between parties, which is precisely this component's job. It is not the repository name —
Meta's JavaScript engine already owns that word in this ecosystem.

## Storage

PostgreSQL for runs, briefs, claims, evidence and itineraries. pgvector for evidence
embeddings, which serve two purposes: deduplicating near-identical sources across queries,
and retrieving prior evidence for a claim before spending a fetch on new sources.

## Model access

One seam, OpenAI-compatible, configured per stage. Research and writing want a broad,
fluent model; verification wants a cheap, literal one that does not embellish. Making the
model a per-stage setting rather than a global keeps that trade-off adjustable without
touching agent code.

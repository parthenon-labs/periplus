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

## Retrieval

The models used here do not browse, so discovery is an explicit dependency. Search sits
behind an interface with Tavily as the default implementation; fetching, cleaning and
provenance are ours.

```
query ──search──▶ SearchResult ──fetch──▶ FetchedPage ──extract──▶ SourceDocument
                                                                        │
                                                          claim quotes a passage
                                                                        ▼
                                                                    Evidence
```

A `SourceDocument` is a cleaned page — the reading material an agent is given. `Evidence`
is narrower: the exact passage one claim rests on. Minting evidence goes through
`SourceDocument.evidence_for`, which **rejects a snippet that does not occur in the page**.
Models asked to quote their source occasionally produce a quote that is almost there: a
tidied number, two sentences merged, a paraphrase in quotation marks. Verification would
then be checking a claim against text the model wrote, which checks nothing.

Four policies carry their weight:

- **Boilerplate is stripped before a model sees anything.** Navigation, cookie banners and
  related-article rails are the majority of a travel page and the majority of the research
  stage's input cost. Removal is structural — drop by tag and by container name, prefer the
  page's declared main region — rather than a readability score, because a wrong guess
  there silently deletes the opening hours.
- **A URL is fetched once, ever.** Pages are cached on disk by normalised URL, so the
  fortieth run of a prompt against the same forty sources touches no network at all. This
  is worth more than any token optimisation during prompt iteration.
- **Fetch failures are values, not exceptions.** Sites block crawlers; a run that loses
  four of forty pages carries on and reports the gaps, because a gap is information the
  research stage needs. Where the search provider returned a verbatim chunk, that chunk
  stands in as a short document rather than the source being lost.
- **Politeness is enforced, not encouraged.** One request in flight per host, a delay
  between consecutive hits, robots.txt honoured, a byte ceiling on responses. A crawler
  that hammers a museum's site loses access to the museum's own opening hours — the best
  source there is.

Source kind is inferred from the domain, with one deliberate asymmetry: a domain may be
recognised as the operator's own only if its stem *is* the venue's name or shorter.
`museodelprado.es` is the museum; `museodelprado-tickets.org` is an agency, and a
reseller's markup must not inherit the museum's authority.

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

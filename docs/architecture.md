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
ContentSet         ──edit──────▶  ContentSet (revised)
```

| Artifact              | Produced by | Contains                                                          |
| ---------------------- | ----------- | ------------------------------------------------------------------ |
| `TripBrief`            | user        | destination, dates, party, interests, constraints, budget          |
| `ResearchBundle`       | Explorer    | candidate places, claims, evidence                                 |
| `VerifiedBundle`       | Auditor     | the same claims, each with a verdict and confidence                |
| `Itinerary`            | Navigator   | day-by-day scheduled items, each referencing verified claims       |
| `ContentSet`           | Chronicler  | itinerary document, long-form article, social copy                 |
| `ContentSet` (revised) | Editor      | the same pieces, tightened for scannability; nothing added, only cut, reordered, or reworded — grounded to the same claim ids |

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
freshness window in `FRESHNESS_DAYS`. Freshness is not model judgement: the model may emit
only the four semantic verdicts (`supported`, `partial`, `contradicted`, `unsupported`).
After strict output validation, deterministic code compares the claim kind's window with
the dates of supporting evidence and downgrades old support to `stale`. A declared
`published_at` date wins; for an undated page, `fetched_at` is the last date Periplus
actually observed the passage and is the conservative fallback. Passing an explicit
`as_of` date makes replay deterministic.

Auditor receives only `Claim` and `Evidence` objects. It resolves each claim's evidence
IDs locally, groups all resolved passages beside that claim, and may batch several groups
into one call. It never receives the brief, places, search queries, Explorer rationale or
confidence. A batch response is accepted only when it has exactly one decision per input
claim, no duplicate or unexpected claim IDs, and every supporting/conflicting evidence ID
belongs to that claim's supplied group. IDs cannot appear in both lists, and verdict/list
combinations must agree. A fabricated ID invalidates the whole batch rather than being
silently removed.

No cited evidence means `no_evidence` with no model call. Claim count, evidence per claim,
claims per batch, characters per batch and whole-stage input characters all have hard
ceilings. Oversized inputs, dangling/duplicate IDs, invalid model output and model errors
remain explicit `VerificationFailure` records; their claims remain unverified. When the
outcome is reattached to a `ResearchBundle`, those failures are also preserved as gaps.
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

## Research: Explorer

Explorer spends model judgement on extraction, not on work that can be deterministic.
It builds a stable, bounded query plan from destination, dates and explicit constraints,
then retrieves source documents under whole-run document/character ceilings and batches
that bounded set under a per-call character ceiling. Stable queries improve cache reuse
and avoid paying a separate model call merely to phrase a search.

The model returns candidate places and atomic claim drafts. Every draft names a local
source index and supplies an exact quotation. That quotation is treated as untrusted
until the application finds it in the cited `SourceDocument` and mints an `Evidence`
object. Bad source indexes, paraphrases wearing quotation marks and ungrounded places are
rejected visibly into `ResearchBundle.gaps`; partial search or fetch failures remain gaps
while surviving sources continue through the stage. Query truncation, input-budget
truncation and duplicate-source removal are gaps too, so a bounded run never looks
complete by accident.

Exact duplicate claims merge across batches and may retain several independent evidence
records up to a configured cap. This is syntactic deduplication only. Semantic conflict
and corroboration belong to Auditor, not Explorer.

## Orchestration: Hermes

Hermes owns everything that is not a stage. It knows nothing about retrieval, prompts or
model providers — only whether the next stage may run.

- **Run state** — a `Run` record per execution, carrying every `StageRun` attempt for
  every stage, and the latest artifact at each boundary (`research`, `verified`,
  `itinerary`, `content`) for convenience.
- **Strict order** — Hermes runs a *contiguous prefix* of `research → verify → plan →
  write`, starting at research. A run may stop after plan and skip Chronicler, but it may
  never run write without having run everything before it. Configuring a gap (verify
  without research) is rejected at construction, not at run time.
- **Stage gates** — a stage adapter returning without raising is not enough to advance.
  Each stage has a gate — a predicate over the produced artifact — that must pass first;
  the default research gate requires at least one grounded claim, the default
  verification gate requires every claim to carry a verdict, the default planning gate
  requires at least one scheduled item, the default writing gate requires at least one
  content piece. A gate failure is a logical failure: it fails the
  stage immediately and is never auto-retried, because the same input would produce the
  same artifact again.
- **Budgets** — a `RunBudget` caps queries, fetches, tokens and wall clock for the whole
  `Run`, replays included. A `BudgetTracker` accumulates what each stage attempt reports
  spending and is checked twice: immediately after a stage attempt completes (so a stage
  that pushes cumulative usage over the ceiling fails its own attempt outright, and is
  never marked a valid replay boundary, even if it is the last configured stage) and again
  before the next attempt starts (so an already-exhausted budget stops downstream work,
  or a retry waiting out backoff, without cutting off a stage already in flight). Neither
  check ever happens mid-call. `Hermes.replay` seeds a fresh `BudgetTracker` with
  everything `run.stages` already spent rather than resetting it to zero, so resuming a
  run repeatedly cannot be used to spend past its ceiling one stage at a time. Wall-clock
  accounting goes through an injected `Clock` and only counts time actually spent running
  a stage, not idle time between calls, so replaying a run long after it stopped is not
  itself charged against it; tests never sleep for real.
- **Retries** — bounded per stage by a `StageRetryPolicy`, and transient failures are not
  logical ones. A stage adapter raises `TransientStageError` for something worth retrying
  with the same input (consumes the retry bound, with backoff) and `StageFailure` for
  something that would not change on retry (fails immediately, bound untouched). Both are
  recorded as a failed `StageRun`, tagged in `error` so the two are never confused after
  the fact.
- **Artifact retention and replay** — every stage attempt's artifact is retained, whether
  it passed its gate or not, keyed by run, stage and attempt; a later attempt never
  overwrites an earlier one. `Hermes.replay(run, from_stage=...)` resumes from the most
  recent *gate-passed* attempt at the stage before `from_stage`, without re-invoking any
  adapter for the stages before it.
- **Cancellation** — an injected `is_cancelled` predicate is checked at the top of every
  stage attempt; once true, no further stage starts and the run ends `cancelled` rather
  than `failed`.
- **Audit trail** — every model call each stage adapter reports is attached to that
  attempt's `StageRun.calls`, including calls from attempts that later failed a gate, so
  `Run.total_tokens` reflects everything actually spent.

`ResearchStageAdapter` and `VerificationStageAdapter` (`periplus.orchestrator.stages`)
are the narrow bridge from Hermes's generic `StageAdapter` protocol to Explorer and
Auditor's own contracts — the verify adapter is what turns a `ResearchBundle` into the
bare claims-and-evidence call Auditor accepts, and reattaches the result. Run
persistence is backed by `PostgresArtifactStore`, which implements the same
`ArtifactStore` protocol `InMemoryArtifactStore` uses for tests, so Hermes never changes
to know which one it is talking to. A run still mid-flight when the process dies is
resumed on the next process's startup by `RunStore._resume_crashed_runs`, from the last
stage that passed its gate.

The name is deliberate and internal: Hermes is the god of travellers and the messenger
between parties, which is precisely this component's job. It is not the repository name —
Meta's JavaScript engine already owns that word in this ecosystem.

## Storage

PostgreSQL for runs, briefs, claims, evidence and itineraries — implemented and load-
bearing today (`PostgresArtifactStore`). pgvector backs a semantic evidence cache in
front of retrieval (`PostgresEvidenceCache`, `periplus/storage/postgres_evidence.py`):
before a search hit is handed to the fetcher, its title and snippet are embedded and
checked against previously stored sources; a high-similarity match is reused instead of
spent on a fetch, and a freshly fetched page is remembered the same way for the next
query or the next run to find. The two purposes this was originally scoped for —
deduplicating near-identical sources across queries, and letting a claim reuse evidence a
prior run already paid to fetch — collapse to that one lookup here, because Explorer
extracts claims *from* fetched documents rather than searching for evidence a claim
already names; see the module docstring for the full reasoning.

Embeddings are local and free by a hard cost constraint: `sentence-transformers` running
`all-MiniLM-L6-v2` on CPU (`periplus/embeddings/`), never a paid embedding API. This
makes the whole layer genuinely optional, not just described as such — with
`sentence-transformers` not installed, the model failing to load, or
`PERIPLUS_EVIDENCE_CACHE_ENABLED=false`, `build_embedder` returns `None`, the cache is
never constructed, and retrieval runs exactly as it did before this existed: every method
on `PostgresEvidenceCache` also degrades to "nothing cached" rather than raising on a
database error, so a Postgres hiccup costs a redundant fetch, never a failed run.
`InMemoryEvidenceCache` (`periplus/retrieval/evidence_cache.py`) is a second, non-Postgres
implementation of the same seam — a real option for local development without a
database, and the offline test double paired with `ScriptedEmbeddings`.

## Model access

One seam, OpenAI-compatible, configured per stage. Research and writing want a broad,
fluent model; verification wants a cheap, literal one that does not embellish. Making the
model a per-stage setting rather than a global keeps that trade-off adjustable without
touching agent code. Tests never instantiate a live provider: checked-in responses run
through `ScriptedClient`, so verification is reproducible, offline and free of search or
fetch dependencies.

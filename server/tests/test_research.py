"""Offline tests for Explorer, the research agent."""

from __future__ import annotations

import json
from datetime import date

from periplus.agents.research import ResearchAgent, build_research_queries
from periplus.llm import ScriptedClient, StagePolicy, Thinking
from periplus.models import ClaimKind, Party, Stage, TripBrief
from periplus.retrieval import RetrievalResult, SourceDocument


class FixedRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self.result = result
        self.calls: list[tuple[list[str], str | None]] = []

    async def gather(self, queries, *, subject=None, seen_urls=None):
        self.calls.append((list(queries), subject))
        return self.result


def brief(**overrides) -> TripBrief:
    values = {
        "destination": "Sydney",
        "start_date": date(2026, 10, 1),
        "end_date": date(2026, 10, 4),
        "interests": ["architecture"],
        "must_see": ["Sydney Opera House"],
    }
    values.update(overrides)
    return TripBrief(**values)


def document(
    *,
    url: str = "https://sydneyoperahouse.com/visit",
    text: str = "The Sydney Opera House is open daily from 9am. Guided tours cost AUD 45.",
) -> SourceDocument:
    return SourceDocument(url=url, title="Visitor information", text=text, query="opera hours")


def extraction(*, quote: str, text: str, source_index: int = 0, name: str = "Sydney Opera House"):
    return json.dumps(
        {
            "places": [
                {
                    "name": name,
                    "kind": "sight",
                    "summary": "A landmark relevant to the brief.",
                    "tags": ["architecture"],
                    "claims": [
                        {
                            "subject": name,
                            "text": text,
                            "kind": "hours",
                            "source_index": source_index,
                            "quote": quote,
                        }
                    ],
                }
            ],
            "gaps": [],
        }
    )


def agent(replies, result: RetrievalResult, **overrides) -> tuple[ResearchAgent, ScriptedClient]:
    llm = ScriptedClient(replies, max_attempts=overrides.pop("llm_attempts", 1))
    explorer = ResearchAgent(
        llm=llm,
        retriever=FixedRetriever(result),
        policy=StagePolicy(model="scripted", thinking=Thinking.HIGH),
        **overrides,
    )
    return explorer, llm


class TestQueryPlan:
    def test_is_bounded_and_deterministic(self):
        trip = brief(interests=["architecture", "food", "beaches"])
        assert build_research_queries(trip, limit=4) == build_research_queries(trip, limit=4)
        assert len(build_research_queries(trip, limit=4)) == 4

    def test_covers_explicit_constraints(self):
        trip = brief(
            dietary=["halal"],
            party=Party(adults=2, children=1, child_ages=[8], mobility_notes="step-free"),
        )
        queries = build_research_queries(trip, limit=20)
        joined = "\n".join(queries).lower()
        assert "sydney opera house" in joined
        assert "halal" in joined
        assert "family" in joined
        assert "accessible" in joined

    def test_zero_limit_produces_no_queries(self):
        assert build_research_queries(brief(), limit=0) == []

    def test_explicit_must_see_queries_survive_a_tight_limit(self):
        queries = build_research_queries(brief(), limit=3)
        assert all("Sydney Opera House" in query for query in queries[1:])


class TestGrounding:
    async def test_builds_claim_evidence_and_place_from_an_exact_quote(self):
        quote = "The Sydney Opera House is open daily from 9am."
        result = RetrievalResult(documents=[document()], queries_run=["q"])
        explorer, llm = agent(
            [extraction(quote=quote, text="The Sydney Opera House opens daily at 9am.")], result
        )

        outcome = await explorer.research(brief())

        assert len(outcome.bundle.places) == 1
        assert len(outcome.bundle.claims) == 1
        assert len(outcome.bundle.evidence) == 1
        claim = outcome.bundle.claims[0]
        assert claim.kind is ClaimKind.HOURS
        assert claim.evidence_ids == [outcome.bundle.evidence[0].id]
        assert outcome.bundle.places[0].claim_ids == [claim.id]
        assert outcome.rejected_quotes == 0
        assert outcome.calls[0].stage is Stage.RESEARCH
        assert llm.call_count == 1

    async def test_rejects_a_plausible_paraphrase_wearing_quote_marks(self):
        result = RetrievalResult(documents=[document()], queries_run=["q"])
        explorer, _ = agent(
            [extraction(quote="The Opera House opens every day at 9am.", text="It opens at 9am.")],
            result,
        )

        outcome = await explorer.research(brief())

        assert outcome.bundle.claims == []
        assert outcome.bundle.evidence == []
        assert outcome.bundle.places == []
        assert outcome.rejected_quotes == 1
        assert any("non-verbatim" in gap for gap in outcome.bundle.gaps)
        assert any("No claims survived" in gap for gap in outcome.bundle.gaps)

    async def test_rejects_an_out_of_range_source_index(self):
        result = RetrievalResult(documents=[document()], queries_run=["q"])
        explorer, _ = agent([extraction(quote="anything", text="anything", source_index=7)], result)
        outcome = await explorer.research(brief())
        assert outcome.rejected_quotes == 1
        assert any("does not exist" in gap for gap in outcome.bundle.gaps)


class TestFailureAndBudgets:
    async def test_no_documents_returns_a_gap_without_calling_the_model(self):
        result = RetrievalResult(queries_run=["q"], failures=["one site blocked us"])
        explorer, llm = agent([], result)
        outcome = await explorer.research(brief())
        assert llm.call_count == 0
        assert outcome.bundle.queries_run == ["q"]
        assert "one site blocked us" in outcome.bundle.gaps
        assert any("No readable sources" in gap for gap in outcome.bundle.gaps)

    async def test_retrieval_failures_are_preserved_when_other_sources_work(self):
        quote = "The Sydney Opera House is open daily from 9am."
        result = RetrievalResult(
            documents=[document()], queries_run=["good query"], failures=["blocked.example: 403"]
        )
        explorer, _ = agent([extraction(quote=quote, text="It opens daily at 9am.")], result)
        outcome = await explorer.research(brief())
        assert len(outcome.bundle.claims) == 1
        assert "blocked.example: 403" in outcome.bundle.gaps

    async def test_invalid_model_output_becomes_a_visible_gap(self):
        result = RetrievalResult(documents=[document()], queries_run=["q"])
        explorer, _ = agent(["not json"], result)
        outcome = await explorer.research(brief())
        assert outcome.bundle.claims == []
        assert len(outcome.calls) == 1
        assert any("Research extraction failed" in gap for gap in outcome.bundle.gaps)

    async def test_documents_are_batched_by_count(self):
        docs = [
            document(url="https://a.example/one", text="Fact one is exact."),
            document(url="https://b.example/two", text="Fact two is exact."),
        ]
        result = RetrievalResult(documents=docs, queries_run=["q"])
        replies = [
            extraction(quote="Fact one is exact.", text="Fact one is exact.", name="One"),
            extraction(quote="Fact two is exact.", text="Fact two is exact.", name="Two"),
        ]
        explorer, llm = agent(replies, result, max_documents_per_batch=1)
        outcome = await explorer.research(brief())
        assert llm.call_count == 2
        assert len(outcome.bundle.places) == 2

    async def test_query_budget_is_passed_to_retrieval_and_reported(self):
        retriever = FixedRetriever(RetrievalResult())
        explorer = ResearchAgent(
            llm=ScriptedClient([]),
            retriever=retriever,
            policy=StagePolicy(model="scripted"),
            max_queries=3,
        )
        outcome = await explorer.research(brief())
        assert len(retriever.calls[0][0]) == 3
        assert any("Query limit reached" in gap for gap in outcome.bundle.gaps)

    async def test_whole_run_document_budget_skips_excess_sources(self):
        exact = "Fact one is exact."
        docs = [
            document(url="https://a.example/one", text=exact),
            document(url="https://b.example/two", text="Fact two is exact."),
        ]
        result = RetrievalResult(documents=docs, queries_run=["q"])
        explorer, llm = agent(
            [extraction(quote=exact, text=exact, name="One")],
            result,
            max_total_documents=1,
        )
        outcome = await explorer.research(brief())
        assert llm.call_count == 1
        assert len(outcome.bundle.places) == 1
        assert any("input budget reached" in gap for gap in outcome.bundle.gaps)

    async def test_whole_run_character_budget_truncates_before_model_call(self):
        prefix = "Exact."
        result = RetrievalResult(
            documents=[document(text=prefix + " Material outside the budget.")],
            queries_run=["q"],
        )
        explorer, llm = agent(
            [extraction(quote=prefix, text=prefix)],
            result,
            max_total_document_chars=len(prefix),
        )
        outcome = await explorer.research(brief())
        assert llm.call_count == 1
        assert prefix in llm.last_request[1].content
        assert "outside the budget" not in llm.last_request[1].content
        assert any("input budget reached" in gap for gap in outcome.bundle.gaps)

    async def test_duplicate_source_is_not_sent_to_the_model_twice(self):
        exact = "The venue opens daily at 9am."
        docs = [
            document(url="https://www.example.com/visit?utm_source=one", text=exact),
            document(url="https://example.com/visit", text=exact),
        ]
        result = RetrievalResult(documents=docs, queries_run=["q"])
        explorer, llm = agent(
            [extraction(quote=exact, text=exact, name="Venue")],
            result,
            max_documents_per_batch=1,
        )
        outcome = await explorer.research(brief())
        assert llm.call_count == 1
        assert any("duplicate source" in gap for gap in outcome.bundle.gaps)


class TestDeduplication:
    async def test_same_claim_from_two_sources_is_merged_with_both_evidence(self):
        exact = "The venue opens daily at 9am."
        docs = [
            document(url="https://a.example/visit", text=exact),
            document(url="https://b.example/guide", text=exact),
        ]
        result = RetrievalResult(documents=docs, queries_run=["q"])
        replies = [
            extraction(quote=exact, text="The venue opens daily at 9am.", name="The Venue"),
            extraction(quote=exact, text="The venue opens daily at 9am.", name="the venue"),
        ]
        explorer, _ = agent(replies, result, max_documents_per_batch=1)
        outcome = await explorer.research(brief())

        assert len(outcome.bundle.places) == 1
        assert len(outcome.bundle.claims) == 1
        assert len(outcome.bundle.claims[0].evidence_ids) == 2
        assert len(outcome.bundle.evidence) == 2
        assert outcome.bundle.places[0].claim_ids == [outcome.bundle.claims[0].id]

    async def test_evidence_per_claim_is_capped(self):
        exact = "The venue opens daily at 9am."
        docs = [document(url=f"https://{letter}.example/visit", text=exact) for letter in "abc"]
        result = RetrievalResult(documents=docs, queries_run=["q"])
        replies = [extraction(quote=exact, text=exact, name="Venue") for _ in range(3)]
        explorer, _ = agent(
            replies,
            result,
            max_documents_per_batch=1,
            max_evidence_per_claim=2,
        )
        outcome = await explorer.research(brief())
        assert len(outcome.bundle.claims[0].evidence_ids) == 2
        assert len(outcome.bundle.evidence) == 2
        assert any("Evidence limit reached" in gap for gap in outcome.bundle.gaps)

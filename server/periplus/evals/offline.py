"""The two seams that make an eval run offline, deterministic and free.

Retrieval and the model are the only parts of the pipeline that reach outside the process.
Replacing exactly those two — and nothing else — is what lets a case exercise the real
:class:`~periplus.orchestrator.hermes.Hermes`, the real agents, the real gates and the real
budget accounting, with no network, no API key and no bill.

What is *not* replaced matters more than what is. Grounding, quote rejection, dedup,
freshness downgrades, batch ceilings, gate rejection and cost accounting are all the
production code paths. A case that passes here is not a mock agreeing with itself.
"""

from __future__ import annotations

import json
from datetime import UTC

from periplus.agents.verification import (
    SemanticVerdict,
    VerificationBatch,
    VerificationDecision,
)
from periplus.evals.case import CorpusPage, VerdictLabel
from periplus.llm.base import LLMClient, LLMError, Message, RawResponse, StagePolicy
from periplus.models import SourceKind
from periplus.retrieval.document import SourceDocument, classify_source
from periplus.retrieval.retriever import RetrievalResult

__all__ = ["CorpusRetriever", "VerdictOracle", "documents_for"]

#: The marker :func:`periplus.agents.verification._verification_prompt` puts in front of its
#: payload. Matched rather than assumed so a prompt rewrite that changes the payload shape
#: fails loudly here instead of producing quietly wrong verdicts.
_VERIFY_PREAMBLE = "CLAIM AND EVIDENCE GROUPS"

_FALLBACK_LABEL = VerdictLabel(
    match="*",
    verdict=SemanticVerdict.UNSUPPORTED,
    confidence=0.5,
    reason="No golden-set label matched this claim.",
    cite_supporting=False,
)


def documents_for(pages: tuple[CorpusPage, ...], *, subject: str | None) -> list[SourceDocument]:
    """Turn a case's corpus into the reading material retrieval would have produced.

    A page that does not declare its ``source_kind`` is classified by the same
    :func:`~periplus.retrieval.document.classify_source` production uses, so official-site
    and government detection stays under test rather than being handed the answer.
    """
    documents: list[SourceDocument] = []
    for page in pages:
        kind = page.source_kind
        if kind is SourceKind.UNKNOWN:
            kind = classify_source(page.url, subject=subject)
        documents.append(
            SourceDocument(
                url=page.url,
                text=page.text,
                title=page.title,
                published_at=page.published_at,
                source_kind=kind,
                query=page.query,
                fetched_at=page.fetched_at or _default_fetched_at(page),
            )
        )
    return documents


def _default_fetched_at(page: CorpusPage):
    """Undated pages fall back to their publication date, never to "now".

    ``fetched_at`` feeds :func:`~periplus.agents.verification.evidence_is_stale` whenever a
    page states no publication date, so defaulting it to the real clock would make a
    freshness assertion pass or fail depending on the day the suite ran.
    """
    from datetime import datetime

    if page.published_at is not None:
        return datetime.combine(page.published_at, datetime.min.time(), tzinfo=UTC)
    return datetime(1970, 1, 1, tzinfo=UTC)


class CorpusRetriever:
    """Serves a case's fixed corpus in place of search-and-fetch.

    Reports the counts a real :class:`~periplus.retrieval.retriever.Retriever` would report
    — every query attempted, every page treated as a fetch — so the run's budget accounting
    and the ``queries``/``fetches`` metrics are exercised rather than zeroed.
    """

    def __init__(self, pages: tuple[CorpusPage, ...]) -> None:
        self._pages = pages
        self.calls: list[list[str]] = []

    async def gather(
        self,
        queries: list[str],
        *,
        subject: str | None = None,
        seen_urls: set[str] | None = None,
    ) -> RetrievalResult:
        self.calls.append(list(queries))
        documents = documents_for(self._pages, subject=subject)
        if seen_urls:
            documents = [doc for doc in documents if doc.url not in seen_urls]
        return RetrievalResult(
            documents=documents,
            queries_run=list(queries),
            queries_attempted=len(queries),
            fetch_attempts=len(documents),
        )


class VerdictOracle(LLMClient):
    """Answers verification calls from the golden set's labels.

    This is the ground-truth auditor: it never reads evidence, it reads the case file. That
    is deliberate. With model judgement pinned, every movement in the verdict distribution
    is attributable to code — a freshness window, a no-evidence path, a batch ceiling, a
    duplicate-ID rejection — which is the only way an offline suite can tell a real
    regression from provider drift.

    It answers any number of batches, unlike a queue of canned replies, so adding a page to
    a corpus does not silently exhaust the script.
    """

    def __init__(
        self,
        labels: tuple[VerdictLabel, ...] | list[VerdictLabel],
        *,
        default: VerdictLabel | None = None,
        model: str = "eval-oracle",
    ) -> None:
        super().__init__(max_attempts=1, retry_backoff_seconds=0.0)
        self._labels = list(labels)
        self._default = default or _FALLBACK_LABEL
        self._model = model
        self.batches: list[list[dict]] = []
        self.unmatched: list[str] = []
        """Claims that fell through to the default label — reported, never swallowed."""

    def _label_for(self, subject: str, text: str) -> VerdictLabel:
        haystack = f"{subject} :: {text}".casefold()
        for label in self._labels:
            if label.match.casefold() in haystack:
                return label
        self.unmatched.append(f"{subject}: {text}")
        return self._default

    async def _chat(
        self,
        messages: list[Message],
        policy: StagePolicy,
        *,
        json_object: bool,
    ) -> RawResponse:
        user = next((m.content for m in reversed(messages) if m.role == "user"), "")
        marker = user.find(_VERIFY_PREAMBLE)
        if marker == -1:
            raise LLMError(
                "VerdictOracle was asked something that is not a verification batch; "
                "the verification prompt shape changed and the oracle needs updating"
            )
        try:
            groups = json.loads(user[marker + len(_VERIFY_PREAMBLE) :])
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"VerdictOracle could not parse the verification payload: {exc}"
            ) from exc
        if not isinstance(groups, list):
            raise LLMError("VerdictOracle expected a list of claim/evidence groups")

        self.batches.append(groups)
        decisions: list[VerificationDecision] = []
        for group in groups:
            claim = group.get("claim", {})
            label = self._label_for(claim.get("subject", ""), claim.get("text", ""))
            evidence_ids = [item["id"] for item in group.get("evidence", [])]
            supporting: list[str] = []
            conflicting: list[str] = []
            if label.cite_supporting:
                if label.verdict is SemanticVerdict.CONTRADICTED:
                    conflicting = evidence_ids
                elif label.verdict is not SemanticVerdict.UNSUPPORTED:
                    supporting = evidence_ids
            decisions.append(
                VerificationDecision(
                    claim_id=claim.get("id", ""),
                    verdict=label.verdict,
                    confidence=label.confidence,
                    reason=label.reason,
                    supporting_evidence_ids=supporting,
                    conflicting_evidence_ids=conflicting,
                )
            )

        text = VerificationBatch(decisions=decisions).model_dump_json()
        return RawResponse(
            text=text,
            model=self._model,
            # Charged the same way ScriptedClient charges, so token and cost metrics are
            # non-trivial and the budget path is genuinely exercised.
            prompt_tokens=sum(len(m.content) for m in messages) // 4,
            completion_tokens=len(text) // 4,
        )

"""Chronicler: turn a grounded itinerary into human-facing content.

The model's job is prose — pacing an itinerary document, pitching an article, cutting a
social thread. It is never trusted to introduce a fact that is not already sitting in the
itinerary it was handed: the only claims and places in its prompt are the ones Navigator
already scheduled and Auditor already verified, and any claim ID a piece cites back is
checked against exactly that set, the same discipline Navigator applies to place and
claim references. A piece that fails validation is dropped with a caveat, never silently
rewritten — writing is the one stage here that is allowed to have taste, not the one
allowed to invent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from periplus.llm import LLMClient, StagePolicy
from periplus.llm.base import LLMError, StructuredOutputError
from periplus.models import (
    Claim,
    ContentPiece,
    ContentSet,
    Itinerary,
    ModelCall,
    Place,
    Stage,
    TripBrief,
)

#: The piece kinds Chronicler is asked for by default. A run may request a subset — e.g.
#: only ``captions`` for a quick social refresh — via :attr:`ContentAgent.kinds`.
DEFAULT_KINDS: tuple[str, ...] = ("itinerary_doc", "article", "thread", "captions", "checklist")


class Draft(BaseModel):
    """Strict base for model output; unknown fields are never silently ignored."""

    model_config = ConfigDict(extra="forbid")


class PieceDraft(Draft):
    kind: str = Field(min_length=1)
    title: str | None = None
    body: str = Field(min_length=1)
    claim_ids: list[str] = Field(
        default_factory=list, description="IDs of the supplied claims this piece states as fact."
    )


class ContentDraft(Draft):
    pieces: list[PieceDraft] = Field(default_factory=list)


@dataclass(slots=True)
class ContentOutcome:
    content: ContentSet
    calls: list[ModelCall] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(call.prompt_tokens + call.completion_tokens for call in self.calls)


class ContentAgent:
    """Draft content pieces from one itinerary, keeping every stated fact traceable."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        policy: StagePolicy,
        kinds: tuple[str, ...] = DEFAULT_KINDS,
        max_items: int = 60,
        max_claims: int = 200,
        max_pieces: int = 12,
        max_piece_chars: int = 8_000,
    ) -> None:
        self.llm = llm
        self.policy = policy
        self.kinds = tuple(dict.fromkeys(kinds))
        self.max_items = max(1, max_items)
        self.max_claims = max(1, max_claims)
        self.max_pieces = max(1, max_pieces)
        self.max_piece_chars = max(1, max_piece_chars)

    async def write(self, brief: TripBrief, itinerary: Itinerary) -> ContentOutcome:
        content = ContentSet(itinerary_id=itinerary.id)
        outcome = ContentOutcome(content=content)

        items = [item for day in itinerary.days for item in day.items]
        if not items:
            content.caveats.append("Itinerary has no scheduled items; nothing was written.")
            return outcome

        truncated_items = len(items) > self.max_items
        items = items[: self.max_items]
        used_item_ids = {item.id for item in items}

        allowed_claim_ids = {
            claim_id
            for day in itinerary.days
            for item in day.items
            if item.id in used_item_ids
            for claim_id in item.claim_ids
        }
        claims_by_id = {
            claim.id: claim for claim in itinerary.claims if claim.id in allowed_claim_ids
        }
        truncated_claims = len(claims_by_id) > self.max_claims
        if truncated_claims:
            claims_by_id = dict(list(claims_by_id.items())[: self.max_claims])

        places_by_id = {place.id: place for place in itinerary.places}

        try:
            completion = await self.llm.structured(
                stage=Stage.WRITE,
                schema=ContentDraft,
                system=_system_prompt(self.kinds),
                user=_prompt(brief, itinerary, items, places_by_id, claims_by_id),
                policy=self.policy,
            )
        except StructuredOutputError as exc:
            outcome.calls.extend(exc.attempts)
            content.caveats.append(f"Writing failed: {exc}")
            return outcome
        except LLMError as exc:
            content.caveats.append(f"Writing failed: {exc}")
            return outcome

        outcome.calls.extend(completion.calls)
        if truncated_items:
            content.caveats.append(
                f"Item limit reached: offered {self.max_items} of the scheduled items."
            )
        if truncated_claims:
            content.caveats.append(
                f"Claim limit reached: offered {self.max_claims} verified claims."
            )

        drafts = completion.value.pieces
        truncated_pieces = len(drafts) > self.max_pieces
        drafts = drafts[: self.max_pieces]
        if truncated_pieces:
            content.caveats.append(f"Piece limit reached: kept the first {self.max_pieces}.")

        content.pieces = _build_pieces(
            drafts,
            kinds=set(self.kinds),
            claims_by_id=claims_by_id,
            max_piece_chars=self.max_piece_chars,
            caveats=content.caveats,
        )
        # Carry over exactly the claims a kept piece actually cites — the same
        # self-contained-artifact discipline VerifiedBundle and Itinerary already follow
        # — so a later stage (Illustrator) can look up claim text without needing the
        # itinerary two stage boundaries back.
        cited_claim_ids = dict.fromkeys(
            claim_id for piece in content.pieces for claim_id in piece.claim_ids
        )
        content.claims = [claims_by_id[claim_id] for claim_id in cited_claim_ids]
        return outcome

    async def aclose(self) -> None:
        await self.llm.aclose()


Chronicler = ContentAgent


def _build_pieces(
    drafts: list[PieceDraft],
    *,
    kinds: set[str],
    claims_by_id: dict[str, Claim],
    max_piece_chars: int,
    caveats: list[str],
) -> list[ContentPiece]:
    pieces: list[ContentPiece] = []
    for draft in drafts:
        kind = draft.kind.strip()
        if kind not in kinds:
            caveats.append(f"Dropped a {kind!r} piece: not one of the requested kinds.")
            continue

        body = draft.body.strip()
        if not body:
            caveats.append(f"Dropped a {kind!r} piece: empty body.")
            continue
        if len(body) > max_piece_chars:
            caveats.append(
                f"Dropped a {kind!r} piece: {len(body)} characters exceeds the "
                f"{max_piece_chars}-character limit."
            )
            continue

        claim_ids = list(dict.fromkeys(draft.claim_ids))
        kept_claim_ids = [claim_id for claim_id in claim_ids if claim_id in claims_by_id]
        if len(kept_claim_ids) != len(claim_ids):
            caveats.append(
                f"A {kind!r} piece cited an unverified or unrelated claim; dropped the reference."
            )

        pieces.append(
            ContentPiece(
                kind=kind,
                title=draft.title.strip() if draft.title else None,
                body=body,
                word_count=len(body.split()),
                claim_ids=kept_claim_ids,
            )
        )
    return pieces


def _item_payload(item, places_by_id: dict[str, Place]) -> dict[str, object]:
    place = places_by_id.get(item.place_id) if item.place_id else None
    return {
        "id": item.id,
        "day": item.day,
        "start": item.start.isoformat() if item.start else None,
        "end": item.end.isoformat() if item.end else None,
        "title": item.title,
        "place": place.name if place else None,
        "notes": item.notes,
        "claim_ids": item.claim_ids,
    }


def _prompt(
    brief: TripBrief,
    itinerary: Itinerary,
    items: list,
    places_by_id: dict[str, Place],
    claims_by_id: dict[str, Claim],
) -> str:
    payload = {
        "destination": itinerary.destination,
        "language": brief.language,
        "interests": brief.interests,
        "pace": brief.pace.value,
        "days": [
            {"day": day.day, "date": day.date.isoformat(), "theme": day.theme}
            for day in itinerary.days
        ],
        "items": [_item_payload(item, places_by_id) for item in items],
        "claims": [
            {"id": claim.id, "subject": claim.subject, "text": claim.text}
            for claim in claims_by_id.values()
        ],
        "caveats": itinerary.caveats,
    }
    return "ITINERARY\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _system_prompt(kinds: tuple[str, ...]) -> str:
    kind_list = ", ".join(kinds)
    return f"""
You are Chronicler, the writing stage of an auditable travel-planning pipeline.
Turn the supplied itinerary into human-facing content. Produce one piece per requested
kind, chosen from exactly these kinds: {kind_list}.

Rules:
- The itinerary, places and claims are the only facts you know about this trip. Use no
  outside knowledge, memory, or invented specifics — no prices, hours, addresses, or
  events beyond what is supplied.
- If you state a fact that came from a supplied claim, cite that claim's id in claim_ids.
- Never cite a claim id that was not supplied.
- itinerary_doc: a clear day-by-day document a traveller would actually follow.
- article: a long-form narrative piece for a travel blog.
- thread: a short-form social thread, written as numbered posts within one body.
- captions: caption text for accompanying photos or video, ready to post.
- checklist: a practical pre-trip or packing checklist drawn from the itinerary's notes
  and caveats.
- Match the trip's language and keep tone consistent with its pace and interests.
- Every piece must have a non-empty body.
"""

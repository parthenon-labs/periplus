"""Editor: raise Chronicler's prose to scannable, structured, fact-checkable copy.

Nielsen Norman Group's web-reading research is the brief here: readers scan rather than
read word for word, so a piece earns its length only with meaningful subheadings, one
idea per paragraph, bulleted logistics (price, hours, how to get there), and an
inverted-pyramid lead — the point up front, not atmosphere first and a fact eventually.
Pure-atmosphere prose with nothing actionable in it ("X in August is a winter
playground", no price, no route, no duration) gets cut, not polished.

The one thing this stage is never trusted to do is state a new fact. Editor sees exactly
the claims Chronicler already cited on the piece it is revising — the same
self-contained-artifact discipline Illustrator already relies on (see
:class:`~periplus.agents.illustration.IllustrationAgent`) rather than the itinerary two
stage boundaries back — and any claim id a revision cites back is checked against
exactly that piece's own original set. A revision that cites an unlisted claim has that
reference dropped, never trusted; a revision this stage cannot validate at all falls back
to Chronicler's own, already-gated wording rather than losing the piece outright, because
unlike Chronicler, Editor is never a piece's only source of content — there is always a
pre-edit draft to fall back on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field

from periplus.llm import LLMClient, StagePolicy
from periplus.llm.base import LLMError, StructuredOutputError
from periplus.models import Claim, ContentPiece, ContentSet, ModelCall, Stage


class Draft(BaseModel):
    """Strict base for model output; unknown fields are never silently ignored."""

    model_config = ConfigDict(extra="forbid")


class PieceEditDraft(Draft):
    piece_id: str = Field(min_length=1)
    drop: bool = Field(
        default=False,
        description="True when the piece is pure atmosphere with nothing actionable in "
        "it and should be cut rather than tightened.",
    )
    reason: str | None = Field(
        default=None, description="Why the piece was dropped. Required when drop is true."
    )
    title: str | None = None
    body: str | None = Field(
        default=None, description="The revised body. Required unless drop is true."
    )
    claim_ids: list[str] = Field(
        default_factory=list,
        description="IDs of the facts this revision still states, drawn only from the "
        "claim_ids already on the piece being revised.",
    )


class EditBatch(Draft):
    pieces: list[PieceEditDraft] = Field(default_factory=list)


@dataclass(slots=True)
class EditOutcome:
    content: ContentSet
    calls: list[ModelCall] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(call.prompt_tokens + call.completion_tokens for call in self.calls)


class EditorAgent:
    """Revise a Chronicler :class:`ContentSet` in place, grounded to its own claims."""

    def __init__(
        self,
        *,
        llm: LLMClient,
        policy: StagePolicy,
        max_pieces: int = 12,
        max_piece_chars: int = 8_000,
        max_input_chars: int = 40_000,
    ) -> None:
        self.llm = llm
        self.policy = policy
        self.max_pieces = max(1, max_pieces)
        self.max_piece_chars = max(1, max_piece_chars)
        self.max_input_chars = max(1, max_input_chars)

    async def edit(self, content: ContentSet) -> EditOutcome:
        # A fresh artifact, not a mutation of the one Chronicler produced — `content`
        # may still be `run.content` elsewhere, the same "clone, then build" contract
        # IllustrationAgent already follows for the stage after this one.
        edited = ContentSet(**content.model_dump())
        outcome = EditOutcome(content=edited)

        if not edited.pieces:
            edited.caveats.append("No content pieces to edit; nothing was revised.")
            return outcome

        offered = edited.pieces
        truncated = len(offered) > self.max_pieces
        offered = offered[: self.max_pieces]
        carried_over = edited.pieces[self.max_pieces :]
        if truncated:
            edited.caveats.append(
                f"Piece limit reached: only the first {self.max_pieces} pieces were offered "
                "for editing; the rest carried over unedited."
            )

        by_id = {piece.id: piece for piece in offered}
        claims_by_id = {claim.id: claim for claim in edited.claims}

        prompt = _prompt(offered, claims_by_id)
        if len(prompt) > self.max_input_chars:
            edited.caveats.append(
                f"Editing input exceeded the {self.max_input_chars}-character budget; "
                "kept Chronicler's draft unchanged."
            )
            return outcome

        try:
            completion = await self.llm.structured(
                stage=Stage.EDIT,
                schema=EditBatch,
                system=_SYSTEM_PROMPT,
                user=prompt,
                policy=self.policy,
            )
        except StructuredOutputError as exc:
            outcome.calls.extend(exc.attempts)
            edited.caveats.append(f"Editing failed: {exc}; kept Chronicler's draft unchanged.")
            return outcome
        except LLMError as exc:
            edited.caveats.append(f"Editing failed: {exc}; kept Chronicler's draft unchanged.")
            return outcome

        outcome.calls.extend(completion.calls)

        revised, changed = _apply_edits(
            completion.value.pieces,
            offered=offered,
            by_id=by_id,
            max_piece_chars=self.max_piece_chars,
            caveats=edited.caveats,
        )

        edited.pieces = [*revised, *carried_over]
        edited.edited = changed

        # Same self-contained-artifact carry-over ContentAgent already does for
        # Illustrator: keep exactly the claims a kept piece still cites.
        cited_claim_ids = dict.fromkeys(
            claim_id for piece in edited.pieces for claim_id in piece.claim_ids
        )
        edited.claims = [
            claims_by_id[claim_id] for claim_id in cited_claim_ids if claim_id in claims_by_id
        ]
        return outcome

    async def aclose(self) -> None:
        await self.llm.aclose()


Editor = EditorAgent


def _apply_edits(
    drafts: list[PieceEditDraft],
    *,
    offered: list[ContentPiece],
    by_id: dict[str, ContentPiece],
    max_piece_chars: int,
    caveats: list[str],
) -> tuple[list[ContentPiece], bool]:
    revised: dict[str, ContentPiece] = {}
    dropped: set[str] = set()
    changed = False
    seen: set[str] = set()

    for draft in drafts:
        piece = by_id.get(draft.piece_id)
        if piece is None:
            caveats.append(
                "Editor referenced a piece id that was not offered for editing; ignored."
            )
            continue
        if draft.piece_id in seen:
            caveats.append(
                f"Editor returned more than one revision for a {piece.kind!r} piece; "
                "kept the first."
            )
            continue
        seen.add(draft.piece_id)

        if draft.drop:
            reason = (draft.reason or "").strip() or "no actionable content"
            caveats.append(f"Dropped a {piece.kind!r} piece: {reason}")
            dropped.add(piece.id)
            changed = True
            continue

        body = (draft.body or "").strip()
        if not body:
            caveats.append(
                f"Editor's revision for a {piece.kind!r} piece was empty; kept the original."
            )
            revised[piece.id] = piece
            continue
        if len(body) > max_piece_chars:
            caveats.append(
                f"Editor's revision for a {piece.kind!r} piece exceeded the "
                f"{max_piece_chars}-character limit; kept the original."
            )
            revised[piece.id] = piece
            continue

        allowed_claim_ids = set(piece.claim_ids)
        claim_ids = list(dict.fromkeys(draft.claim_ids))
        kept_claim_ids = [claim_id for claim_id in claim_ids if claim_id in allowed_claim_ids]
        if len(kept_claim_ids) != len(claim_ids):
            caveats.append(
                f"A revised {piece.kind!r} piece cited a claim not already on that piece; "
                "dropped the reference."
            )

        title = draft.title.strip() if draft.title else piece.title
        if body == piece.body and title == piece.title and kept_claim_ids == piece.claim_ids:
            revised[piece.id] = piece
            continue

        changed = True
        revised[piece.id] = piece.model_copy(
            update={
                "title": title,
                "body": body,
                "word_count": len(body.split()),
                "claim_ids": kept_claim_ids,
            }
        )

    # A piece the model never mentioned is kept exactly as Chronicler wrote it — silence
    # is not a drop; a piece the model explicitly dropped is the one case that is.
    ordered = [
        revised.get(piece.id, piece) for piece in offered if piece.id not in dropped
    ]
    return ordered, changed


def _piece_payload(piece: ContentPiece, claims_by_id: dict[str, Claim]) -> dict[str, object]:
    cited = (claims_by_id[claim_id] for claim_id in piece.claim_ids if claim_id in claims_by_id)
    return {
        "id": piece.id,
        "kind": piece.kind,
        "title": piece.title,
        "body": piece.body,
        "claims": [
            {"id": claim.id, "subject": claim.subject, "text": claim.text} for claim in cited
        ],
    }


def _prompt(pieces: list[ContentPiece], claims_by_id: dict[str, Claim]) -> str:
    payload = [_piece_payload(piece, claims_by_id) for piece in pieces]
    return "PIECES\n" + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


_SYSTEM_PROMPT = """
You are Editor, the copy-editing stage of an auditable travel-planning pipeline. Revise
each supplied piece for a reader who scans rather than reads word for word. Return
exactly one decision per supplied piece id, with no extras or duplicates.

Rules:
- The only facts you know are the claims already listed on the piece you are revising.
  Use no outside knowledge, memory, or invented specifics — no new prices, hours,
  addresses, durations, or events.
- If your revised body states a fact, cite that claim's id in claim_ids. Never cite a
  claim id that is not already listed on that piece.
- You may cut, reorder, and tighten wording. You may never add a claim.
- Prefer meaningful subheadings over none, one idea per paragraph, and bulleted lists
  for logistics — price, hours, how to get there, duration.
- Lead with the point (inverted pyramid): the concrete experience, the route or
  duration, the cost, the food — not atmosphere first and a fact eventually.
- Cut pure-atmosphere prose that states no price, route, duration, or other actionable
  fact — sentences like "X in August is a winter playground" with nothing concrete
  behind them do not survive a revision.
- If an entire piece is nothing but that kind of prose and has no claims worth keeping,
  set drop to true and give a one-sentence reason instead of forcing a rewrite.
- Otherwise return the full revised body — not a diff or a description of the change.
- Keep the piece's kind, language, and tone; only itinerary_doc and checklist pieces
  should lean on bullets — article, thread, and captions stay prose, tightened not
  bulletized.
"""


__all__ = ["EditBatch", "EditOutcome", "Editor", "EditorAgent", "PieceEditDraft"]

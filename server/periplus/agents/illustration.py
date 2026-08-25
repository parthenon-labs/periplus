"""Illustrator: turn already-verified claims into images, never a model's guess.

This stage has no judgement call the way Navigator or Chronicler do. Which subjects get
illustrated and what prompt describes them is decided by plain application code reading
claim text Auditor already verified — never a model asked to "imagine" a destination. The
only place free text enters is the claim itself, which arrived from research and was
checked before Illustrator ever saw it.

Image generation is optional infrastructure, not a hard dependency: when no provider is
configured, this stage does not crash. It produces an artifact with zero images and a
plain-English caveat and continues — the same graceful-degrade contract
:class:`~periplus.agents.navigation.NavigationAgent` already applies to its distance
provider.

Retries are this agent's job, per subject, and that is why both providers in
:mod:`periplus.media.images` construct their SDK client with ``max_retries=0``: a retry
hidden inside the SDK is a call the run never sees. A subject is reattempted only for a
:class:`~periplus.media.TransientImageGenerationError` — a rate limit, a timeout, a 5xx —
and each subject's budget is its own, so one flaky prompt cannot consume the attempts
another subject would have had.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from periplus.media import (
    GeneratedImage,
    ImageGenerationError,
    ImageProvider,
    ImageRequest,
    TransientImageGenerationError,
)
from periplus.models import (
    Claim,
    ClaimKind,
    ContentSet,
    IllustratedContentSet,
    Illustration,
    ModelCall,
)


@dataclass(slots=True)
class IllustrationOutcome:
    illustrated: IllustratedContentSet
    #: Always empty today — image generation is not an LLM call — kept for symmetry with
    #: every other stage's Outcome so :class:`~periplus.orchestrator.stages.StageResult`
    #: construction stays uniform across adapters.
    calls: list[ModelCall] = field(default_factory=list)


class IllustrationAgent:
    """Bind at most ``max_images`` generated pictures to the claims a run already wrote
    content about, one image per distinct subject.
    """

    def __init__(
        self,
        *,
        provider: ImageProvider | None = None,
        max_images: int = 6,
        size: str = "1024x1024",
        quality: str = "auto",
        max_attempts: int = 2,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.provider = provider
        self.max_images = max(1, max_images)
        self.size = size
        self.quality = quality
        #: Attempts per subject, transient failures only. 1 disables retry.
        self.max_attempts = max(1, max_attempts)
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds)

    async def illustrate(self, content: ContentSet) -> IllustrationOutcome:
        illustrated = IllustratedContentSet(**content.model_dump())
        outcome = IllustrationOutcome(illustrated=illustrated)

        selections = _select_subjects(content, max_subjects=self.max_images)
        if not selections:
            illustrated.caveats.append(
                "No cited claim had illustratable text; nothing was illustrated."
            )
            return outcome

        if self.provider is None:
            illustrated.caveats.append(
                "No image-generation provider configured; illustrations were not produced."
            )
            return outcome

        for subject, claim_ids, prompt in selections:
            generated = await self._generate(subject, prompt, illustrated.caveats)
            if generated is None:
                continue
            illustrated.images.append(_to_illustration(subject, claim_ids, prompt, generated))

        return outcome

    async def _generate(
        self, subject: str, prompt: str, caveats: list[str]
    ) -> GeneratedImage | None:
        """One subject's image, reattempted only while the provider itself is the problem.

        A non-transient :class:`~periplus.media.ImageGenerationError` — a rejected prompt,
        a malformed response, a missing key — would fail the same way again, so it is
        recorded as a caveat on the first attempt rather than paid for twice. Either way
        this returns ``None`` instead of raising: a missing picture is a degraded
        artifact, never a failed run.
        """
        assert self.provider is not None
        request = ImageRequest(prompt=prompt, size=self.size, quality=self.quality)
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await self.provider.generate(request)
            except TransientImageGenerationError as exc:
                if attempt == self.max_attempts:
                    caveats.append(
                        f"Illustration for {subject!r} failed after {attempt} attempt(s): {exc}"
                    )
                    return None
                if self.retry_backoff_seconds:
                    await asyncio.sleep(self.retry_backoff_seconds * attempt)
            except ImageGenerationError as exc:
                caveats.append(f"Illustration for {subject!r} failed: {exc}")
                return None
        return None

    async def aclose(self) -> None:
        close_provider = getattr(self.provider, "aclose", None)
        if close_provider is not None:
            await close_provider()


def _to_illustration(
    subject: str, claim_ids: list[str], prompt: str, generated: GeneratedImage
) -> Illustration:
    return Illustration(
        subject=subject,
        claim_ids=claim_ids,
        prompt=prompt,
        data_base64=generated.data_base64,
        mime_type=generated.mime_type,
        model=generated.model,
    )


def _select_subjects(
    content: ContentSet, *, max_subjects: int
) -> list[tuple[str, list[str], str]]:
    """Pick which claims get illustrated and what to ask the provider for.

    Deterministic and grounded: subjects are ordered by first citation across the
    content pieces actually kept, one image per distinct claim subject (so three claims
    about the same museum yield one photo of the museum, not three), and the prompt is a
    template filled from that claim's own subject and text — never free-generated.
    """
    cited_claim_ids = dict.fromkeys(
        claim_id for piece in content.pieces for claim_id in piece.claim_ids
    )
    claims_by_id = {claim.id: claim for claim in content.claims}

    by_subject: dict[str, list[Claim]] = {}
    order: list[str] = []
    for claim_id in cited_claim_ids:
        claim = claims_by_id.get(claim_id)
        if claim is None or not claim.text.strip():
            continue
        if claim.subject not in by_subject:
            order.append(claim.subject)
        by_subject.setdefault(claim.subject, []).append(claim)

    selections: list[tuple[str, list[str], str]] = []
    for subject in order[:max_subjects]:
        claims = by_subject[subject]
        # DESCRIPTION claims read as prose ("a grand 19th-century facade overlooking the
        # park"); a PRICE or HOURS claim reads as a fact sheet entry — prefer the former
        # for a prompt when one is available, but never drop the subject for lacking one.
        representative = next(
            (claim for claim in claims if claim.kind is ClaimKind.DESCRIPTION), claims[0]
        )
        selections.append(
            (subject, [claim.id for claim in claims], _build_prompt(subject, representative.text))
        )
    return selections


def _build_prompt(subject: str, text: str) -> str:
    detail = text.strip().rstrip(".")
    return (
        f"Editorial travel photograph of {subject}. {detail}. "
        "Natural lighting, photorealistic, no text overlays or watermarks."
    )


__all__ = ["IllustrationAgent", "IllustrationOutcome"]

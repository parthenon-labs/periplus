"""Offline contract tests for Illustrator, the illustration agent."""

from __future__ import annotations

from periplus.agents.illustration import IllustrationAgent
from periplus.media.images import (
    GeneratedImage,
    ImageGenerationError,
    ScriptedImages,
    TransientImageGenerationError,
)
from periplus.models import Claim, ClaimKind, ContentPiece, ContentSet


def make_claim(**overrides) -> Claim:
    values = {
        "id": "claim-1",
        "subject": "Museo del Prado",
        "text": "The Prado's grand 19th-century facade overlooks the Paseo del Prado.",
        "kind": ClaimKind.DESCRIPTION,
        "evidence_ids": ["evidence-1"],
    }
    values.update(overrides)
    return Claim(**values)


def make_piece(**overrides) -> ContentPiece:
    values = {
        "kind": "itinerary_doc",
        "body": "Visit the Prado in the morning.",
        "claim_ids": ["claim-1"],
    }
    values.update(overrides)
    return ContentPiece(**values)


def content(*, pieces: list[ContentPiece], claims: list[Claim]) -> ContentSet:
    return ContentSet(itinerary_id="itinerary-1", pieces=pieces, claims=claims)


def agent(*, provider=None, **overrides) -> IllustrationAgent:
    return IllustrationAgent(provider=provider, **overrides)


class TestSelectionAndGeneration:
    async def test_generates_one_image_per_cited_claim(self):
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=", model="gpt-image-1")])
        illustrator = agent(provider=provider)
        piece = make_piece()

        outcome = await illustrator.illustrate(content(pieces=[piece], claims=[make_claim()]))

        illustrated = outcome.illustrated
        assert len(illustrated.images) == 1
        image = illustrated.images[0]
        assert image.subject == "Museo del Prado"
        assert image.claim_ids == ["claim-1"]
        assert image.data_base64 == "aGVsbG8="
        assert image.model == "gpt-image-1"
        # Prompt is templated straight from the claim, never free-generated.
        assert "Museo del Prado" in image.prompt
        assert "grand 19th-century facade" in image.prompt
        assert len(provider.requests) == 1
        # Everything else on the content set travels through untouched.
        assert illustrated.pieces == [piece]
        assert illustrated.caveats == []

    async def test_dedupes_multiple_claims_about_the_same_subject_into_one_image(self):
        hours_claim = make_claim(
            id="claim-2", kind=ClaimKind.HOURS, text="Opens at 10am daily."
        )
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=")])
        illustrator = agent(provider=provider)

        outcome = await illustrator.illustrate(
            content(
                pieces=[make_piece(claim_ids=["claim-1", "claim-2"])],
                claims=[make_claim(), hours_claim],
            )
        )

        assert len(outcome.illustrated.images) == 1
        assert set(outcome.illustrated.images[0].claim_ids) == {"claim-1", "claim-2"}
        # The DESCRIPTION claim is preferred for the prompt over the HOURS one.
        assert "facade" in outcome.illustrated.images[0].prompt

    async def test_respects_the_max_images_bound(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park", text="A vast city park.")
        provider = ScriptedImages(
            [GeneratedImage(data_base64="aGVsbG8="), GeneratedImage(data_base64="d29ybGQ=")]
        )
        illustrator = agent(provider=provider, max_images=1)

        outcome = await illustrator.illustrate(
            content(
                pieces=[make_piece(claim_ids=["claim-1", "claim-2"])],
                claims=[make_claim(), second_claim],
            )
        )

        assert len(outcome.illustrated.images) == 1
        assert len(provider.requests) == 1

    async def test_uncited_claims_are_never_illustrated(self):
        uncited = make_claim(id="claim-2", subject="Retiro Park")
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=")])
        illustrator = agent(provider=provider)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece(claim_ids=["claim-1"])], claims=[make_claim(), uncited])
        )

        assert len(outcome.illustrated.images) == 1
        assert outcome.illustrated.images[0].subject == "Museo del Prado"

    async def test_no_pieces_is_a_caveat_not_a_crash(self):
        illustrator = agent(provider=ScriptedImages())

        outcome = await illustrator.illustrate(content(pieces=[], claims=[]))

        assert outcome.illustrated.images == []
        assert any("nothing was illustrated" in c for c in outcome.illustrated.caveats)

    async def test_a_failed_generation_is_a_caveat_not_a_crash_and_others_still_run(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park", text="A vast city park.")
        provider = ScriptedImages(
            [ImageGenerationError("provider is down"), GeneratedImage(data_base64="d29ybGQ=")]
        )
        illustrator = agent(provider=provider)

        outcome = await illustrator.illustrate(
            content(
                pieces=[make_piece(claim_ids=["claim-1", "claim-2"])],
                claims=[make_claim(), second_claim],
            )
        )

        assert len(outcome.illustrated.images) == 1
        assert outcome.illustrated.images[0].subject == "Retiro Park"
        assert any("Museo del Prado" in c and "failed" in c for c in outcome.illustrated.caveats)


class TestGracefulDegrade:
    async def test_missing_provider_adds_one_caveat_and_produces_zero_images(self):
        illustrator = agent(provider=None)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        illustrated = outcome.illustrated
        assert illustrated.images == []
        caveats = [c for c in illustrated.caveats if "provider configured" in c]
        assert len(caveats) == 1
        # The artifact is still a fully-formed IllustratedContentSet, not a crash.
        assert illustrated.itinerary_id == "itinerary-1"
        assert illustrated.pieces[0].body == "Visit the Prado in the morning."

    async def test_missing_provider_never_calls_anything(self):
        illustrator = agent(provider=None)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        # No provider means no generate() call could have happened at all; nothing to
        # assert against a provider double here beyond the caveat and empty images above.
        assert outcome.calls == []


class TestPerSubjectRetries:
    """Both image providers construct their SDK client with ``max_retries=0`` on the
    stated grounds that retries are this agent's job, per subject. These are the tests
    that make that statement true rather than aspirational.
    """

    async def test_a_transient_failure_is_retried_and_can_succeed(self):
        provider = ScriptedImages(
            [
                TransientImageGenerationError("429 rate limited"),
                GeneratedImage(data_base64="aGVsbG8="),
            ]
        )
        illustrator = agent(provider=provider, max_attempts=2, retry_backoff_seconds=0)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        assert len(provider.requests) == 2
        assert len(outcome.illustrated.images) == 1
        assert outcome.illustrated.caveats == []

    async def test_a_permanent_failure_is_never_paid_for_twice(self):
        provider = ScriptedImages([ImageGenerationError("prompt rejected")])
        illustrator = agent(provider=provider, max_attempts=3, retry_backoff_seconds=0)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        assert len(provider.requests) == 1
        assert outcome.illustrated.images == []
        assert any("prompt rejected" in c for c in outcome.illustrated.caveats)

    async def test_exhausting_the_attempts_is_a_caveat_not_a_crash(self):
        provider = ScriptedImages(
            [TransientImageGenerationError("503"), TransientImageGenerationError("503")]
        )
        illustrator = agent(provider=provider, max_attempts=2, retry_backoff_seconds=0)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        assert len(provider.requests) == 2
        assert outcome.illustrated.images == []
        assert any("2 attempt(s)" in c for c in outcome.illustrated.caveats)

    async def test_each_subject_gets_its_own_attempt_budget(self):
        second_claim = make_claim(id="claim-2", subject="Retiro Park", text="A vast city park.")
        provider = ScriptedImages(
            [
                TransientImageGenerationError("503"),
                TransientImageGenerationError("503"),
                TransientImageGenerationError("503"),
                GeneratedImage(data_base64="d29ybGQ="),
            ]
        )
        illustrator = agent(provider=provider, max_attempts=2, retry_backoff_seconds=0)

        outcome = await illustrator.illustrate(
            content(
                pieces=[make_piece(claim_ids=["claim-1", "claim-2"])],
                claims=[make_claim(), second_claim],
            )
        )

        # The Prado burnt both of its attempts; Retiro Park still got two of its own.
        assert len(provider.requests) == 4
        assert [image.subject for image in outcome.illustrated.images] == ["Retiro Park"]

    async def test_max_attempts_of_one_disables_retry(self):
        provider = ScriptedImages(
            [TransientImageGenerationError("503"), GeneratedImage(data_base64="aGVsbG8=")]
        )
        illustrator = agent(provider=provider, max_attempts=1, retry_backoff_seconds=0)

        outcome = await illustrator.illustrate(
            content(pieces=[make_piece()], claims=[make_claim()])
        )

        assert len(provider.requests) == 1
        assert outcome.illustrated.images == []

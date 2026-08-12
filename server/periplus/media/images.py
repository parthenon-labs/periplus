"""The image-generation seam.

Illustrator needs a picture bound to something a claim already says, not a model's
free-associated fantasy of a destination. OpenAI's Images API is one provider — it sits
behind an interface for the same reason search, the model and distance already do: this
pipeline should not care which provider turns "the Prado's grand facade" into a picture,
only that the answer is a grounded :class:`GeneratedImage` it can attach to an
:class:`~periplus.models.Illustration`.

:class:`OpenAIImages` goes through the official ``openai`` SDK — the same dependency
:class:`~periplus.llm.openai_compatible.OpenAICompatibleClient` already uses for chat
completions — rather than hand-rolled HTTP, so request assembly and error classification
stay in one place. Endpoint verified against OpenAI's current Images API reference
(``POST /v1/images/generations``, 2026): the GPT image models (``gpt-image-1`` and
successors) always return base64-encoded image bytes in ``data[0].b64_json`` —
``response_format`` is a dall-e-2/dall-e-3-only parameter and is never sent here.

:class:`AgnesImages` is a second provider behind the same interface, preferred over
OpenAI's when its key is set — see ``build_image_provider`` and the geo package's
Google Maps/OpenRouteService precedent for the "preferred when both are set" convention.
Agnes AI exposes an OpenAI-compatible Images API (``AsyncOpenAI(base_url=...)`` works
against it unmodified), verified with a live call on 2026-08-12 against
``https://apihub.agnes-ai.com/v1`` with model ``agnes-image-2.1-flash``: unlike OpenAI's
own image models, Agnes returns the image as a URL in ``data[0].url`` (``b64_json`` is
empty), so this provider fetches those bytes over HTTPX and base64-encodes them itself —
``GeneratedImage.data_base64`` is always base64 bytes, never a URL, no matter the
provider.

Timeouts: Agnes's own troubleshooting docs put generation at "a few seconds to tens of
seconds" depending on prompt/size/load, and recommend a client-side timeout of 60-360s —
worst-case latency the shared, fetch-tuned ``request_timeout_seconds`` (30s default)
undershoots. ``build_image_provider`` therefore passes ``AgnesImages`` its own
``settings.agnes_image_timeout_seconds`` (120s default), leaving ``OpenAIImages`` on the
shared default since OpenAI's models typically answer well inside it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from base64 import b64encode
from dataclasses import dataclass
from typing import Any

import httpx

AGNES_IMAGES_BASE_URL = "https://apihub.agnes-ai.com/v1"


@dataclass(frozen=True, slots=True)
class ImageRequest:
    prompt: str
    size: str = "1024x1024"
    quality: str = "auto"


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    """One generated image. Always base64 bytes, never a URL — see module docstring."""

    data_base64: str
    mime_type: str = "image/png"
    model: str | None = None


class ImageGenerationError(RuntimeError):
    """The provider answered, but not with a usable image."""


class TransientImageGenerationError(ImageGenerationError):
    """Rate limited or provider-side failure; the same request may work shortly."""


class ImageProvider(ABC):
    @abstractmethod
    async def generate(self, request: ImageRequest) -> GeneratedImage:
        """Return one generated image for the given prompt."""

    async def aclose(self) -> None:
        return None


class OpenAIImages(ImageProvider):
    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-image-1",
        timeout_seconds: float = 60.0,
        client: Any = None,
    ) -> None:
        if not api_key:
            raise ImageGenerationError(
                "no OpenAI API key configured; set PERIPLUS_OPENAI_API_KEY"
            )
        self.model = model

        if client is not None:
            self._client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
                raise ImageGenerationError(
                    "the 'openai' package is required for live image generation"
                ) from exc
            # Retries are handled by IllustrationAgent, per subject — the same reasoning
            # OpenAICompatibleClient applies to chat completions: letting the SDK retry
            # underneath would hide calls the run's budget should see.
            self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds, max_retries=0)

    async def generate(self, request: ImageRequest) -> GeneratedImage:
        try:
            response = await self._client.images.generate(
                model=self.model,
                prompt=request.prompt,
                n=1,
                size=request.size,
                quality=request.quality,
            )
        except Exception as exc:
            raise self._classify(exc) from exc

        try:
            item = response.data[0]
            b64 = item.b64_json
        except (IndexError, AttributeError, TypeError) as exc:
            raise ImageGenerationError(
                "OpenAI Images response missing data[0].b64_json"
            ) from exc
        if not b64:
            raise ImageGenerationError("OpenAI Images response missing data[0].b64_json")

        output_format = getattr(response, "output_format", None) or "png"
        return GeneratedImage(
            data_base64=b64, mime_type=f"image/{output_format}", model=self.model
        )

    @staticmethod
    def _classify(exc: Exception) -> ImageGenerationError:
        """Decide whether the same request could plausibly succeed if retried.

        Mirrors :meth:`OpenAICompatibleClient._classify` — same SDK, same distinction
        between a provider blip worth another attempt and a request that would just fail
        the same way again.
        """
        import openai

        transient = (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
        if isinstance(exc, transient):
            return TransientImageGenerationError(str(exc))
        if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
            return TransientImageGenerationError(str(exc))
        return ImageGenerationError(str(exc))

    async def aclose(self) -> None:
        await self._client.close()


class AgnesImages(ImageProvider):
    """Agnes AI's Images API — OpenAI-compatible wire format, URL-based responses.

    Goes through the same ``openai`` SDK client as :class:`OpenAIImages` (Agnes speaks
    the same request/response shape at ``/v1/images/generations``), pointed at Agnes's
    base URL instead of OpenAI's. The one real difference, confirmed with a live call on
    2026-08-12 against model ``agnes-image-2.1-flash``: Agnes returns ``data[0].url``
    (``b64_json`` comes back empty), so this provider makes one follow-up HTTPS fetch for
    the image bytes and base64-encodes them itself, to keep :class:`GeneratedImage`'s
    "always base64, never a URL" contract true for every provider.
    """

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "agnes-image-2.1-flash",
        base_url: str = AGNES_IMAGES_BASE_URL,
        timeout_seconds: float = 60.0,
        client: Any = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        if not api_key:
            raise ImageGenerationError(
                "no Agnes API key configured; set PERIPLUS_AGNES_API_KEY"
            )
        self.model = model

        if client is not None:
            self._client = client
        else:
            try:
                from openai import AsyncOpenAI
            except ModuleNotFoundError as exc:  # pragma: no cover - depends on install extras
                raise ImageGenerationError(
                    "the 'openai' package is required for live image generation"
                ) from exc
            # Same reasoning as OpenAIImages: retries are IllustrationAgent's job, so the
            # SDK is told not to hide any attempt from the run's budget.
            self._client = AsyncOpenAI(
                api_key=api_key, base_url=base_url, timeout=timeout_seconds, max_retries=0
            )

        self._owns_http_client = http_client is None
        self._http_client = http_client or httpx.AsyncClient(timeout=timeout_seconds)

    async def generate(self, request: ImageRequest) -> GeneratedImage:
        try:
            response = await self._client.images.generate(
                model=self.model,
                prompt=request.prompt,
                n=1,
                size=request.size,
            )
        except Exception as exc:
            raise self._classify(exc) from exc

        try:
            item = response.data[0]
            url = item.url
        except (IndexError, AttributeError, TypeError) as exc:
            raise ImageGenerationError("Agnes Images response missing data[0].url") from exc
        if not url:
            raise ImageGenerationError("Agnes Images response missing data[0].url")

        image_bytes = await self._fetch(url)
        return GeneratedImage(
            data_base64=b64encode(image_bytes).decode("ascii"),
            mime_type="image/png",
            model=self.model,
        )

    async def _fetch(self, url: str) -> bytes:
        try:
            response = await self._http_client.get(url)
        except httpx.HTTPError as exc:
            raise TransientImageGenerationError(f"{type(exc).__name__}: {exc}") from exc

        if response.status_code != 200:
            detail = response.text[:200]
            if response.status_code == 429 or response.status_code >= 500:
                raise TransientImageGenerationError(
                    f"fetching generated image: HTTP {response.status_code}: {detail}"
                )
            raise ImageGenerationError(
                f"fetching generated image: HTTP {response.status_code}: {detail}"
            )

        return response.content

    @staticmethod
    def _classify(exc: Exception) -> ImageGenerationError:
        """Same distinction as :meth:`OpenAIImages._classify` — same SDK underneath."""
        import openai

        transient = (
            openai.APITimeoutError,
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        )
        if isinstance(exc, transient):
            return TransientImageGenerationError(str(exc))
        if isinstance(exc, openai.APIStatusError) and exc.status_code >= 500:
            return TransientImageGenerationError(str(exc))
        return ImageGenerationError(str(exc))

    async def aclose(self) -> None:
        await self._client.close()
        if self._owns_http_client:
            await self._http_client.aclose()


class ScriptedImages(ImageProvider):
    """Canned images for tests and offline development.

    Replies are consumed in order; an ``Exception`` entry is raised in place rather than
    returned — the same convention :class:`~periplus.llm.ScriptedClient` uses.
    """

    def __init__(self, replies: list[GeneratedImage | Exception] | None = None) -> None:
        self._replies = list(replies or [])
        self.requests: list[ImageRequest] = []

    def push(self, reply: GeneratedImage | Exception) -> None:
        self._replies.append(reply)

    async def generate(self, request: ImageRequest) -> GeneratedImage:
        self.requests.append(request)
        if not self._replies:
            raise ImageGenerationError("ScriptedImages ran out of replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


__all__ = [
    "AGNES_IMAGES_BASE_URL",
    "AgnesImages",
    "GeneratedImage",
    "ImageGenerationError",
    "ImageProvider",
    "ImageRequest",
    "OpenAIImages",
    "ScriptedImages",
    "TransientImageGenerationError",
]

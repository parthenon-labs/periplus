"""Media: grounded image generation for illustrations.

Illustrator imports from here and nowhere deeper, the same rule
:mod:`periplus.geo` and :mod:`periplus.retrieval` already follow.
``build_image_provider`` assembles the configured provider so Illustrator never has to
know it is OpenAI's Images API behind the interface.
"""

from __future__ import annotations

from periplus.config import Settings, get_settings
from periplus.media.images import (
    AgnesImages,
    GeneratedImage,
    ImageGenerationError,
    ImageProvider,
    ImageRequest,
    OpenAIImages,
    ScriptedImages,
    TransientImageGenerationError,
)

__all__ = [
    "AgnesImages",
    "GeneratedImage",
    "ImageGenerationError",
    "ImageProvider",
    "ImageRequest",
    "OpenAIImages",
    "ScriptedImages",
    "TransientImageGenerationError",
    "build_image_provider",
]


def build_image_provider(settings: Settings | None = None) -> ImageProvider:
    """Assemble the configured image provider. Agnes is preferred when both keys are
    set — the same "preferred when both are set" precedent as
    :func:`periplus.geo.build_distance_provider` for OpenRouteService over Google Maps.
    Callers check ``settings.has_agnes_image_key``/``has_openai_image_key`` first — see
    :func:`periplus.agents.build_illustration_agent` — the same way
    :func:`periplus.geo.build_distance_provider` is only reached once a key is known to
    exist, rather than checking here and returning ``None`` silently.
    """
    settings = settings or get_settings()
    if settings.has_agnes_image_key:
        return AgnesImages(
            settings.agnes_api_key.get_secret_value(),
            model=settings.agnes_image_model,
            timeout_seconds=settings.agnes_image_timeout_seconds,
        )
    return OpenAIImages(
        settings.openai_api_key.get_secret_value(),
        model=settings.illustration_image_model,
        timeout_seconds=settings.request_timeout_seconds,
    )

"""Tests for the image-generation seam.

Entirely offline. :class:`OpenAIImages` and :class:`AgnesImages` go through the
``openai`` SDK's ``AsyncOpenAI`` client, so they are exercised here with a fake client
double injected in place of a real one — the same way :mod:`periplus.llm` tests never
actually call a real chat-completions endpoint either. :class:`AgnesImages` additionally
fetches the generated image over HTTPX, which is exercised with a fake ``httpx``-shaped
client double the same way.
"""

from __future__ import annotations

from base64 import b64encode
from types import SimpleNamespace

import httpx
import pytest

from periplus.config import Settings
from periplus.media import build_image_provider
from periplus.media.images import (
    AgnesImages,
    GeneratedImage,
    ImageGenerationError,
    ImageRequest,
    OpenAIImages,
    ScriptedImages,
    TransientImageGenerationError,
)


class _FakeImagesEndpoint:
    def __init__(self, reply) -> None:
        self._reply = reply
        self.calls: list[dict] = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


class _FakeAsyncOpenAI:
    def __init__(self, reply) -> None:
        self.images = _FakeImagesEndpoint(reply)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def ok_response(*, b64: str = "aGVsbG8=", output_format: str = "png"):
    return SimpleNamespace(data=[SimpleNamespace(b64_json=b64)], output_format=output_format)


class TestOpenAIImages:
    async def test_sends_the_documented_request_shape(self):
        fake_client = _FakeAsyncOpenAI(ok_response())
        provider = OpenAIImages("secret-key", model="gpt-image-1", client=fake_client)

        image = await provider.generate(
            ImageRequest(prompt="A watercolor of a harbour", size="1024x1024", quality="high")
        )

        assert fake_client.images.calls == [
            {
                "model": "gpt-image-1",
                "prompt": "A watercolor of a harbour",
                "n": 1,
                "size": "1024x1024",
                "quality": "high",
            }
        ]
        assert image.data_base64 == "aGVsbG8="
        assert image.mime_type == "image/png"
        assert image.model == "gpt-image-1"

    async def test_constructor_rejects_an_empty_key(self):
        with pytest.raises(ImageGenerationError):
            OpenAIImages("", client=_FakeAsyncOpenAI(ok_response()))

    async def test_missing_data_is_a_permanent_error(self):
        fake_client = _FakeAsyncOpenAI(SimpleNamespace(data=[], output_format="png"))
        provider = OpenAIImages("key", client=fake_client)

        with pytest.raises(ImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_an_unexpected_provider_exception_is_wrapped_not_raised_raw(self):
        fake_client = _FakeAsyncOpenAI(RuntimeError("provider exploded"))
        provider = OpenAIImages("key", client=fake_client)

        with pytest.raises(ImageGenerationError, match="provider exploded"):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_aclose_closes_the_underlying_client(self):
        fake_client = _FakeAsyncOpenAI(ok_response())
        provider = OpenAIImages("key", client=fake_client)

        await provider.aclose()

        assert fake_client.closed is True


class _FakeHTTPResponse:
    def __init__(self, *, status_code: int = 200, content: bytes = b"", text: str = "") -> None:
        self.status_code = status_code
        self.content = content
        self.text = text


class _FakeHTTPClient:
    def __init__(self, reply) -> None:
        self._reply = reply
        self.calls: list[str] = []
        self.closed = False

    async def get(self, url, **kwargs):
        self.calls.append(url)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply

    async def aclose(self) -> None:
        self.closed = True


def agnes_ok_response(*, url: str = "https://cdn.agnes-ai.com/img/abc123.png"):
    return SimpleNamespace(data=[SimpleNamespace(url=url, b64_json=None)])


class TestAgnesImages:
    async def test_sends_the_documented_request_shape_and_fetches_the_url(self):
        fake_sdk_client = _FakeAsyncOpenAI(agnes_ok_response())
        fake_http_client = _FakeHTTPClient(_FakeHTTPResponse(content=b"raw image bytes"))
        provider = AgnesImages(
            "secret-key",
            model="agnes-image-2.1-flash",
            client=fake_sdk_client,
            http_client=fake_http_client,
        )

        image = await provider.generate(
            ImageRequest(prompt="A watercolor of a harbour", size="1024x1024")
        )

        assert fake_sdk_client.images.calls == [
            {
                "model": "agnes-image-2.1-flash",
                "prompt": "A watercolor of a harbour",
                "n": 1,
                "size": "1024x1024",
            }
        ]
        assert fake_http_client.calls == ["https://cdn.agnes-ai.com/img/abc123.png"]
        assert image.data_base64 == b64encode(b"raw image bytes").decode("ascii")
        assert image.mime_type == "image/png"
        assert image.model == "agnes-image-2.1-flash"

    async def test_constructor_rejects_an_empty_key(self):
        with pytest.raises(ImageGenerationError):
            AgnesImages("", client=_FakeAsyncOpenAI(agnes_ok_response()))

    async def test_missing_url_is_a_permanent_error(self):
        fake_client = _FakeAsyncOpenAI(SimpleNamespace(data=[]))
        provider = AgnesImages("key", client=fake_client, http_client=_FakeHTTPClient(None))

        with pytest.raises(ImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_an_empty_url_is_a_permanent_error(self):
        fake_client = _FakeAsyncOpenAI(agnes_ok_response(url=""))
        provider = AgnesImages("key", client=fake_client, http_client=_FakeHTTPClient(None))

        with pytest.raises(ImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_an_unexpected_sdk_exception_is_wrapped_not_raised_raw(self):
        fake_client = _FakeAsyncOpenAI(RuntimeError("provider exploded"))
        provider = AgnesImages("key", client=fake_client, http_client=_FakeHTTPClient(None))

        with pytest.raises(ImageGenerationError, match="provider exploded"):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_a_5xx_fetch_is_transient(self):
        fake_sdk_client = _FakeAsyncOpenAI(agnes_ok_response())
        fake_http_client = _FakeHTTPClient(_FakeHTTPResponse(status_code=503, text="down"))
        provider = AgnesImages("key", client=fake_sdk_client, http_client=fake_http_client)

        with pytest.raises(TransientImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_a_4xx_fetch_is_permanent(self):
        fake_sdk_client = _FakeAsyncOpenAI(agnes_ok_response())
        fake_http_client = _FakeHTTPClient(_FakeHTTPResponse(status_code=404, text="gone"))
        provider = AgnesImages("key", client=fake_sdk_client, http_client=fake_http_client)

        with pytest.raises(ImageGenerationError) as caught:
            await provider.generate(ImageRequest(prompt="x"))
        assert not isinstance(caught.value, TransientImageGenerationError)

    async def test_a_fetch_connection_error_is_transient(self):
        fake_sdk_client = _FakeAsyncOpenAI(agnes_ok_response())
        fake_http_client = _FakeHTTPClient(httpx.ConnectError("boom"))
        provider = AgnesImages("key", client=fake_sdk_client, http_client=fake_http_client)

        with pytest.raises(TransientImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_aclose_closes_the_sdk_client_but_leaves_an_injected_http_client_open(self):
        # Same ownership rule as GoogleMapsDistance/OpenRouteServiceDistance: a caller-owned
        # httpx.AsyncClient is theirs to close, not ours.
        fake_sdk_client = _FakeAsyncOpenAI(agnes_ok_response())
        fake_http_client = _FakeHTTPClient(_FakeHTTPResponse(content=b"x"))
        provider = AgnesImages("key", client=fake_sdk_client, http_client=fake_http_client)

        await provider.aclose()

        assert fake_sdk_client.closed is True
        assert fake_http_client.closed is False


class TestScriptedImages:
    async def test_returns_queued_replies_in_order(self):
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=")])

        image = await provider.generate(ImageRequest(prompt="x"))

        assert image.data_base64 == "aGVsbG8="
        assert provider.requests[0].prompt == "x"

    async def test_raises_a_queued_exception_in_place(self):
        provider = ScriptedImages([ImageGenerationError("boom")])

        with pytest.raises(ImageGenerationError, match="boom"):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_running_out_of_replies_raises(self):
        provider = ScriptedImages()

        with pytest.raises(ImageGenerationError):
            await provider.generate(ImageRequest(prompt="x"))

    async def test_push_queues_an_additional_reply(self):
        provider = ScriptedImages([GeneratedImage(data_base64="aGVsbG8=")])
        provider.push(GeneratedImage(data_base64="d29ybGQ="))

        first = await provider.generate(ImageRequest(prompt="x"))
        second = await provider.generate(ImageRequest(prompt="y"))

        assert (first.data_base64, second.data_base64) == ("aGVsbG8=", "d29ybGQ=")


class TestBuildImageProvider:
    """No network: only checks which provider class gets assembled."""

    def test_builds_openai_images_from_settings(self):
        settings = Settings(
            agnes_api_key="",
            openai_api_key="secret-key",
            illustration_image_model="gpt-image-1-mini",
        )

        provider = build_image_provider(settings)

        assert isinstance(provider, OpenAIImages)
        assert provider.model == "gpt-image-1-mini"

    def test_builds_agnes_images_from_settings(self):
        settings = Settings(agnes_api_key="secret-key", agnes_image_model="agnes-image-2.1-flash")

        provider = build_image_provider(settings)

        assert isinstance(provider, AgnesImages)
        assert provider.model == "agnes-image-2.1-flash"

    def test_prefers_agnes_when_both_keys_are_set(self):
        settings = Settings(agnes_api_key="agnes-key", openai_api_key="openai-key")

        provider = build_image_provider(settings)

        assert isinstance(provider, AgnesImages)

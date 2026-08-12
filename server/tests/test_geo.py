"""Tests for the distance seam.

Entirely offline. Google Maps runs against an in-process httpx transport, so the request
shape and the status-code mapping are exercised without a real key or a real call.
"""

from __future__ import annotations

import httpx
import pytest

from periplus.config import Settings
from periplus.geo import build_distance_provider
from periplus.geo.distance import (
    DistanceError,
    GoogleMapsDistance,
    NoRouteFound,
    OpenRouteServiceDistance,
    Route,
    RouteQuery,
    ScriptedDistance,
    TransientDistanceError,
    TravelMode,
)
from periplus.models import GeoPoint

PRADO = GeoPoint(lat=40.4138, lon=-3.6921)
RETIRO = GeoPoint(lat=40.4153, lon=-3.6844)


def transport(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def ok_response(*, meters: int = 850, seconds: int = 620, status: str = "OK") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "status": "OK",
            "rows": [
                {
                    "elements": [
                        {
                            "status": status,
                            "distance": {"value": meters, "text": f"{meters} m"},
                            "duration": {"value": seconds, "text": f"{seconds // 60} mins"},
                        }
                    ]
                }
            ],
        },
    )


class TestGoogleMapsDistance:
    def client(self, handler) -> GoogleMapsDistance:
        return GoogleMapsDistance("gmaps-test", client=transport(handler))

    async def test_maps_the_response(self):
        seen: dict = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return ok_response(meters=850, seconds=620)

        route = await self.client(handler).route(RouteQuery(PRADO, RETIRO, TravelMode.WALKING))
        assert route.meters == 850
        assert route.seconds == 620
        assert route.mode == TravelMode.WALKING
        assert seen["params"]["origins"] == "40.4138,-3.6921"
        assert seen["params"]["destinations"] == "40.4153,-3.6844"
        assert seen["params"]["mode"] == "walking"
        assert seen["params"]["key"] == "gmaps-test"
        assert "departure_time" not in seen["params"]

    async def test_departure_time_only_sent_for_transit(self):
        seen: dict = {}

        def handler(request):
            seen["params"] = dict(request.url.params)
            return ok_response()

        query = RouteQuery(PRADO, RETIRO, TravelMode.TRANSIT, departure_time=1_700_000_000)
        await self.client(handler).route(query)
        assert seen["params"]["departure_time"] == "1700000000"

        seen.clear()
        query = RouteQuery(PRADO, RETIRO, TravelMode.WALKING, departure_time=1_700_000_000)
        await self.client(handler).route(query)
        assert "departure_time" not in seen["params"]

    async def test_zero_results_is_no_route(self):
        def handler(request):
            return ok_response(status="ZERO_RESULTS")

        with pytest.raises(NoRouteFound):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    async def test_over_query_limit_is_transient(self):
        def handler(request):
            return httpx.Response(200, json={"status": "OVER_QUERY_LIMIT", "rows": []})

        with pytest.raises(TransientDistanceError):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    async def test_request_denied_is_permanent(self):
        def handler(request):
            return httpx.Response(200, json={"status": "REQUEST_DENIED", "rows": []})

        with pytest.raises(DistanceError) as caught:
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))
        assert not isinstance(caught.value, TransientDistanceError)

    async def test_server_error_is_transient(self):
        def handler(request):
            return httpx.Response(503, text="try later")

        with pytest.raises(TransientDistanceError):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    def test_missing_key_fails_loudly(self):
        with pytest.raises(DistanceError):
            GoogleMapsDistance("")


def ors_response(*, meters: float = 850, seconds: float = 620) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "features": [
                {"properties": {"summary": {"distance": meters, "duration": seconds}}},
            ]
        },
    )


class TestOpenRouteServiceDistance:
    def client(self, handler) -> OpenRouteServiceDistance:
        return OpenRouteServiceDistance("ors-test", client=transport(handler))

    async def test_maps_the_response(self):
        seen: dict = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["params"] = dict(request.url.params)
            seen["headers"] = dict(request.headers)
            return ors_response(meters=850, seconds=620)

        route = await self.client(handler).route(RouteQuery(PRADO, RETIRO, TravelMode.WALKING))
        assert route.meters == 850
        assert route.seconds == 620
        assert route.mode == TravelMode.WALKING
        assert "/foot-walking" in seen["url"]
        # ORS takes lon,lat — the reverse of Google's lat,lon.
        assert seen["params"]["start"] == "-3.6921,40.4138"
        assert seen["params"]["end"] == "-3.6844,40.4153"
        assert seen["headers"]["authorization"] == "ors-test"

    async def test_fractional_meters_round(self):
        def handler(request):
            return ors_response(meters=849.6, seconds=619.5)

        route = await self.client(handler).route(RouteQuery(PRADO, RETIRO, TravelMode.WALKING))
        assert route.meters == 850
        assert route.seconds == 620

    async def test_transit_has_no_profile(self):
        with pytest.raises(DistanceError):
            await self.client(lambda request: ors_response()).route(
                RouteQuery(PRADO, RETIRO, TravelMode.TRANSIT)
            )

    async def test_not_found_is_no_route(self):
        def handler(request):
            return httpx.Response(404, json={"error": {"message": "point not routable"}})

        with pytest.raises(NoRouteFound):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    async def test_rate_limited_is_transient(self):
        def handler(request):
            return httpx.Response(429, text="slow down")

        with pytest.raises(TransientDistanceError):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    async def test_server_error_is_transient(self):
        def handler(request):
            return httpx.Response(503, text="try later")

        with pytest.raises(TransientDistanceError):
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))

    async def test_bad_request_is_permanent(self):
        def handler(request):
            return httpx.Response(400, json={"error": {"message": "invalid coordinates"}})

        with pytest.raises(DistanceError) as caught:
            await self.client(handler).route(RouteQuery(PRADO, RETIRO))
        assert not isinstance(caught.value, TransientDistanceError)

    def test_missing_key_fails_loudly(self):
        with pytest.raises(DistanceError):
            OpenRouteServiceDistance("")


class TestScriptedDistance:
    async def test_registered_route_is_returned(self):
        provider = ScriptedDistance()
        provider.register(PRADO, RETIRO, TravelMode.WALKING, Route(850, 620, TravelMode.WALKING))

        route = await provider.route(RouteQuery(PRADO, RETIRO, TravelMode.WALKING))
        assert route.meters == 850
        assert len(provider.queries) == 1

    async def test_nearby_coordinates_still_match(self):
        provider = ScriptedDistance()
        provider.register(PRADO, RETIRO, TravelMode.WALKING, Route(850, 620, TravelMode.WALKING))

        nearby = GeoPoint(lat=PRADO.lat + 1e-7, lon=PRADO.lon)
        route = await provider.route(RouteQuery(nearby, RETIRO, TravelMode.WALKING))
        assert route.meters == 850


class TestBuildDistanceProvider:
    """No network: only checks which provider class gets assembled."""

    def test_prefers_ors_when_both_keys_are_set(self):
        settings = Settings(ors_api_key="ors-key", google_maps_api_key="maps-key")
        provider = build_distance_provider(settings)
        assert isinstance(provider, OpenRouteServiceDistance)

    def test_falls_back_to_google_maps_without_an_ors_key(self):
        settings = Settings(ors_api_key="", google_maps_api_key="maps-key")
        provider = build_distance_provider(settings)
        assert isinstance(provider, GoogleMapsDistance)

    async def test_unregistered_query_falls_back_to_default(self):
        default = Route(1000, 900, TravelMode.DRIVING)
        provider = ScriptedDistance(default=default)

        route = await provider.route(RouteQuery(PRADO, RETIRO, TravelMode.DRIVING))
        assert route == default

    async def test_unregistered_query_without_default_raises(self):
        provider = ScriptedDistance()
        with pytest.raises(NoRouteFound):
            await provider.route(RouteQuery(PRADO, RETIRO))

    async def test_different_mode_is_a_different_key(self):
        provider = ScriptedDistance()
        provider.register(PRADO, RETIRO, TravelMode.WALKING, Route(850, 620, TravelMode.WALKING))
        with pytest.raises(NoRouteFound):
            await provider.route(RouteQuery(PRADO, RETIRO, TravelMode.DRIVING))

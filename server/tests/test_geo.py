"""Tests for the distance seam.

Entirely offline. Google Maps runs against an in-process httpx transport, so the request
shape and the status-code mapping are exercised without a real key or a real call.
"""

from __future__ import annotations

import httpx
import pytest

from periplus.geo.distance import (
    DistanceError,
    GoogleMapsDistance,
    NoRouteFound,
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

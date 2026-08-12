"""The distance seam.

Navigator needs real travel times between stops, not a model's guess at how far the
museum is from the hotel. Google Maps Distance Matrix is one provider — it sits behind
an interface for the same reason search and the model do: this pipeline should not care
which provider answers "how long to walk from A to B", only that the answer is a
grounded number it can put in an :class:`~periplus.models.Transfer`. OpenRouteService is
the second implementation: no billing account required, only an email-verified key.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import httpx

from periplus.models import GeoPoint

DISTANCE_MATRIX_ENDPOINT = "https://maps.googleapis.com/maps/api/distancematrix/json"
ORS_DIRECTIONS_ENDPOINT = "https://api.openrouteservice.org/v2/directions"


class TravelMode(StrEnum):
    WALKING = "walking"
    DRIVING = "driving"
    TRANSIT = "transit"
    BICYCLING = "bicycling"


@dataclass(frozen=True, slots=True)
class RouteQuery:
    origin: GeoPoint
    destination: GeoPoint
    mode: TravelMode = TravelMode.WALKING
    """Transit departure, as epoch seconds. Google ignores this for other modes."""
    departure_time: int | None = None


@dataclass(frozen=True, slots=True)
class Route:
    """A grounded distance/duration pair for one query. Never a model's estimate."""

    meters: int
    seconds: int
    mode: TravelMode


class DistanceError(RuntimeError):
    """The provider answered, but not with a usable route."""


class TransientDistanceError(DistanceError):
    """Rate limited or provider-side failure; the same query may work shortly."""


class NoRouteFound(DistanceError):
    """The provider understood the request and found nothing connecting the two points."""


class DistanceProvider(ABC):
    @abstractmethod
    async def route(self, query: RouteQuery) -> Route:
        """Return the distance/duration for one origin-destination pair."""

    async def aclose(self) -> None:
        return None


class GoogleMapsDistance(DistanceProvider):
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        endpoint: str = DISTANCE_MATRIX_ENDPOINT,
    ) -> None:
        if not api_key:
            raise DistanceError(
                "no Google Maps API key configured; set PERIPLUS_GOOGLE_MAPS_API_KEY"
            )
        self.api_key = api_key
        self.endpoint = endpoint
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def route(self, query: RouteQuery) -> Route:
        params: dict[str, Any] = {
            "origins": _latlon(query.origin),
            "destinations": _latlon(query.destination),
            "mode": query.mode.value,
            # The key travels as a query param because that is the only form the Distance
            # Matrix API accepts; it still never ends up logged in this codebase, since
            # httpx params are not included in any log statement here.
            "key": self.api_key,
        }
        if query.departure_time is not None and query.mode is TravelMode.TRANSIT:
            params["departure_time"] = query.departure_time

        try:
            response = await self._client.get(self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise TransientDistanceError(f"{type(exc).__name__}: {exc}") from exc

        self._raise_for_status(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise DistanceError(f"unparseable Google Maps response: {exc}") from exc

        return self._to_route(body, query.mode)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        detail = response.text[:200]
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientDistanceError(f"HTTP {response.status_code}: {detail}")
        raise DistanceError(f"HTTP {response.status_code}: {detail}")

    @staticmethod
    def _to_route(body: dict[str, Any], mode: TravelMode) -> Route:
        top_status = body.get("status")
        if top_status in ("OVER_QUERY_LIMIT", "UNKNOWN_ERROR"):
            raise TransientDistanceError(f"Google Maps status: {top_status}")
        if top_status != "OK":
            raise DistanceError(f"Google Maps rejected the request: {top_status}")

        try:
            element = body["rows"][0]["elements"][0]
        except (IndexError, KeyError) as exc:
            raise DistanceError("Google Maps response missing rows/elements") from exc

        element_status = element.get("status")
        if element_status == "ZERO_RESULTS":
            raise NoRouteFound("no route found between origin and destination")
        if element_status != "OK":
            raise DistanceError(f"Google Maps element status: {element_status}")

        return Route(
            meters=element["distance"]["value"],
            seconds=element["duration"]["value"],
            mode=mode,
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


ORS_PROFILE_BY_MODE: dict[TravelMode, str] = {
    TravelMode.WALKING: "foot-walking",
    TravelMode.DRIVING: "driving-car",
    TravelMode.BICYCLING: "cycling-regular",
    # ORS has no public-transit profile; callers get a clear, permanent error rather
    # than a silently wrong driving/walking estimate standing in for a bus route.
}


class OpenRouteServiceDistance(DistanceProvider):
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_seconds: float = 30.0,
        endpoint: str = ORS_DIRECTIONS_ENDPOINT,
    ) -> None:
        if not api_key:
            raise DistanceError("no OpenRouteService API key configured; set PERIPLUS_ORS_API_KEY")
        self.api_key = api_key
        self.endpoint = endpoint
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def route(self, query: RouteQuery) -> Route:
        profile = ORS_PROFILE_BY_MODE.get(query.mode)
        if profile is None:
            raise DistanceError(f"OpenRouteService has no profile for {query.mode.value}")

        params: dict[str, Any] = {
            "start": _lonlat(query.origin),
            "end": _lonlat(query.destination),
        }
        headers = {"Authorization": self.api_key}

        try:
            response = await self._client.get(
                f"{self.endpoint}/{profile}", params=params, headers=headers
            )
        except httpx.HTTPError as exc:
            raise TransientDistanceError(f"{type(exc).__name__}: {exc}") from exc

        self._raise_for_status(response)

        try:
            body = response.json()
        except ValueError as exc:
            raise DistanceError(f"unparseable OpenRouteService response: {exc}") from exc

        return self._to_route(body, query.mode)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.status_code == 200:
            return
        detail = response.text[:200]
        if response.status_code == 404:
            raise NoRouteFound(f"HTTP 404: {detail}")
        if response.status_code == 429 or response.status_code >= 500:
            raise TransientDistanceError(f"HTTP {response.status_code}: {detail}")
        raise DistanceError(f"HTTP {response.status_code}: {detail}")

    @staticmethod
    def _to_route(body: dict[str, Any], mode: TravelMode) -> Route:
        try:
            summary = body["features"][0]["properties"]["summary"]
        except (IndexError, KeyError) as exc:
            raise DistanceError("OpenRouteService response missing a route summary") from exc

        return Route(
            meters=round(summary["distance"]), seconds=round(summary["duration"]), mode=mode
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()


class ScriptedDistance(DistanceProvider):
    """Canned routes for tests and offline development.

    Matched on ``(origin, destination, mode)``, rounded to five decimal places (about a
    metre of slop) so a test can build a query from real-looking coordinates without
    hand-tuning them to match a fixture exactly.
    """

    def __init__(
        self, responses: dict[tuple, Route] | None = None, *, default: Route | None = None
    ) -> None:
        self.responses: dict[tuple, Route] = dict(responses or {})
        self.default = default
        self.queries: list[RouteQuery] = []

    def register(
        self, origin: GeoPoint, destination: GeoPoint, mode: TravelMode, route: Route
    ) -> None:
        self.responses[_key(origin, destination, mode)] = route

    async def route(self, query: RouteQuery) -> Route:
        self.queries.append(query)
        key = _key(query.origin, query.destination, query.mode)
        if key in self.responses:
            return self.responses[key]
        if self.default is not None:
            return self.default
        raise NoRouteFound(f"ScriptedDistance has no route registered for {key}")


def _latlon(point: GeoPoint) -> str:
    return f"{point.lat},{point.lon}"


def _lonlat(point: GeoPoint) -> str:
    """ORS takes coordinates as lon,lat — the GeoJSON order, reversed from Google's."""
    return f"{point.lon},{point.lat}"


def _key(origin: GeoPoint, destination: GeoPoint, mode: TravelMode) -> tuple:
    return (
        round(origin.lat, 5),
        round(origin.lon, 5),
        round(destination.lat, 5),
        round(destination.lon, 5),
        mode,
    )


__all__ = [
    "DISTANCE_MATRIX_ENDPOINT",
    "ORS_DIRECTIONS_ENDPOINT",
    "ORS_PROFILE_BY_MODE",
    "DistanceError",
    "DistanceProvider",
    "GoogleMapsDistance",
    "NoRouteFound",
    "OpenRouteServiceDistance",
    "Route",
    "RouteQuery",
    "ScriptedDistance",
    "TransientDistanceError",
    "TravelMode",
]

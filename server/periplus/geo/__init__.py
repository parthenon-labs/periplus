"""Geo: grounded distance and travel time between two points.

Agents import from here and nowhere deeper, same rule as :mod:`periplus.retrieval`.
``build_distance_provider`` assembles the configured provider so Navigator never has to
know it is Google Maps behind the interface.
"""

from __future__ import annotations

from periplus.config import Settings, get_settings
from periplus.geo.distance import (
    DISTANCE_MATRIX_ENDPOINT,
    DistanceError,
    DistanceProvider,
    GoogleMapsDistance,
    NoRouteFound,
    Route,
    RouteQuery,
    ScriptedDistance,
    TransientDistanceError,
    TravelMode,
)

__all__ = [
    "DISTANCE_MATRIX_ENDPOINT",
    "DistanceError",
    "DistanceProvider",
    "GoogleMapsDistance",
    "NoRouteFound",
    "Route",
    "RouteQuery",
    "ScriptedDistance",
    "TransientDistanceError",
    "TravelMode",
    "build_distance_provider",
]


def build_distance_provider(settings: Settings | None = None) -> DistanceProvider:
    settings = settings or get_settings()
    return GoogleMapsDistance(
        settings.google_maps_api_key.get_secret_value(),
        timeout_seconds=settings.request_timeout_seconds,
    )

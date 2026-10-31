"""Weather via Open-Meteo (no API key, no account, no tracking).

This is one of only two tools that reach the network, and it can be switched
off entirely with ``NOVA_WEATHER_ENABLED=false``.
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, Field

from nova.config import Settings
from nova.logging_setup import get_logger
from nova.tools.base import RiskTier, Tool, ToolContext, ToolResult, compact_json

log = get_logger(__name__)

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT = 10.0

# https://open-meteo.com/en/docs - WMO weather interpretation codes.
WEATHER_CODES: dict[int, str] = {
    0: "clear sky",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    56: "light freezing drizzle",
    57: "freezing drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    66: "light freezing rain",
    67: "freezing rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    77: "snow grains",
    80: "light rain showers",
    81: "rain showers",
    82: "violent rain showers",
    85: "light snow showers",
    86: "snow showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with heavy hail",
}


class WeatherArgs(BaseModel):
    model_config = {"extra": "forbid"}

    location: str | None = Field(
        default=None,
        max_length=80,
        description="City name. Omit to use the configured default location.",
    )
    days: int = Field(default=1, ge=1, le=5, description="How many days of forecast.")


def describe_code(code: int | None) -> str:
    if code is None:
        return "unknown conditions"
    return WEATHER_CODES.get(int(code), f"weather code {code}")


class Weather(Tool):
    name: ClassVar[str] = "weather"
    description: ClassVar[str] = (
        "Current weather and a short forecast for a city. Needs internet access."
    )
    args_model: ClassVar[type[BaseModel]] = WeatherArgs
    risk: ClassVar[RiskTier] = RiskTier.SAFE

    def is_enabled(self, settings: Settings) -> bool:
        return settings.weather_enabled

    async def run(self, args: WeatherArgs, context: ToolContext) -> ToolResult:
        settings = context.settings
        if not settings.weather_enabled:
            return ToolResult.failure("weather lookups are disabled in the settings")

        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT, follow_redirects=False) as client:
                if args.location:
                    place = await self._geocode(client, args.location)
                    if place is None:
                        return ToolResult.failure(
                            f"could not find a place called {args.location!r}"
                        )
                    latitude, longitude, label = place
                elif (
                    settings.weather_latitude is not None and settings.weather_longitude is not None
                ):
                    latitude = settings.weather_latitude
                    longitude = settings.weather_longitude
                    label = settings.weather_location_name
                else:
                    return ToolResult.failure(
                        "no location given and no default location is configured"
                    )

                forecast = await self._forecast(client, latitude, longitude, args.days)
        except httpx.HTTPError as exc:
            log.warning("weather.request_failed", error=str(exc))
            return ToolResult.failure("the weather service could not be reached")

        current = forecast.get("current", {})
        daily = forecast.get("daily", {})
        summary: dict[str, Any] = {
            "location": label,
            "now": {
                "temperature_c": current.get("temperature_2m"),
                "feels_like_c": current.get("apparent_temperature"),
                "humidity_pct": current.get("relative_humidity_2m"),
                "wind_kph": current.get("wind_speed_10m"),
                "conditions": describe_code(current.get("weather_code")),
            },
            "forecast": [
                {
                    "date": date,
                    "min_c": low,
                    "max_c": high,
                    "conditions": describe_code(code),
                    "rain_chance_pct": rain,
                }
                for date, low, high, code, rain in zip(
                    daily.get("time", []),
                    daily.get("temperature_2m_min", []),
                    daily.get("temperature_2m_max", []),
                    daily.get("weather_code", []),
                    daily.get("precipitation_probability_max", []),
                    strict=False,
                )
            ][: args.days],
        }

        return ToolResult(
            ok=True,
            content=compact_json(summary),
            data={"weather": summary},
        )

    @staticmethod
    async def _geocode(client: httpx.AsyncClient, location: str) -> tuple[float, float, str] | None:
        response = await client.get(
            GEOCODE_URL,
            params={"name": location, "count": 1, "language": "en", "format": "json"},
        )
        response.raise_for_status()
        results = response.json().get("results") or []
        if not results:
            return None
        first = results[0]
        label_parts = [first.get("name"), first.get("admin1"), first.get("country")]
        label = ", ".join(part for part in label_parts if part)
        return float(first["latitude"]), float(first["longitude"]), label

    @staticmethod
    async def _forecast(
        client: httpx.AsyncClient, latitude: float, longitude: float, days: int
    ) -> dict[str, Any]:
        response = await client.get(
            FORECAST_URL,
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,apparent_temperature,relative_humidity_2m,"
                    "weather_code,wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_min,temperature_2m_max,weather_code,"
                    "precipitation_probability_max"
                ),
                "forecast_days": days,
                "timezone": "auto",
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        return payload

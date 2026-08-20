"""Open-Meteo API client.

Deliberately "dumb" per PROJECT_PRINCIPLES.md: knows only how to talk to
the API and parse its response into typed readings. Zero domain logic --
no windows, no scoring, no thresholds. Window-hour filtering belongs in
the coordinator, not here (same lesson carried over from sun_bathing).

Unit note: Open-Meteo's &wind_speed_unit= is a single global setting that
applies to every wind field in the response -- there's no way to request
km/h for 10m wind and m/s for the 300hPa pressure-level wind separately.
This client fetches the default (km/h) and converts only the jet stream
field to m/s during parsing, since score.py's HourlyConditions expects
jet_stream_wind_speed in m/s but wind_speed in km/h.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime

import aiohttp

BASE_URL = "https://api.open-meteo.com/v1/forecast"

# Open-Meteo variable names, confirmed against the live API docs
# (https://open-meteo.com/en/docs) -- do not guess these, they're easy
# to get subtly wrong (e.g. dew_point_2m, not dewpoint_2m).
HOURLY_VARIABLES = (
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "temperature_2m",
    "dew_point_2m",
    "visibility",
    "wind_speed_300hPa",
    "precipitation_probability",
    "wind_speed_10m",
)

KMH_TO_MS = 1000.0 / 3600.0


class OpenMeteoError(Exception):
    """Raised when the Open-Meteo API returns an error or unusable data."""


@dataclass
class OpenMeteoHourlyReading:
    """One hour of raw weather data, field names aligned with
    HourlyConditions in score.py (minus moon fields, which come from
    astral/HA sun helpers, not Open-Meteo -- see PROJECT_PRINCIPLES.md
    Phase 4)."""

    time: datetime
    low_cloud_cover: float  # %
    mid_cloud_cover: float  # %
    high_cloud_cover: float  # %
    temperature: float  # °C
    dew_point: float  # °C
    visibility: float  # m
    jet_stream_wind_speed: float  # m/s (converted from Open-Meteo's km/h)
    precipitation_probability: float  # %
    wind_speed: float  # km/h


class OpenMeteoClient:
    """Thin async wrapper around the Open-Meteo forecast endpoint.

    Takes a shared aiohttp.ClientSession (HA provides one) rather than
    creating its own -- standard practice so the caller controls
    connection pooling/lifecycle.
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    async def async_get_hourly_forecast(
        self,
        latitude: float,
        longitude: float,
        forecast_days: int = 2,
        timezone: str = "auto",
    ) -> list[OpenMeteoHourlyReading]:
        """Fetch and parse the hourly forecast for a location.

        Raises OpenMeteoError on any HTTP failure, malformed response, or
        missing expected field -- callers (the coordinator) should treat
        this as a single failure point to handle, not inspect internals.
        """
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "hourly": ",".join(HOURLY_VARIABLES),
            "forecast_days": forecast_days,
            "timezone": timezone,
        }

        try:
            async with self._session.get(BASE_URL, params=params) as response:
                if response.status != 200:
                    body = await response.text()
                    raise OpenMeteoError(
                        f"Open-Meteo returned HTTP {response.status}: {body}"
                    )
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise OpenMeteoError(f"Failed to reach Open-Meteo: {err}") from err

        if payload.get("error"):
            raise OpenMeteoError(
                f"Open-Meteo API error: {payload.get('reason', 'unknown reason')}"
            )

        return self._parse_hourly(payload)

    @staticmethod
    def _parse_hourly(payload: dict) -> list[OpenMeteoHourlyReading]:
        try:
            hourly = payload["hourly"]
            times = hourly["time"]
            low_cloud = hourly["cloud_cover_low"]
            mid_cloud = hourly["cloud_cover_mid"]
            high_cloud = hourly["cloud_cover_high"]
            temperature = hourly["temperature_2m"]
            dew_point = hourly["dew_point_2m"]
            visibility = hourly["visibility"]
            jet_stream_kmh = hourly["wind_speed_300hPa"]
            precipitation_probability = hourly["precipitation_probability"]
            wind_speed = hourly["wind_speed_10m"]
        except KeyError as err:
            raise OpenMeteoError(
                f"Open-Meteo response missing expected field: {err}"
            ) from err

        readings: list[OpenMeteoHourlyReading] = []
        for i, time_str in enumerate(times):
            try:
                readings.append(
                    OpenMeteoHourlyReading(
                        time=datetime.fromisoformat(time_str),
                        low_cloud_cover=low_cloud[i],
                        mid_cloud_cover=mid_cloud[i],
                        high_cloud_cover=high_cloud[i],
                        temperature=temperature[i],
                        dew_point=dew_point[i],
                        visibility=visibility[i],
                        jet_stream_wind_speed=jet_stream_kmh[i] * KMH_TO_MS,
                        precipitation_probability=precipitation_probability[i],
                        wind_speed=wind_speed[i],
                    )
                )
            except IndexError as err:
                raise OpenMeteoError(
                    f"Open-Meteo hourly arrays have mismatched lengths at index {i}"
                ) from err

        return readings
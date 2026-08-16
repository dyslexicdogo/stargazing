"""DataUpdateCoordinator for stargazing.

Thin orchestration glue only, per PROJECT_PRINCIPLES.md: fetch weather,
determine tonight's darkness window, filter to hours inside it, attach
moon position, score each hour. No business logic lives here -- that's
client.py (API), astro.py (windows/moon), and score.py (scoring).

TIMEZONE HANDLING -- the one genuinely tricky part of this file: astral
(used in astro.py) returns tz-aware datetimes, but Open-Meteo's response
(parsed in client.py) gives naive local datetimes with no UTC offset
attached. Comparing aware and naive datetimes directly raises TypeError
in Python, so window boundaries are stripped to naive local time before
filtering readings against them -- both represent the same wall-clock
local time as long as the same IANA timezone string is passed to both
astro.get_darkness_window() and client.async_get_hourly_forecast(),
which this coordinator ensures by using hass.config.time_zone (confirmed
via a real hass fixture to be a plain string, not a tzinfo object) as
the single source of truth for both calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import homeassistant.util.dt as dt_util
from astral import Observer
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .astro import (
    DARKNESS_TIERS,
    EphemerisError,
    get_darkness_window,
    moon_altitude,
    moon_illumination_percent,
)
from .client import OpenMeteoClient, OpenMeteoError
from .score import (
    FalloffSpans,
    HourlyConditions,
    PlateauEdges,
    ScoreBreakdown,
    ScoreWeights,
    calculate_score_breakdown,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_UPDATE_INTERVAL = timedelta(minutes=30)


@dataclass
class HourlyScore:
    """One hour's raw conditions plus its computed score breakdown --
    what the coordinator ultimately hands to entities."""

    time: datetime
    conditions: HourlyConditions
    breakdown: ScoreBreakdown


def determine_night_of(now: datetime) -> date:
    """Decide which night's darkness window applies right now.

    Dusk/dawn at Inverness's latitude always land well clear of local
    noon (confirmed empirically in this project: dusk falls between
    18:00 and 00:45, dawn always before ~06:30), so noon is a safe
    cutover: before noon, we're still in the tail end of last night's
    window; after noon, we're looking ahead to tonight's.

    Pulled out as a standalone function (not a method) so it can be
    unit-tested with plain datetimes, no hass/coordinator needed.
    """
    if now.hour < 12:
        return (now - timedelta(days=1)).date()
    return now.date()


class StargazingCoordinator(DataUpdateCoordinator[list[HourlyScore]]):
    """Fetches weather + astronomical data and produces per-hour scores
    for tonight's (or this early morning's) darkness window."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: OpenMeteoClient,
        observer: Observer,
        edges: PlateauEdges,
        spans: FalloffSpans,
        weights: ScoreWeights,
        tiers: tuple = DARKNESS_TIERS,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        super().__init__(hass, _LOGGER, name="stargazing", update_interval=update_interval)
        self._client = client
        self._observer = observer
        self._edges = edges
        self._spans = spans
        self._weights = weights
        self._tiers = tiers

    async def _async_update_data(self) -> list[HourlyScore]:
        timezone_str = str(self.hass.config.time_zone)
        now = dt_util.now()
        night_of = determine_night_of(now)

        window = get_darkness_window(
            self._observer, night_of, tzinfo=timezone_str, tiers=self._tiers
        )
        if window is None:
            _LOGGER.warning(
                "No darkness window found for %s (tiers=%s) -- sun likely "
                "never reaches even the shallowest configured twilight "
                "depth at this latitude/date",
                night_of,
                self._tiers,
            )
            return []

        try:
            readings = await self._client.async_get_hourly_forecast(
                latitude=self._observer.latitude,
                longitude=self._observer.longitude,
                timezone=timezone_str,
            )
        except OpenMeteoError as err:
            raise UpdateFailed(f"Failed to fetch Open-Meteo forecast: {err}") from err

        # See module docstring: window bounds are tz-aware, reading times
        # are naive. Strip to naive local time for comparison.
        window_start_naive = window.start.replace(tzinfo=None)
        window_end_naive = window.end.replace(tzinfo=None)

        hourly_scores: list[HourlyScore] = []
        for reading in readings:
            if not (window_start_naive <= reading.time < window_end_naive):
                continue

            # moon_altitude/moon_illumination_percent need a tz-aware
            # datetime (skyfield requires it) -- reattach the same
            # tzinfo astral used for the window boundaries.
            reading_time_aware = reading.time.replace(tzinfo=window.start.tzinfo)

            try:
                altitude = moon_altitude(self._observer, reading_time_aware)
                illumination = moon_illumination_percent(self._observer, reading_time_aware)
            except EphemerisError as err:
                raise UpdateFailed(f"Failed to compute moon position: {err}") from err

            conditions = HourlyConditions(
                low_cloud_cover=reading.low_cloud_cover,
                mid_cloud_cover=reading.mid_cloud_cover,
                high_cloud_cover=reading.high_cloud_cover,
                temperature=reading.temperature,
                dew_point=reading.dew_point,
                visibility=reading.visibility,
                jet_stream_wind_speed=reading.jet_stream_wind_speed,
                moon_illumination=illumination,
                moon_altitude=altitude,
                precipitation_probability=reading.precipitation_probability,
                wind_speed=reading.wind_speed,
            )
            breakdown = calculate_score_breakdown(
                conditions, self._edges, self._spans, self._weights
            )
            hourly_scores.append(
                HourlyScore(time=reading.time, conditions=conditions, breakdown=breakdown)
            )

        return hourly_scores
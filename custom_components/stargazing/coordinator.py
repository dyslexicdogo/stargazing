"""DataUpdateCoordinator for stargazing.

Thin orchestration glue only, per PROJECT_PRINCIPLES.md: fetch weather,
determine darkness windows for the next few nights, filter to hours
inside each, attach moon position, score each hour. No business logic
lives here -- that's client.py (API), astro.py (windows/moon), and
score.py (scoring).

MULTI-NIGHT: computes NUM_NIGHTS_AHEAD (3) consecutive nights' worth of
scores in one poll, not just tonight's -- it backs the 3-night forecast
card (peak score per night plus per-hour breakdowns, mirroring
sun_bathing's pattern). A single Open-Meteo call covers all 3 nights --
FORECAST_DAYS is set generously enough to include the last night's
early-morning dawn spillover into the following calendar day.

Nights are independent: if one night has no darkness window at all
(e.g. summer solstice with strict tiers configured), that night's entry
just has an empty hourly_scores list and peak_score of None -- it does
NOT abort scoring for the other nights, since they may well have valid
windows even when one doesn't (confirmed empirically: nautical twilight
disappears for roughly a week around the solstice at Inverness's
latitude, not the whole summer).

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
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .astro import (
    DARKNESS_TIERS,
    DarknessWindow,
    EphemerisError,
    get_darkness_window,
    moon_position,
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

# How many consecutive nights to score per poll -- backs the 3-day
# overview card (tonight / tomorrow night / night after).
NUM_NIGHTS_AHEAD = 3

# Covers NUM_NIGHTS_AHEAD full nights including the last night's dawn,
# which can spill into the following calendar day. With NUM_NIGHTS_AHEAD
# = 3 and "tonight" potentially already being "yesterday" (per
# determine_night_of()'s before-noon case), the furthest possible dawn
# is up to 4 calendar days out from the API call -- 4 gives safe margin.
FORECAST_DAYS = 4


@dataclass
class HourlyScore:
    """One hour's raw conditions plus its computed score breakdown."""

    time: datetime
    conditions: HourlyConditions
    breakdown: ScoreBreakdown


@dataclass
class NightlyScore:
    """One night's full scoring result: its darkness window (if any),
    every scored hour within it, and a peak-score summary for quick
    display (e.g. one row in a 3-day overview card).

    window and hourly_scores are both None/empty (not an error) when no
    darkness window exists for this night under the configured tiers --
    see module docstring.
    """

    night_of: date
    window: DarknessWindow | None
    hourly_scores: list[HourlyScore]

    @property
    def peak_score(self) -> float | None:
        if not self.hourly_scores:
            return None
        return max(hs.breakdown.total for hs in self.hourly_scores)


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


class StargazingCoordinator(DataUpdateCoordinator[list[NightlyScore]]):
    """Fetches weather + astronomical data and produces per-hour scores
    for the next NUM_NIGHTS_AHEAD nights' darkness windows."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        client: OpenMeteoClient,
        observer: Observer,
        edges: PlateauEdges,
        spans: FalloffSpans,
        weights: ScoreWeights,
        tiers: tuple = DARKNESS_TIERS,
        update_interval: timedelta = DEFAULT_UPDATE_INTERVAL,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name="stargazing",
            update_interval=update_interval,
        )
        self._client = client
        self._observer = observer
        self._edges = edges
        self._spans = spans
        self._weights = weights
        self._tiers = tiers

    async def _async_update_data(self) -> list[NightlyScore]:
        timezone_str = str(self.hass.config.time_zone)
        now = dt_util.now()
        first_night = determine_night_of(now)

        try:
            readings = await self._client.async_get_hourly_forecast(
                latitude=self._observer.latitude,
                longitude=self._observer.longitude,
                forecast_days=FORECAST_DAYS,
                timezone=timezone_str,
            )
        except OpenMeteoError as err:
            raise UpdateFailed(f"Failed to fetch Open-Meteo forecast: {err}") from err

        nightly_scores: list[NightlyScore] = []
        for offset in range(NUM_NIGHTS_AHEAD):
            night_of = first_night + timedelta(days=offset)
            nightly_scores.append(
                self._score_one_night(night_of, readings, timezone_str)
            )

        return nightly_scores

    def _score_one_night(
        self, night_of: date, readings: list, timezone_str: str
    ) -> NightlyScore:
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
            return NightlyScore(night_of=night_of, window=None, hourly_scores=[])

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
                position = moon_position(self._observer, reading_time_aware)
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
                moon_illumination=position.illumination_percent,
                moon_altitude=position.altitude,
                precipitation_probability=reading.precipitation_probability,
                wind_speed=reading.wind_speed,
            )
            breakdown = calculate_score_breakdown(
                conditions, self._edges, self._spans, self._weights
            )
            hourly_scores.append(
                HourlyScore(time=reading.time, conditions=conditions, breakdown=breakdown)
            )

        return NightlyScore(night_of=night_of, window=window, hourly_scores=hourly_scores)
"""Tests for coordinator.py.

Uses the real hass fixture (confirmed working via test_smoke.py) plus a
hand-built fake client rather than aioresponses -- per
PROJECT_PRINCIPLES.md's stated preference for "minimal hand-built fakes,
not full mocks, not real network" at the coordinator level. HTTP-level
testing already lives in test_client.py; these tests are about
orchestration (filtering, wiring, error propagation), not HTTP.

Time is frozen with pytest_freezer so determine_night_of()'s noon
cutover is deterministic regardless of when the suite actually runs.
"""

import datetime

import pytest
from astral import Depression, Observer

from custom_components.stargazing.astro import DARKNESS_TIERS
from custom_components.stargazing.client import OpenMeteoError, OpenMeteoHourlyReading
from custom_components.stargazing.coordinator import (
    StargazingCoordinator,
    determine_night_of,
)
from custom_components.stargazing.score import FalloffSpans, PlateauEdges, ScoreWeights
from homeassistant.helpers.update_coordinator import UpdateFailed

INVERNESS = Observer(latitude=57.4778, longitude=-4.2247, elevation=0)


class FakeOpenMeteoClient:
    """Hand-built fake, not a mock -- returns canned readings and
    records the call it received for assertions."""

    def __init__(self, readings: list[OpenMeteoHourlyReading]) -> None:
        self._readings = readings
        self.last_call_kwargs: dict | None = None

    async def async_get_hourly_forecast(
        self, latitude, longitude, forecast_days=2, timezone="auto"
    ):
        self.last_call_kwargs = dict(
            latitude=latitude,
            longitude=longitude,
            forecast_days=forecast_days,
            timezone=timezone,
        )
        return self._readings


class FailingOpenMeteoClient:
    async def async_get_hourly_forecast(self, *args, **kwargs):
        raise OpenMeteoError("simulated API failure")


def make_reading(hour_str: str, **overrides) -> OpenMeteoHourlyReading:
    """A reading with sensible defaults, only the timestamp usually
    needs to vary between test readings."""
    defaults = dict(
        time=datetime.datetime.fromisoformat(hour_str),
        low_cloud_cover=10.0,
        mid_cloud_cover=15.0,
        high_cloud_cover=20.0,
        temperature=5.0,
        dew_point=1.0,
        visibility=20000.0,
        jet_stream_wind_speed=15.0,
        precipitation_probability=5.0,
        wind_speed=8.0,
    )
    defaults.update(overrides)
    return OpenMeteoHourlyReading(**defaults)


# Real astronomical night for Inverness on 2026-01-15/16, confirmed
# empirically earlier in this project: dusk ~18:29, dawn ~06:22.
WINTER_READINGS = [
    make_reading("2026-01-15T17:00"),  # before dusk -- excluded
    make_reading("2026-01-15T19:00"),  # inside window
    make_reading("2026-01-15T23:00"),  # inside window
    make_reading("2026-01-16T02:00"),  # inside window
    make_reading("2026-01-16T06:00"),  # inside window (before ~06:22 dawn)
    make_reading("2026-01-16T07:00"),  # after dawn -- excluded
]


def make_coordinator(hass, client, tiers=DARKNESS_TIERS):
    return StargazingCoordinator(
        hass=hass,
        client=client,
        observer=INVERNESS,
        edges=PlateauEdges(),
        spans=FalloffSpans(),
        weights=ScoreWeights(),
        tiers=tiers,
    )


@pytest.fixture(autouse=True)
def stub_moon_functions(monkeypatch):
    """Coordinator tests verify orchestration (filtering/wiring), not
    astronomical correctness -- that's already covered by test_astro.py.
    Stubbing these avoids a real skyfield/de421.bsp network dependency
    here (pytest-homeassistant-custom-component blocks real sockets by
    default anyway) and keeps these tests fast and focused."""
    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_altitude",
        lambda observer, at: 12.5,
    )
    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_illumination_percent",
        lambda observer, at: 40.0,
    )


# ---------------------------------------------------------------------------
# determine_night_of -- pure function, no hass needed
# ---------------------------------------------------------------------------
class TestDetermineNightOf:
    def test_before_noon_is_still_last_nights_window(self):
        now = datetime.datetime(2026, 1, 16, 3, 0)
        assert determine_night_of(now) == datetime.date(2026, 1, 15)

    def test_at_or_after_noon_is_tonights_window(self):
        now = datetime.datetime(2026, 1, 15, 12, 0)
        assert determine_night_of(now) == datetime.date(2026, 1, 15)

    def test_evening_is_tonights_window(self):
        now = datetime.datetime(2026, 1, 15, 20, 0)
        assert determine_night_of(now) == datetime.date(2026, 1, 15)

    def test_just_before_noon_is_still_last_night(self):
        now = datetime.datetime(2026, 1, 15, 11, 59)
        assert determine_night_of(now) == datetime.date(2026, 1, 14)


# ---------------------------------------------------------------------------
# StargazingCoordinator._async_update_data
# ---------------------------------------------------------------------------
class TestCoordinatorUpdateData:
    async def test_filters_to_only_hours_inside_the_darkness_window(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(WINTER_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert len(result) == 4
        included_hours = {hs.time.isoformat() for hs in result}
        assert included_hours == {
            "2026-01-15T19:00:00",
            "2026-01-15T23:00:00",
            "2026-01-16T02:00:00",
            "2026-01-16T06:00:00",
        }

    async def test_passes_correct_location_and_timezone_to_client(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(WINTER_READINGS)
        coordinator = make_coordinator(hass, client)

        await coordinator._async_update_data()

        assert client.last_call_kwargs["latitude"] == INVERNESS.latitude
        assert client.last_call_kwargs["longitude"] == INVERNESS.longitude
        assert client.last_call_kwargs["timezone"] == "Europe/London"

    async def test_each_hourly_score_has_a_moon_altitude_and_illumination(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(WINTER_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert len(result) > 0
        for hourly_score in result:
            # values come from the stub_moon_functions fixture -- this
            # test is about wiring (does the value land in the right
            # field?), not astronomical correctness
            assert hourly_score.conditions.moon_altitude == 12.5
            assert hourly_score.conditions.moon_illumination == 40.0

    async def test_each_hourly_score_has_a_computed_breakdown(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(WINTER_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        for hourly_score in result:
            assert 0.0 <= hourly_score.breakdown.total <= 100.0

    async def test_no_darkness_window_returns_empty_list(self, hass, freezer):
        # summer solstice, restricted to tiers that don't reach civil --
        # confirmed empirically that neither is achievable at this
        # latitude/date
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-06-21 20:00:00")

        client = FakeOpenMeteoClient(WINTER_READINGS)
        coordinator = make_coordinator(
            hass, client, tiers=(Depression.ASTRONOMICAL, Depression.NAUTICAL)
        )

        result = await coordinator._async_update_data()

        assert result == []

    async def test_client_error_raises_update_failed(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        coordinator = make_coordinator(hass, FailingOpenMeteoClient())

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_readings_returns_empty_list_not_error(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient([])
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert result == []
"""Tests for coordinator.py.

Uses the real hass fixture (confirmed working via test_smoke.py) plus a
hand-built fake client rather than aioresponses -- per
PROJECT_PRINCIPLES.md's stated preference for "minimal hand-built fakes,
not full mocks, not real network" at the coordinator level. HTTP-level
testing already lives in test_client.py; these tests are about
orchestration (filtering, wiring, multi-night structure, error
propagation), not HTTP.

Time is frozen with pytest_freezer so determine_night_of()'s noon
cutover is deterministic regardless of when the suite actually runs.
"""

import datetime

import pytest
from astral import Depression, Observer
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stargazing.astro import DARKNESS_TIERS
from custom_components.stargazing.client import OpenMeteoError, OpenMeteoHourlyReading
from custom_components.stargazing.const import DOMAIN
from custom_components.stargazing.coordinator import (
    FORECAST_DAYS,
    NUM_NIGHTS_AHEAD,
    HourlyScore,
    NightlyScore,
    StargazingCoordinator,
    current_hourly_score,
    determine_night_of,
)
from custom_components.stargazing.score import (
    FalloffSpans,
    HourlyConditions,
    PlateauEdges,
    ScoreBreakdown,
    ScoreWeights,
)
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


# Night 0 (Jan15->16) uses exact, empirically-confirmed boundary times
# from earlier in this project: astronomical dusk ~18:29, dawn ~06:22.
# Nights 1 and 2 use conservative mid-window hours (not boundary-precise
# -- winter dusk/dawn shift only a few minutes/day at this latitude in
# mid-January, so these are safely inside any reasonable window without
# needing a fresh astral check for each exact date).
MULTI_NIGHT_READINGS = [
    # --- Night 0: 2026-01-15 -> 2026-01-16 ---
    make_reading("2026-01-15T17:00"),  # before dusk -- excluded
    make_reading("2026-01-15T19:00"),  # inside window
    make_reading("2026-01-15T23:00"),  # inside window
    make_reading("2026-01-16T02:00"),  # inside window
    make_reading("2026-01-16T06:00"),  # inside window (before ~06:22 dawn)
    make_reading("2026-01-16T07:00"),  # after dawn -- excluded
    # --- Night 1: 2026-01-16 -> 2026-01-17 ---
    make_reading("2026-01-16T20:00"),  # inside window
    make_reading("2026-01-17T01:00"),  # inside window
    # --- Night 2: 2026-01-17 -> 2026-01-18 ---
    make_reading("2026-01-17T21:00"),  # inside window
    make_reading("2026-01-18T03:00"),  # inside window
]


def make_coordinator(hass, client, tiers=DARKNESS_TIERS):
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    return StargazingCoordinator(
        hass=hass,
        config_entry=entry,
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
    Stubbing this avoids a real skyfield/de421.bsp dependency here
    (pytest-homeassistant-custom-component blocks real sockets by
    default anyway) and keeps these tests fast and focused."""
    from custom_components.stargazing.astro import MoonPosition

    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_position",
        lambda observer, at: MoonPosition(altitude=12.5, illumination_percent=40.0),
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


def make_hourly_score(when: datetime.datetime, total: float) -> HourlyScore:
    """A fully-populated HourlyScore. current_hourly_score() only reads
    `.time`, but building the real dataclass keeps these fakes honest and
    lets assertions read `.breakdown.total` too."""
    return HourlyScore(
        time=when,
        conditions=HourlyConditions(
            low_cloud_cover=0.0,
            mid_cloud_cover=0.0,
            high_cloud_cover=0.0,
            temperature=10.0,
            dew_point=5.0,
            visibility=30000.0,
            jet_stream_wind_speed=10.0,
            moon_illumination=0.0,
            moon_altitude=-10.0,
            precipitation_probability=0.0,
            wind_speed=5.0,
        ),
        breakdown=ScoreBreakdown(
            low_cloud=100.0,
            mid_cloud=100.0,
            high_cloud=100.0,
            dew_point_spread=100.0,
            visibility=100.0,
            jet_stream_wind=100.0,
            moon_illumination=100.0,
            precipitation_probability=100.0,
            wind_speed=100.0,
            total=total,
        ),
    )


def make_night(night_of: datetime.date, hours: list[HourlyScore]) -> NightlyScore:
    return NightlyScore(night_of=night_of, window=None, hourly_scores=hours)


class TestCurrentHourlyScore:
    def test_returns_hour_containing_now(self):
        now = datetime.datetime(2026, 1, 15, 20, 30)
        nightly_scores = [
            make_night(
                datetime.date(2026, 1, 15),
                [
                    make_hourly_score(datetime.datetime(2026, 1, 15, 19, 0), 1.0),
                    make_hourly_score(datetime.datetime(2026, 1, 15, 20, 0), 9.0),
                    make_hourly_score(datetime.datetime(2026, 1, 15, 21, 0), 5.0),
                ],
            ),
            make_night(datetime.date(2026, 1, 16), []),
        ]

        result = current_hourly_score(nightly_scores, now)

        assert result is not None
        assert result.time == datetime.datetime(2026, 1, 15, 20, 0)
        assert result.breakdown.total == 9.0

    def test_returns_none_when_no_hour_contains_now(self):
        now = datetime.datetime(2026, 1, 15, 18, 30)
        nightly_scores = [
            make_night(
                datetime.date(2026, 1, 15),
                [make_hourly_score(datetime.datetime(2026, 1, 15, 19, 0), 1.0)],
            ),
        ]

        assert current_hourly_score(nightly_scores, now) is None

    def test_matches_exact_hour_boundary_and_excludes_next_hour(self):
        now = datetime.datetime(2026, 1, 15, 21, 0)
        nightly_scores = [
            make_night(
                datetime.date(2026, 1, 15),
                [
                    make_hourly_score(datetime.datetime(2026, 1, 15, 20, 0), 3.0),
                    make_hourly_score(datetime.datetime(2026, 1, 15, 21, 0), 9.0),
                ],
            ),
        ]

        result = current_hourly_score(nightly_scores, now)

        assert result is not None
        assert result.time == datetime.datetime(2026, 1, 15, 21, 0)


# ---------------------------------------------------------------------------
# StargazingCoordinator._async_update_data -- multi-night structure
# ---------------------------------------------------------------------------
class TestCoordinatorReturnsThreeNights:
    async def test_returns_num_nights_ahead_entries(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert len(result) == NUM_NIGHTS_AHEAD == 3

    async def test_nights_are_consecutive_starting_from_determined_night(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert [n.night_of for n in result] == [
            datetime.date(2026, 1, 15),
            datetime.date(2026, 1, 16),
            datetime.date(2026, 1, 17),
        ]

    async def test_requests_forecast_days_covering_all_nights(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        await coordinator._async_update_data()

        assert client.last_call_kwargs["forecast_days"] == FORECAST_DAYS


# ---------------------------------------------------------------------------
# Per-night filtering and content
# ---------------------------------------------------------------------------
class TestNightlyScoreContent:
    async def test_first_night_filters_to_only_hours_inside_its_window(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()
        night_0 = result[0]

        included_hours = {hs.time.isoformat() for hs in night_0.hourly_scores}
        assert included_hours == {
            "2026-01-15T19:00:00",
            "2026-01-15T23:00:00",
            "2026-01-16T02:00:00",
            "2026-01-16T06:00:00",
        }

    async def test_each_night_has_a_window(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        for night in result:
            assert night.window is not None

    async def test_each_hourly_score_has_moon_altitude_and_illumination(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        for night in result:
            for hourly_score in night.hourly_scores:
                # values come from the stub_moon_functions fixture -- this
                # test is about wiring, not astronomical correctness
                assert hourly_score.conditions.moon_altitude == 12.5
                assert hourly_score.conditions.moon_illumination == 40.0

    async def test_each_hourly_score_has_a_computed_breakdown(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        for night in result:
            for hourly_score in night.hourly_scores:
                assert 0.0 <= hourly_score.breakdown.total <= 100.0


# ---------------------------------------------------------------------------
# NightlyScore.peak_score
# ---------------------------------------------------------------------------
class TestPeakScore:
    async def test_peak_score_matches_max_of_hourly_totals(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()
        night_0 = result[0]

        expected_max = max(hs.breakdown.total for hs in night_0.hourly_scores)
        assert night_0.peak_score == expected_max

    async def test_peak_score_is_none_when_no_hourly_scores(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-06-21 20:00:00")

        client = FakeOpenMeteoClient([])
        coordinator = make_coordinator(
            hass, client, tiers=(Depression.ASTRONOMICAL, Depression.NAUTICAL)
        )

        result = await coordinator._async_update_data()

        assert all(n.peak_score is None for n in result)


# ---------------------------------------------------------------------------
# Graceful degradation: no darkness window for some/all nights
# ---------------------------------------------------------------------------
class TestNoWindowGracefulDegradation:
    async def test_night_with_no_window_has_empty_scores_not_an_error(
        self, hass, freezer
    ):
        # confirmed empirically: nautical twilight is unreachable at
        # Inverness's latitude for a stretch of at least a week around
        # the solstice (June 18-24 all confirmed missing)
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-06-20 20:00:00")

        client = FakeOpenMeteoClient([])
        coordinator = make_coordinator(
            hass, client, tiers=(Depression.ASTRONOMICAL, Depression.NAUTICAL)
        )

        result = await coordinator._async_update_data()

        assert len(result) == NUM_NIGHTS_AHEAD
        for night in result:
            assert night.window is None
            assert night.hourly_scores == []

    async def test_one_bad_night_does_not_abort_other_nights(self, hass, freezer):
        # night 0 (June 20) has no nautical window; nights 1-2 (May
        # dates would, but we can't shift dates independently here since
        # nights are always consecutive) -- this test instead confirms
        # the coordinator doesn't raise/short-circuit when night 0 fails,
        # by checking all 3 entries are still present and well-formed
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-06-20 20:00:00")

        client = FakeOpenMeteoClient([])
        coordinator = make_coordinator(
            hass, client, tiers=(Depression.ASTRONOMICAL, Depression.NAUTICAL)
        )

        result = await coordinator._async_update_data()

        assert len(result) == 3
        assert [n.night_of for n in result] == [
            datetime.date(2026, 6, 20),
            datetime.date(2026, 6, 21),
            datetime.date(2026, 6, 22),
        ]


# ---------------------------------------------------------------------------
# Client call parameters and error handling
# ---------------------------------------------------------------------------
class TestClientInteraction:
    async def test_passes_correct_location_and_timezone_to_client(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient(MULTI_NIGHT_READINGS)
        coordinator = make_coordinator(hass, client)

        await coordinator._async_update_data()

        assert client.last_call_kwargs["latitude"] == INVERNESS.latitude
        assert client.last_call_kwargs["longitude"] == INVERNESS.longitude
        assert client.last_call_kwargs["timezone"] == "Europe/London"

    async def test_client_error_raises_update_failed(self, hass, freezer):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        coordinator = make_coordinator(hass, FailingOpenMeteoClient())

        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_empty_readings_still_returns_three_nights_with_empty_scores(
        self, hass, freezer
    ):
        hass.config.time_zone = "Europe/London"
        freezer.move_to("2026-01-15 20:00:00")

        client = FakeOpenMeteoClient([])
        coordinator = make_coordinator(hass, client)

        result = await coordinator._async_update_data()

        assert len(result) == NUM_NIGHTS_AHEAD
        for night in result:
            # windows exist (winter dates), just no readings fell inside them
            assert night.window is not None
            assert night.hourly_scores == []
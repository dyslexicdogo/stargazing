"""Tests for astro.py.

No HA imports. The darkness-window tests below run offline against real
astral computations for Inverness's coordinates. The moon_altitude/
moon_illumination_percent tests need skyfield's de421.bsp ephemeris file,
which downloads from NASA on first use and is cached locally after --
these tests need real network access at least once and were NOT run
successfully in the sandbox this file was written in (NASA's ephemeris
server isn't network-reachable there). Run these locally to confirm
before trusting them; the darkness-window tests were verified.
"""

import datetime

import pytest
from astral import Depression, Observer

from custom_components.stargazing.astro import (
    DARKNESS_TIERS,
    get_darkness_window,
    moon_altitude,
    moon_illumination_percent,
)

INVERNESS = Observer(latitude=57.4778, longitude=-4.2247, elevation=0)


# ---------------------------------------------------------------------------
# get_darkness_window: tier fallback behaviour
# ---------------------------------------------------------------------------
class TestGetDarknessWindow:
    def test_winter_night_achieves_astronomical_tier(self):
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 1, 15), tzinfo="Europe/London"
        )
        assert window is not None
        assert window.tier == Depression.ASTRONOMICAL
        assert window.start < window.end

    def test_summer_solstice_falls_back_to_civil(self):
        # confirmed empirically: astronomical and nautical are both
        # unreachable at Inverness's latitude on the solstice
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 6, 21), tzinfo="Europe/London"
        )
        assert window is not None
        assert window.tier == Depression.CIVIL

    def test_mid_august_achieves_astronomical_but_narrow(self):
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 8, 15), tzinfo="Europe/London"
        )
        assert window is not None
        assert window.tier == Depression.ASTRONOMICAL
        duration = window.end - window.start
        # confirmed ~1h13m in the empirical check after fixing the
        # dusk/dawn pairing bug -- must stay comfortably under a normal
        # night's length, not just under 24h
        assert duration < datetime.timedelta(hours=3)

    def test_short_night_does_not_pair_dusk_with_wrong_nights_dawn(self):
        # regression test: astral's dusk(D)/dawn(D+1) date-labeling breaks
        # down right at the point where nights collapse to a few hours.
        # Naively pairing dusk(night_of) with dawn(night_of + 1) here
        # previously produced a bogus ~25 hour "window" by matching dusk
        # from one night with dawn from the following night.
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 8, 15), tzinfo="Europe/London"
        )
        assert window is not None
        duration = window.end - window.start
        assert duration < datetime.timedelta(hours=6)
        assert duration > datetime.timedelta(minutes=0)

    def test_window_end_is_after_start_even_across_midnight(self):
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 1, 15), tzinfo="Europe/London"
        )
        assert window.end > window.start
        # dawn should land on the calendar day after night_of
        assert window.end.date() == datetime.date(2026, 1, 16)

    def test_sun_azimuth_is_captured_at_both_boundaries(self):
        window = get_darkness_window(
            INVERNESS, datetime.date(2026, 1, 15), tzinfo="Europe/London"
        )
        assert 0.0 <= window.start_sun_azimuth <= 360.0
        assert 0.0 <= window.end_sun_azimuth <= 360.0

    def test_returns_none_when_not_even_civil_twilight_occurs(self):
        # a genuine polar-day observer, far enough north that the sun
        # never dips 6 degrees below the horizon around the solstice
        svalbard = Observer(latitude=78.9, longitude=11.9, elevation=0)
        window = get_darkness_window(
            svalbard, datetime.date(2026, 6, 21), tzinfo="UTC"
        )
        assert window is None

    def test_custom_tiers_can_skip_civil_fallback(self):
        # if only astronomical/nautical are acceptable, solstice should
        # correctly fail to find anything rather than silently falling
        # back further than requested
        window = get_darkness_window(
            INVERNESS,
            datetime.date(2026, 6, 21),
            tzinfo="Europe/London",
            tiers=(Depression.ASTRONOMICAL, Depression.NAUTICAL),
        )
        assert window is None


# ---------------------------------------------------------------------------
# moon_altitude (skyfield-based)
# ---------------------------------------------------------------------------
class TestMoonAltitude:
    def test_returns_a_plausible_degree_value(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        altitude = moon_altitude(INVERNESS, at)
        assert -90.0 <= altitude <= 90.0

    def test_altitude_changes_over_the_course_of_a_night(self):
        # the moon moves across the sky -- altitude at 22:00 and 04:00
        # on the same night should generally differ
        early = moon_altitude(
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        )
        late = moon_altitude(
            INVERNESS, datetime.datetime(2026, 1, 16, 4, 0, tzinfo=datetime.UTC)
        )
        assert early != late

    def test_naive_datetime_is_treated_as_utc(self):
        # _ensure_utc() should make this equivalent to an explicit UTC dt
        naive = datetime.datetime(2026, 1, 15, 22, 0)
        aware = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        assert moon_altitude(INVERNESS, naive) == pytest.approx(
            moon_altitude(INVERNESS, aware)
        )


# ---------------------------------------------------------------------------
# moon_illumination_percent (skyfield-based; needs Observer, not just a date,
# since fraction_illuminated() comes from the actual topocentric position)
# ---------------------------------------------------------------------------
class TestMoonIlluminationPercent:
    def test_returns_a_percentage_in_range(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        pct = moon_illumination_percent(INVERNESS, at)
        assert 0.0 <= pct <= 100.0

    def test_roughly_stable_within_theoretical_bounds(self):
        # illumination's rate of change isn't constant across the lunar
        # cycle -- it's slowest at new/full moon (flat part of the
        # 1-cos curve) and fastest near the quarters (steepest part).
        # Theoretical maximum: (2*pi/29.53059 days) * 0.5 * 100 ~= 0.443
        # pct/hour, so ~2.66 points over 6 hours at worst. A tighter
        # fixed tolerance (e.g. 1.0) only holds near new/full and fails
        # for dates that land nearer a quarter -- this bound covers the
        # whole cycle with some margin for orbital eccentricity.
        early = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        )
        late = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 16, 4, 0, tzinfo=datetime.UTC)
        )
        assert early == pytest.approx(late, abs=3.5)

    def test_illumination_progresses_over_two_weeks(self):
        # not asserting exact real-world phase dates (that needs an
        # external almanac to verify against, which this environment
        # can't reach) -- just confirming the value actually moves
        # rather than being stuck at a constant
        day1 = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 1, 22, 0, tzinfo=datetime.UTC)
        )
        day15 = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.UTC)
        )
        assert day1 != pytest.approx(day15, abs=5.0)
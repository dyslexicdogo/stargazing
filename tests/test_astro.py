"""Tests for astro.py.

No HA imports, no network. The darkness-window tests run offline against
real astral computations for Inverness's coordinates. The
moon_altitude/moon_illumination_percent/moon_position tests use
skyfield's bundled de421.bsp ephemeris (custom_components/stargazing/
de421.bsp, loaded via load_file()) -- no network access at all, since
load_file() only ever reads a local path. These need the bundled file to
actually be present to pass; they were NOT run successfully in the
sandbox this file was written in (no bundled file exists there). Run
these locally to confirm; the darkness-window tests were verified.
"""

import datetime

import pytest
from astral import Depression, Observer

from custom_components.stargazing.astro import (
    DARKNESS_TIERS,
    EphemerisError,
    get_darkness_window,
    moon_altitude,
    moon_illumination_percent,
    moon_position,
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
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        altitude = moon_altitude(INVERNESS, at)
        assert -90.0 <= altitude <= 90.0

    def test_altitude_changes_over_the_course_of_a_night(self):
        # the moon moves across the sky -- altitude at 22:00 and 04:00
        # on the same night should generally differ
        early = moon_altitude(
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        )
        late = moon_altitude(
            INVERNESS, datetime.datetime(2026, 1, 16, 4, 0, tzinfo=datetime.timezone.utc)
        )
        assert early != late

    def test_naive_datetime_is_treated_as_utc(self):
        # _ensure_utc() should make this equivalent to an explicit UTC dt
        naive = datetime.datetime(2026, 1, 15, 22, 0)
        aware = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        assert moon_altitude(INVERNESS, naive) == pytest.approx(
            moon_altitude(INVERNESS, aware)
        )


# ---------------------------------------------------------------------------
# moon_illumination_percent (skyfield-based; needs Observer, not just a date,
# since fraction_illuminated() comes from the actual topocentric position)
# ---------------------------------------------------------------------------
class TestMoonIlluminationPercent:
    def test_returns_a_percentage_in_range(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
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
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        )
        late = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 16, 4, 0, tzinfo=datetime.timezone.utc)
        )
        assert early == pytest.approx(late, abs=3.5)

    def test_illumination_progresses_over_two_weeks(self):
        # not asserting exact real-world phase dates (that needs an
        # external almanac to verify against, which this environment
        # can't reach) -- just confirming the value actually moves
        # rather than being stuck at a constant
        day1 = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 1, 22, 0, tzinfo=datetime.timezone.utc)
        )
        day15 = moon_illumination_percent(
            INVERNESS, datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        )
        assert day1 != pytest.approx(day15, abs=5.0)


# ---------------------------------------------------------------------------
# moon_position -- combined altitude + illumination from one observation.
# moon_altitude()/moon_illumination_percent() are thin wrappers around
# this; these tests confirm the combined result matches what calling
# them separately would give, i.e. the refactor didn't change behavior.
# ---------------------------------------------------------------------------
class TestMoonPosition:
    def test_altitude_matches_standalone_function(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        position = moon_position(INVERNESS, at)
        assert position.altitude == moon_altitude(INVERNESS, at)

    def test_illumination_matches_standalone_function(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        position = moon_position(INVERNESS, at)
        assert position.illumination_percent == moon_illumination_percent(INVERNESS, at)

    def test_both_values_in_plausible_ranges(self):
        at = datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc)
        position = moon_position(INVERNESS, at)
        assert -90.0 <= position.altitude <= 90.0
        assert 0.0 <= position.illumination_percent <= 100.0


# ---------------------------------------------------------------------------
# EphemerisError -- load failures surface a clean error, not a raw
# OSError/ValueError, and the message names the bundled file.
# ---------------------------------------------------------------------------
class TestEphemerisErrors:
    def _reset_cache(self, monkeypatch, astro_module):
        # moon tests populate the module-level ephemeris cache; force a
        # reload path so load_file is actually reached again
        for name in ("_timescale", "_earth", "_moon_body", "_sun_body", "_ephemeris_mtime"):
            monkeypatch.setattr(astro_module, name, None)

    def test_load_failure_raises_ephemeris_error_with_path(self, monkeypatch):
        import custom_components.stargazing.astro as astro_module

        self._reset_cache(monkeypatch, astro_module)

        def boom(*args, **kwargs):
            raise ValueError("corrupt ephemeris data")

        monkeypatch.setattr(astro_module, "load_file", boom)

        with pytest.raises(EphemerisError, match="de421.bsp"):
            astro_module.moon_position(
                INVERNESS,
                datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc),
            )

    def test_missing_file_raises_ephemeris_error_with_path(self, monkeypatch):
        import custom_components.stargazing.astro as astro_module
        from pathlib import Path

        self._reset_cache(monkeypatch, astro_module)
        monkeypatch.setattr(astro_module, "_EPHEMERIS_PATH", Path("/nonexistent/de421.bsp"))

        with pytest.raises(EphemerisError, match="de421.bsp"):
            astro_module.moon_position(
                INVERNESS,
                datetime.datetime(2026, 1, 15, 22, 0, tzinfo=datetime.timezone.utc),
            )
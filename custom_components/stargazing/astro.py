"""Astronomical window calculation: darkness boundaries + moon position.

Deliberately has ZERO Home Assistant imports so it can be unit-tested in
isolation, same as score.py and client.py (see PROJECT_PRINCIPLES.md).

Sun/twilight boundaries use `astral` directly (stable across versions --
astral.sun.dusk/dawn and Depression have existed since old releases,
confirmed against a 2.2 install). Moon altitude and illumination use
`skyfield` instead of astral.moon: astral.moon only exposes phase() in
older releases (confirmed empirically -- astral 2.2's moon.py has no
zenith/elevation/azimuth at all, only phase() was added there; those
functions were added in a later astral release, but HA core's actual
pinned version can't be assumed to have them without checking the real
installed version -- see PROJECT_PRINCIPLES.md "verify before coding").
skyfield gives proper ephemeris-based altitude and illumination from a
single consistent source, avoiding a two-library inconsistency.

DARKNESS TIER FALLBACK: at Inverness's latitude, true astronomical
darkness (-18 degrees) doesn't occur at all for roughly a month around
the summer solstice, and even nautical twilight (-12 degrees) disappears
for part of that stretch too -- confirmed empirically against the real
coordinates, not assumed. So get_darkness_window() tries astronomical
first, falls back to nautical, then to civil, and records which tier was
actually achieved that night rather than silently returning nothing or
silently using a shallower tier than necessary. Which tiers are tried
(and in what order) is caller-configurable via the `tiers` parameter --
intended to back a user-facing "minimum acceptable darkness" config
option (Phase 6), not hardcoded here.

MOON: skyfield needs an ephemeris file (de421.bsp, ~17MB). Rather than
downloading it on first use, the file is bundled directly in this
integration's directory (custom_components/stargazing/de421.bsp) and
loaded via load_file(), which only ever reads a local path -- it has no
download capability at all, unlike load()/Loader. This is a genuinely
stronger guarantee than caching a downloaded copy: there's no code path
here that can ever attempt network access, not even as a fallback.
Valid through 2053.

Bundling means this file must be committed inside
custom_components/stargazing/, not just the repo root -- HACS
"Integration" category repos only download custom_components/ (see
PROJECT_PRINCIPLES.md gotcha list), so a copy sitting only at the repo
root would silently not exist for real HACS installs.

EphemerisError wraps load failures (missing/corrupted file) the same
way client.py wraps HTTP failures in OpenMeteoError, so the coordinator
can catch it and surface a clean UpdateFailed rather than a raw
FileNotFoundError.

The ephemeris is loaded LAZILY (on first call to a moon function), not
at module import time. Loading it eagerly at import time was tried
first and was a real bug: it meant simply importing this module -- even
just to use the sun-only darkness window functions -- required loading
a 17MB file just to import, which slowed down and complicated test
collection for the whole file, not just the moon-related tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from pathlib import Path

import astral.sun as astral_sun
from astral import Depression, Observer
from skyfield.api import load, load_file, wgs84

_EPHEMERIS_PATH = Path(__file__).parent / "de421.bsp"
_ephemeris_mtime = None  # track file mtime


# Populated on first call to _get_ephemeris(), not at import time -- see
# module docstring. Cached after that so the (slow) load only happens
# once per process.
_timescale = None
_earth = None
_moon_body = None
_sun_body = None


class EphemerisError(Exception):
    """Raised when the moon ephemeris can't be loaded (e.g. the bundled
    file is missing or corrupted). Mirrors OpenMeteoError in client.py
    -- coordinator code should catch this the same way and surface it
    as UpdateFailed."""


def _get_ephemeris():
    global _timescale, _earth, _moon_body, _sun_body, _ephemeris_mtime
    current_mtime = _EPHEMERIS_PATH.stat().st_mtime
    if _earth is None or current_mtime != _ephemeris_mtime:
        # file changed or first load
        try:
            _timescale = load.timescale()
            ephemeris = load_file(_EPHEMERIS_PATH)
        except (OSError, ValueError) as err:
            raise EphemerisError(...)
        _earth = ephemeris["earth"]
        _moon_body = ephemeris["moon"]
        _sun_body = ephemeris["sun"]
        _ephemeris_mtime = current_mtime
    return _timescale, _earth, _moon_body, _sun_body

# Tried in order: best darkness quality first, falls back only when the
# sun genuinely never reaches that depression at this latitude/date.
DARKNESS_TIERS: tuple[Depression, ...] = (
    Depression.ASTRONOMICAL,
    Depression.NAUTICAL,
    Depression.CIVIL,
)


@dataclass
class DarknessWindow:
    """A single night's dark-sky window.

    `tier` records which twilight depth was actually achieved --
    ASTRONOMICAL is genuine dark-sky darkness, NAUTICAL and CIVIL mean
    some residual sky glow was present all night. Useful for future
    diagnostics/attributes even though it isn't fed into score.py today.
    """

    start: datetime  # dusk at `tier`
    end: datetime  # dawn at `tier`, the following calendar morning
    tier: Depression
    start_sun_azimuth: float  # degrees; captured for future directional use
    end_sun_azimuth: float  # degrees; captured for future directional use


def get_darkness_window(
    observer: Observer,
    night_of: date_type,
    tzinfo: str = "UTC",
    tiers: tuple[Depression, ...] = DARKNESS_TIERS,
) -> DarknessWindow | None:
    """Find the darkest available window for the night starting on `night_of`.

    Tries each tier in order (deepest darkness first) and returns the
    first one that's physically reachable at this latitude/date. Returns
    None only if not even civil twilight occurs (polar day) -- a real
    possibility to guard against even though it won't happen at
    Inverness's latitude.
    """
    for tier in tiers:
        try:
            start = astral_sun.dusk(observer, date=night_of, depression=tier, tzinfo=tzinfo)
        except ValueError:
            continue

        # astral's date-labeling for dusk/dawn gets unreliable right at the
        # point where a night collapses to just a few hours (confirmed at
        # Inverness in mid-August): dusk(night_of) and dawn(night_of) can
        # both land on the same calendar date, or dawn can end up on
        # night_of + 1 depending on exact rounding near midnight. Rather
        # than assume one or the other, check both candidate dates and
        # take whichever gives the earliest dawn that's actually after
        # dusk -- that's always the correct pairing regardless of how
        # astral chose to label the date.
        end = None
        for candidate_date in (night_of, night_of + timedelta(days=1)):
            try:
                candidate_end = astral_sun.dawn(
                    observer, date=candidate_date, depression=tier, tzinfo=tzinfo
                )
            except ValueError:
                continue
            if candidate_end > start and (end is None or candidate_end < end):
                end = candidate_end

        if end is None:
            continue

        return DarknessWindow(
            start=start,
            end=end,
            tier=tier,
            start_sun_azimuth=astral_sun.azimuth(observer, start),
            end_sun_azimuth=astral_sun.azimuth(observer, end),
        )

    return None


def _ensure_utc(at: datetime) -> datetime:
    """skyfield's timescale needs a timezone-aware datetime."""
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at.astimezone(timezone.utc)


def _observe_moon(observer: Observer, at: datetime):
    """Shared skyfield observation of the moon from a topocentric location.

    Used by moon_position() to compute altitude and illumination from a
    single call. moon_altitude()/moon_illumination_percent() also each
    call this independently when used alone -- callers needing both
    values for the same (observer, at), which coordinator.py always
    does once per scored hour, should use moon_position() instead to
    avoid computing the same observation twice.
    """
    timescale, earth, moon_body, _sun_body = _get_ephemeris()
    at = _ensure_utc(at)
    t = timescale.from_datetime(at)
    location = earth + wgs84.latlon(
        latitude_degrees=observer.latitude,
        longitude_degrees=observer.longitude,
        elevation_m=observer.elevation,
    )
    return location.at(t).observe(moon_body).apparent()


@dataclass
class MoonPosition:
    """Altitude and illumination from a single shared observation."""

    altitude: float  # degrees above horizon
    illumination_percent: float  # 0-100


def moon_position(observer: Observer, at: datetime) -> MoonPosition:
    """Altitude + illumination together, from one skyfield observation.

    Prefer this over calling moon_altitude() and
    moon_illumination_percent() separately when both are needed for the
    same (observer, at) -- calling both separately computes the same
    underlying topocentric observation twice for no benefit.
    """
    _timescale, _earth, _moon_body, sun_body = _get_ephemeris()
    apparent = _observe_moon(observer, at)
    alt, _az, _distance = apparent.altaz()
    illumination_percent = apparent.fraction_illuminated(sun_body) * 100.0
    return MoonPosition(altitude=alt.degrees, illumination_percent=illumination_percent)


def moon_altitude(observer: Observer, at: datetime) -> float:
    """Moon altitude in degrees above the horizon at a given moment.

    Thin wrapper around moon_position() -- prefer that directly if you
    also need illumination for the same (observer, at), to avoid a
    redundant second observation.
    """
    return moon_position(observer, at).altitude


def moon_illumination_percent(observer: Observer, at: datetime) -> float:
    """Moon illumination as a percentage (0-100), from skyfield's
    ephemeris-based fraction_illuminated() rather than a phase-based
    approximation.

    Thin wrapper around moon_position() -- prefer that directly if you
    also need altitude for the same (observer, at), to avoid a
    redundant second observation.
    """
    return moon_position(observer, at).illumination_percent
"""Tests for sensor.py.

Follows test_init.py's pattern: real hass fixture, aioresponses for the
Open-Meteo HTTP layer, moon functions stubbed to avoid touching the
bundled ephemeris (blocked by pytest-socket anyway). These tests go
through the real hass.config_entries.async_setup() path so
__init__.py's forward-to-sensor wiring is exercised end-to-end, not
just sensor.py in isolation.

ENTITY LOOKUP: entities are resolved by unique_id via the entity
registry (see entity_id()), never by a hardcoded/guessed entity_id
string. sensor.py sets unique_id explicitly -- that's the value under
this test suite's control -- whereas the entity_id HA derives from
has_entity_name + device/entity name is HA's slugification behavior,
not ours to assume. Looking up by unique_id also means these tests
don't silently start passing for the wrong reason if two sensors'
names ever collide after slugification.
"""

import re

import pytest
from aioresponses import aioresponses
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stargazing.astro import MoonPosition
from custom_components.stargazing.client import BASE_URL
from custom_components.stargazing.const import (
    CONF_PRESET,
    CONF_TWILIGHT_TIER,
    DOMAIN,
    PRESET_BALANCED,
    TIER_ASTRONOMICAL,
)
from custom_components.stargazing.presets import get_preset_values

URL_PATTERN = re.compile(rf"^{re.escape(BASE_URL)}")

# Six readings covering all three nights the coordinator scores, so the
# multi-night sensors (not just night 0) are exercised end-to-end:
#   night 0 = Jan 15 (20:00, 21:00)
#   night 1 = Jan 16 (20:00, 23:00)
#   night 2 = Jan 17 (21:00)
VALID_PAYLOAD = {
    "hourly": {
        "time": [
            "2026-01-15T20:00",
            "2026-01-15T21:00",
            "2026-01-16T20:00",
            "2026-01-16T23:00",
            "2026-01-17T21:00",
        ],
        "cloud_cover_low": [10.0, 12.0, 5.0, 8.0, 15.0],
        "cloud_cover_mid": [15.0, 16.0, 6.0, 10.0, 18.0],
        "cloud_cover_high": [20.0, 21.0, 8.0, 12.0, 25.0],
        "temperature_2m": [5.0, 4.8, 6.0, 5.5, 4.0],
        "dew_point_2m": [1.0, 0.9, 1.5, 1.0, 0.5],
        "visibility": [20000.0, 19000.0, 25000.0, 22000.0, 18000.0],
        "wind_speed_300hPa": [30.0, 32.0, 25.0, 28.0, 35.0],
        "precipitation_probability": [5.0, 6.0, 0.0, 2.0, 10.0],
        "wind_speed_10m": [8.0, 9.0, 5.0, 6.0, 12.0],
    }
}


def make_entry(
    twilight_tier: str = TIER_ASTRONOMICAL,
    latitude: float = 57.4778,
    longitude: float = -4.2247,
) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "latitude": latitude,
            "longitude": longitude,
            "elevation": 10.0,
            CONF_PRESET: PRESET_BALANCED,
            CONF_TWILIGHT_TIER: twilight_tier,
            "score_config": get_preset_values(PRESET_BALANCED),
        },
    )


def night_unique_id(entry: MockConfigEntry, night_index: int) -> str:
    """Mirrors sensor.py's StargazingNightScoreSensor unique_id exactly
    -- kept as a single named helper so if that format ever changes,
    every test using it changes with one edit instead of many."""
    return f"{entry.entry_id}_night_{night_index}"


def entity_id(hass, unique_id: str) -> str | None:
    """Resolve a sensor entity_id from its unique_id via the entity
    registry, rather than assuming HA's has_entity_name slugification
    -- see module docstring."""
    registry = er.async_get(hass)
    return registry.async_get_entity_id("sensor", DOMAIN, unique_id)


def get_state(hass, unique_id: str):
    """Look up an entity's state by unique_id. Asserts the entity was
    actually registered (a missing entity_id is itself a bug worth
    failing loudly on, not something to silently treat as 'no state')."""
    resolved = entity_id(hass, unique_id)
    assert resolved is not None, f"No entity registered for unique_id={unique_id!r}"
    return hass.states.get(resolved)


@pytest.fixture(autouse=True)
def stub_moon_functions(monkeypatch):
    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_position",
        lambda observer, at: MoonPosition(altitude=12.5, illumination_percent=40.0),
    )


async def _setup_entry(hass) -> MockConfigEntry:
    entry = make_entry()
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True
    return entry


async def test_three_sensors_created(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)

    assert entity_id(hass, night_unique_id(entry, 0)) is not None
    assert entity_id(hass, night_unique_id(entry, 1)) is not None
    assert entity_id(hass, night_unique_id(entry, 2)) is not None


@pytest.mark.parametrize("night_index", [0, 1, 2])
async def test_night_sensor_state_is_peak_score(hass, freezer, night_index):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    expected_peak = coordinator.data[night_index].peak_score
    assert expected_peak is not None  # the payload covers every night

    state = get_state(hass, night_unique_id(entry, night_index))
    assert state is not None
    assert float(state.state) == expected_peak


async def test_night_sensor_attributes_include_window(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    window = coordinator.data[0].window
    assert window is not None  # sanity check on the fixture itself

    state = get_state(hass, night_unique_id(entry, 0))
    assert state.attributes["night_of"] == coordinator.data[0].night_of.isoformat()
    assert state.attributes["window_start"] == window.start.isoformat()
    assert state.attributes["window_end"] == window.end.isoformat()
    assert state.attributes["twilight_tier"] == window.tier.name
    assert state.attributes["hourly_scores_count"] == len(coordinator.data[0].hourly_scores)


async def test_night_sensor_attributes_include_forecast(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)
    coordinator = entry.runtime_data
    night = coordinator.data[0]
    assert len(night.hourly_scores) == 2  # sanity check on the fixture itself

    state = get_state(hass, night_unique_id(entry, 0))
    forecast = state.attributes["forecast"]
    assert len(forecast) == len(night.hourly_scores)

    for entry_dict, hourly_score in zip(forecast, night.hourly_scores):
        assert entry_dict["time"] == hourly_score.time.isoformat()
        assert entry_dict["score"] == hourly_score.breakdown.total
        # every ScoreBreakdown factor is present -- assert the full set,
        # not a sample, so a dropped factor fails loudly
        breakdown = hourly_score.breakdown
        for factor in (
            "low_cloud",
            "mid_cloud",
            "high_cloud",
            "dew_point_spread",
            "visibility",
            "jet_stream_wind",
            "moon_illumination",
            "precipitation_probability",
            "wind_speed",
        ):
            assert entry_dict[factor] == getattr(breakdown, factor)

        # the `raw` bundle carries each factor's raw reading under the
        # same keys, so the forecast card can render "reading (score)"
        # without a second naming lookup
        raw = entry_dict["raw"]
        conditions = hourly_score.conditions
        assert raw["low_cloud"] == conditions.low_cloud_cover
        assert raw["mid_cloud"] == conditions.mid_cloud_cover
        assert raw["high_cloud"] == conditions.high_cloud_cover
        assert raw["dew_point_spread"] == conditions.dew_point_spread
        assert raw["visibility"] == conditions.visibility
        assert raw["jet_stream_wind"] == conditions.jet_stream_wind_speed
        assert raw["moon_illumination"] == conditions.moon_illumination
        assert raw["precipitation_probability"] == conditions.precipitation_probability
        assert raw["wind_speed"] == conditions.wind_speed


async def test_night_sensor_forecast_empty_when_no_darkness_window(hass, freezer):
    freezer.move_to("2026-06-20 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = make_entry(twilight_tier=TIER_ASTRONOMICAL)
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True

    state = get_state(hass, night_unique_id(entry, 0))
    assert state.attributes["forecast"] == []


async def test_night_sensor_unique_ids_are_distinct_and_scoped_to_entry(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)

    unique_ids = {
        night_unique_id(entry, 0),
        night_unique_id(entry, 1),
        night_unique_id(entry, 2),
    }
    assert len(unique_ids) == 3
    assert all(uid.startswith(entry.entry_id) for uid in unique_ids)
    # And every one of them actually resolved to a real entity -- a
    # unique_id that's merely well-formed but unregistered would be a
    # silent setup bug.
    for uid in unique_ids:
        assert entity_id(hass, uid) is not None


async def test_night_sensors_unknown_when_no_darkness_window(hass, freezer):
    # A genuine polar-day observer (Tromsø) on the summer solstice: the
    # sun never reaches even civil twilight, so EVERY night's window is
    # None for every preferred-darkness tier (even "astronomical" with
    # its nautical/civil fallback). sensor.py must fail soft -- peak
    # sensors unknown, window attributes None, no crash -- not just the
    # coordinator (which is already covered in test_coordinator.py).
    freezer.move_to("2026-06-20 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = make_entry(latitude=69.6, longitude=18.9)
    entry.add_to_hass(hass)
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)
    assert result is True

    for night_index in range(3):
        state = get_state(hass, night_unique_id(entry, night_index))
        assert state.state == "unknown"
        assert state.attributes["window_start"] is None
        assert state.attributes["window_end"] is None
        assert state.attributes["twilight_tier"] is None
        assert state.attributes["hourly_scores_count"] == 0


async def test_unload_entry_removes_sensor_entities(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    await hass.config.async_set_time_zone("Europe/London")

    entry = await _setup_entry(hass)
    tonight_id = entity_id(hass, night_unique_id(entry, 0))
    assert hass.states.get(tonight_id) is not None

    result = await hass.config_entries.async_unload(entry.entry_id)

    assert result is True
    # HA keeps the registry entry on config-entry unload and (since the
    # "write unavailable on unload" behavior) marks its state unavailable
    # + restored rather than deleting it outright -- the live entity is
    # gone, which is what this test verifies.
    state = hass.states.get(tonight_id)
    assert state is not None
    assert state.state == "unavailable"
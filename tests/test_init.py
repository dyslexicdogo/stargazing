"""Tests for __init__.py.

Confirms the actual failure the user hit in the real UI is fixed:
config_flow.py creates an entry, but nothing previously turned it into a
running coordinator ("Setup failed... No setup or config entry setup
function defined"). These tests exercise that exact path.
"""

import re

import pytest
from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stargazing import async_setup_entry, async_unload_entry
from custom_components.stargazing.client import BASE_URL
from custom_components.stargazing.const import (
    CONF_PRESET,
    CONF_TWILIGHT_TIER,
    DOMAIN,
    PRESET_BALANCED,
    TIER_CIVIL_MINIMUM,
)
from custom_components.stargazing.coordinator import StargazingCoordinator
from custom_components.stargazing.presets import get_preset_values

URL_PATTERN = re.compile(rf"^{re.escape(BASE_URL)}.*$")

VALID_PAYLOAD = {
    "hourly": {
        "time": ["2026-01-15T20:00", "2026-01-15T21:00"],
        "cloud_cover_low": [10.0, 12.0],
        "cloud_cover_mid": [15.0, 16.0],
        "cloud_cover_high": [20.0, 21.0],
        "temperature_2m": [5.0, 4.8],
        "dew_point_2m": [1.0, 0.9],
        "visibility": [20000.0, 19000.0],
        "wind_speed_300hPa": [30.0, 32.0],
        "precipitation_probability": [5.0, 6.0],
        "wind_speed_10m": [8.0, 9.0],
    }
}


def make_entry() -> MockConfigEntry:
    # Same shape config_flow.py actually produces -- see
    # test_preset_data_matches_get_preset_values in test_config_flow.py
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            "latitude": 57.4778,
            "longitude": -4.2247,
            "elevation": 10.0,
            CONF_PRESET: PRESET_BALANCED,
            CONF_TWILIGHT_TIER: TIER_CIVIL_MINIMUM,
            "score_config": get_preset_values(PRESET_BALANCED),
        },
    )


@pytest.fixture(autouse=True)
def stub_moon_functions(monkeypatch):
    # Same rationale as test_coordinator.py: this test is about wiring
    # (does setup actually produce a running coordinator?), not
    # astronomical correctness -- already covered by test_astro.py.
    # Also avoids needing the bundled ephemeris file in this sandbox.
    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_altitude",
        lambda observer, at: 12.5,
    )
    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_illumination_percent",
        lambda observer, at: 40.0,
    )


async def test_setup_entry_creates_and_starts_coordinator(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD)
        # Goes through HA's real setup path (NOT_LOADED -> SETUP_IN_PROGRESS
        # -> LOADED), rather than calling async_setup_entry directly --
        # DataUpdateCoordinator.async_config_entry_first_refresh() checks
        # that the entry is actually in SETUP_IN_PROGRESS state (another
        # report_usage deprecation, breaks_in_ha_version=2025.11, same
        # category as the config_entry=None issue fixed earlier). Calling
        # async_setup_entry directly skips HA's real state transitions
        # entirely, so MockConfigEntry.add_to_hass() alone leaves the
        # entry at its default state and silently violates this.
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, StargazingCoordinator)


async def test_setup_entry_coordinator_has_scored_data_after_first_refresh(
    hass, freezer
):
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD)
        await hass.config_entries.async_setup(entry.entry_id)

    # both readings (20:00, 21:00) fall inside a January Inverness
    # darkness window -- confirms the coordinator actually ran, not just
    # that it was constructed
    assert entry.runtime_data.data is not None
    assert len(entry.runtime_data.data) == 2
    assert entry.runtime_data.data[0].breakdown.total >= 0.0


async def test_unload_entry_returns_true(hass):
    entry = make_entry()
    entry.add_to_hass(hass)

    result = await async_unload_entry(hass, entry)

    assert result is True
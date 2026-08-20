"""Tests for __init__.py.

Confirms the actual failure the user hit in the real UI is fixed:
config_flow.py creates an entry, but nothing previously turned it into a
running coordinator ("Setup failed... No setup or config entry setup
function defined"). These tests exercise that exact path.
"""

import re
from unittest.mock import AsyncMock, patch

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

# Use regex pattern that matches any query params
URL_PATTERN = re.compile(rf"^{re.escape(BASE_URL)}")

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
    from custom_components.stargazing.astro import MoonPosition

    monkeypatch.setattr(
        "custom_components.stargazing.coordinator.moon_position",
        lambda observer, at: MoonPosition(altitude=12.5, illumination_percent=40.0),
    )


async def test_setup_entry_creates_and_starts_coordinator(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
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
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        await hass.config_entries.async_setup(entry.entry_id)

    assert entry.runtime_data.data is not None
    assert len(entry.runtime_data.data) == 3

    night_0 = entry.runtime_data.data[0]
    assert len(night_0.hourly_scores) == 2
    assert night_0.hourly_scores[0].breakdown.total >= 0.0


async def test_unload_entry_returns_true(hass):
    entry = make_entry()
    entry.add_to_hass(hass)

    result = await async_unload_entry(hass, entry)

    assert result is True


async def test_unload_entry_shuts_down_coordinator(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)
        assert result is True

    coordinator = entry.runtime_data

    # The coordinator has no listeners yet (no sensor.py exists), so HA's
    # DataUpdateCoordinator never arms its polling timer (_unsub_refresh
    # stays None). What we actually need to verify is the async_shutdown()
    # fix in __init__.py -- that unload stops the (potentially polling)
    # coordinator instead of leaving it orphaned.
    with patch.object(coordinator, "async_shutdown", AsyncMock()) as mock_shutdown:
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_shutdown.assert_awaited_once()



async def test_async_migrate_entry_returns_true_for_v1(hass):
    """Migration stub returns True for version 1 entries."""
    from custom_components.stargazing import async_migrate_entry
    from homeassistant.config_entries import ConfigEntry
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="stargazing", version=1, data={})
    result = await async_migrate_entry(hass, entry)
    assert result is True

async def test_async_migrate_entry_false_for_unknown_version(hass):
    """Migration returns False for future/unknown versions."""
    from custom_components.stargazing import async_migrate_entry
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="stargazing", version=999, data={})
    result = await async_migrate_entry(hass, entry)
    assert result is False
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
    CARD_RESOURCES,
    CONF_PRESET,
    CONF_SCORE_CONFIG,
    CONF_TWILIGHT_TIER,
    DOMAIN,
    PRESET_BALANCED,
    TIER_ASTRONOMICAL,
    TIER_CIVIL,
    TWILIGHT_TIER_CHOICES,
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
            CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL,
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


@pytest.fixture(autouse=True)
def reset_frontend_registered_flag():
    """Reset the process-wide frontend registration guard per test."""
    import custom_components.stargazing as stargazing_module

    stargazing_module._frontend_registered = False
    yield
    stargazing_module._frontend_registered = False


class FakeResources:
    """Minimal stand-in for Lovelace's storage-mode resources collection."""

    def __init__(self, existing_urls=(), loaded=True):
        self._items = [{"url": url} for url in existing_urls]
        self.loaded = loaded
        self.load_called = False

    async def async_load(self):
        self.load_called = True
        self.loaded = True

    def async_items(self):
        return list(self._items)

    async def async_create_item(self, item):
        self._items.append(item)
        return item


class FakeLovelaceData:
    def __init__(self, resources):
        self.resources = resources


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


async def test_setup_entry_applies_options_over_data(hass, freezer):
    # Options layer wins per-key over the data layer: one overridden edge
    # plus an overridden tier must reach the coordinator, while untouched
    # keys keep falling through to the data layer's preset values.
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = MockConfigEntry(
        domain=DOMAIN,
        data=make_entry().data,
        options={
            CONF_TWILIGHT_TIER: TIER_CIVIL,
            CONF_SCORE_CONFIG: {"edges": {"low_cloud_max": 33.0}},
        },
    )
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        assert await hass.config_entries.async_setup(entry.entry_id) is True

    assert entry.state is ConfigEntryState.LOADED
    coordinator = entry.runtime_data

    assert coordinator._edges.low_cloud_max == 33.0  # options override
    balanced_edges = get_preset_values(PRESET_BALANCED)["edges"]
    assert coordinator._edges.visibility_min == balanced_edges["visibility_min"]
    assert coordinator._spans.low_cloud_max == 60.0  # spans section untouched
    assert coordinator._weights.low_cloud == 5.0  # weights section untouched
    assert coordinator._tiers == TWILIGHT_TIER_CHOICES[TIER_CIVIL]


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


# ---------------------------------------------------------------------------
# Frontend / Lovelace card registration
# ---------------------------------------------------------------------------


async def test_setup_entry_succeeds_when_http_not_loaded(hass, freezer):
    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    assert entry.state is ConfigEntryState.LOADED


async def test_frontend_registers_static_path_when_http_available(hass, freezer):
    from homeassistant.setup import async_setup_component

    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"
    await async_setup_component(hass, "http", {})

    entry = make_entry()
    entry.add_to_hass(hass)

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    import custom_components.stargazing as stargazing_module

    assert stargazing_module._frontend_registered is True


async def test_lovelace_resource_created_when_missing(hass, freezer):
    from homeassistant.setup import async_setup_component

    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"
    await async_setup_component(hass, "http", {})

    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelaceData(resources)

    entry = make_entry()
    entry.add_to_hass(hass)

    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        aioresponses() as mocked,
    ):
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    created_urls = {item["url"] for item in resources.async_items()}
    assert created_urls == set(CARD_RESOURCES.values())


async def test_lovelace_resource_not_duplicated_when_already_present(hass, freezer):
    from homeassistant.setup import async_setup_component

    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"
    await async_setup_component(hass, "http", {})

    already_there = set(CARD_RESOURCES.values())
    resources = FakeResources(existing_urls=already_there)
    hass.data["lovelace"] = FakeLovelaceData(resources)

    entry = make_entry()
    entry.add_to_hass(hass)

    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        aioresponses() as mocked,
    ):
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    urls = [item["url"] for item in resources.async_items()]
    for url in already_there:
        assert urls.count(url) == 1


async def test_lovelace_resources_loaded_if_not_already(hass, freezer):
    from homeassistant.setup import async_setup_component

    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"
    await async_setup_component(hass, "http", {})

    resources = FakeResources(loaded=False)
    hass.data["lovelace"] = FakeLovelaceData(resources)

    entry = make_entry()
    entry.add_to_hass(hass)

    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        aioresponses() as mocked,
    ):
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True
    assert resources.load_called is True


async def test_frontend_registration_is_noop_when_lovelace_absent(hass, freezer):
    from homeassistant.setup import async_setup_component

    freezer.move_to("2026-01-15 20:00:00")
    hass.config.time_zone = "Europe/London"
    await async_setup_component(hass, "http", {})
    assert "lovelace" not in hass.data

    entry = make_entry()
    entry.add_to_hass(hass)

    with (
        patch.object(hass.http, "async_register_static_paths", AsyncMock()),
        aioresponses() as mocked,
    ):
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)
        result = await hass.config_entries.async_setup(entry.entry_id)

    assert result is True


async def test_frontend_registration_only_runs_once_across_entries(hass, freezer):
    from homeassistant.setup import async_setup_component
    from custom_components.stargazing import _async_register_frontend

    await async_setup_component(hass, "http", {})

    resources = FakeResources()
    hass.data["lovelace"] = FakeLovelaceData(resources)

    with patch.object(
        hass.http, "async_register_static_paths", AsyncMock()
    ) as mock_register:
        await _async_register_frontend(hass)
        await _async_register_frontend(hass)

    mock_register.assert_awaited_once()
    urls = [item["url"] for item in resources.async_items()]
    for url in CARD_RESOURCES.values():
        assert urls.count(url) == 1
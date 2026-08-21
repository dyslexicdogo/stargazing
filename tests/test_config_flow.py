"""Tests for config_flow.py.

Uses the real hass fixture plus aioresponses to mock the underlying HTTP
call (same regex URL pattern as test_client.py), rather than swapping out
OpenMeteoClient for a fake -- this way the test actually exercises
client.py's real code path during location validation, matching the
"validated with a real Open-Meteo test call" intent from sun_bathing.
"""

import re

import pytest
from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData

from custom_components.stargazing.client import BASE_URL
from custom_components.stargazing.const import (
    CONF_PRESET,
    CONF_TWILIGHT_TIER,
    DEFAULT_TWILIGHT_TIER,
    DOMAIN,
    PRESET_STRICT,
    TIER_ASTRONOMICAL,
    TWILIGHT_TIER_CHOICES,
)

URL_PATTERN = re.compile(rf"^{re.escape(BASE_URL)}.*$")

# Minimal valid Open-Meteo shape -- enough for client.py's parser to
# succeed, which is all location validation actually checks.
VALID_PAYLOAD = {
    "hourly": {
        "time": ["2026-01-15T20:00"],
        "cloud_cover_low": [10.0],
        "cloud_cover_mid": [15.0],
        "cloud_cover_high": [20.0],
        "temperature_2m": [5.0],
        "dew_point_2m": [1.0],
        "visibility": [20000.0],
        "wind_speed_300hPa": [30.0],
        "precipitation_probability": [5.0],
        "wind_speed_10m": [8.0],
    }
}


async def test_full_flow_creates_entry_with_valid_location(hass):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 10.0},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "preset"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_PRESET: PRESET_STRICT,
                CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"]["latitude"] == 57.4778
    assert result["data"]["longitude"] == -4.2247
    assert result["data"][CONF_PRESET] == PRESET_STRICT
    assert result["data"][CONF_TWILIGHT_TIER] == TIER_ASTRONOMICAL
    assert "score_config" in result["data"]
    assert set(result["data"]["score_config"]) == {"edges", "spans", "weights"}


async def test_twilight_tier_defaults_to_astronomical(hass):
    # The preset step defaults to the darkest preference (astronomical);
    # submitting without choosing a tier stores that default.
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 10.0},
        )
        assert result["step_id"] == "preset"
        # the form exposes the new preferred-darkness options
        assert set(TWILIGHT_TIER_CHOICES) == {
            "astronomical",
            "nautical",
            "civil",
        }
        assert DEFAULT_TWILIGHT_TIER == "astronomical"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PRESET: PRESET_STRICT},  # omit the tier -> default applies
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_TWILIGHT_TIER] == DEFAULT_TWILIGHT_TIER


async def test_invalid_location_stays_on_user_step_with_error(hass):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, status=400, body="Bad Request")

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 0.0, "longitude": 0.0, "elevation": 0.0}  # Valid range, API returns 400
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_connection_failure_stays_on_user_step_with_error(hass):
    import aiohttp

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, exception=aiohttp.ClientConnectionError("boom"))

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 0.0},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_preset_data_matches_get_preset_values(hass):
    from custom_components.stargazing.presets import get_preset_values

    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 0.0},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_PRESET: PRESET_STRICT,
                CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL,
            },
        )

    assert result["data"]["score_config"] == get_preset_values(PRESET_STRICT)


async def test_no_options_flow_implemented_yet(hass):
    # deliberate, per config_flow.py's comment -- Phase 10 doesn't exist
    # yet. The base ConfigFlow class always has async_get_options_flow
    # (hasattr alone can't detect an override, since it's inherited
    # either way) -- the real check is that it still raises the base
    # class's UnknownHandler, confirming we haven't overridden it.
    from homeassistant import data_entry_flow

    from custom_components.stargazing.config_flow import StargazingConfigFlow

    with pytest.raises(data_entry_flow.UnknownHandler):
        StargazingConfigFlow.async_get_options_flow(None)

async def test_invalid_latitude_range_shows_field_error(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    
    with pytest.raises(InvalidData) as exc_info:
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 999.0, "longitude": -4.2247, "elevation": 0.0},
        )
    
    assert "latitude" in str(exc_info.value.schema_errors)
    assert exc_info.value.path == ["latitude"]



async def test_invalid_longitude_range_shows_field_error(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    
    with pytest.raises(InvalidData) as exc_info:
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": 999.0, "elevation": 0.0},
        )
    
    assert "longitude" in str(exc_info.value.schema_errors)

async def test_invalid_elevation_range_shows_field_error(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    
    with pytest.raises(InvalidData) as exc_info:
        await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 99999.0},
        )
    
    assert "elevation" in str(exc_info.value.schema_errors)

async def test_duplicate_location_aborts(hass):
    with aioresponses() as mocked:
        mocked.get(URL_PATTERN, payload=VALID_PAYLOAD, repeat=True)

        # First flow creates entry
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 10.0},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PRESET: PRESET_STRICT, CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL},
        )
        assert result["type"] == FlowResultType.CREATE_ENTRY

        # Second flow with same location should abort
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"latitude": 57.4778, "longitude": -4.2247, "elevation": 10.0},
        )
        assert result["type"] == FlowResultType.ABORT
        assert result["reason"] == "already_configured"
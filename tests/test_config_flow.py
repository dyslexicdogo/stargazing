"""Tests for config_flow.py.

Uses the real hass fixture plus aioresponses to mock the underlying HTTP
call (same regex URL pattern as test_client.py), rather than swapping out
OpenMeteoClient for a fake -- this way the test actually exercises
client.py's real code path during location validation, matching the
"validated with a real Open-Meteo test call" intent from sun_bathing.
"""

import re
from unittest.mock import patch

import pytest
from aioresponses import aioresponses
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.stargazing.client import BASE_URL
from custom_components.stargazing.const import (
    CONF_PRESET,
    CONF_SCORE_CONFIG,
    CONF_TWILIGHT_TIER,
    DEFAULT_TWILIGHT_TIER,
    DOMAIN,
    PRESET_BALANCED,
    PRESET_STRICT,
    TIER_ASTRONOMICAL,
    TIER_CIVIL,
    TWILIGHT_TIER_CHOICES,
)
from custom_components.stargazing.presets import get_preset_values

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


# ---------------------------------------------------------------------------
# Options flow (Phase 10): six-page wizard
# ---------------------------------------------------------------------------


def make_options_entry(hass, *, custom_edges=None):
    """A registered entry as the config flow would have created it."""
    score_config = get_preset_values(PRESET_BALANCED)
    if custom_edges:
        score_config["edges"].update(custom_edges)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "latitude": 57.4778,
            "longitude": -4.2247,
            "elevation": 10.0,
            CONF_PRESET: PRESET_BALANCED,
            CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL,
            CONF_SCORE_CONFIG: score_config,
        },
    )
    entry.add_to_hass(hass)
    return entry


def form_defaults(result):
    """voluptuous fills defaults for every Required-with-default key when
    validating an empty dict -- the same values the UI would prefill with."""
    return result["data_schema"]({})


async def test_options_wizard_full_walkthrough(hass):
    entry = make_options_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "init"

    # Keep the current preset -> tuning pages prefill from stored values
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PRESET: PRESET_BALANCED}
    )
    assert result["step_id"] == "night"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TWILIGHT_TIER: TIER_CIVIL}
    )
    assert result["step_id"] == "cloud_thresholds"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"low_cloud_max": 5.0, "mid_cloud_max": 12.0, "high_cloud_max": 25.0},
    )
    assert result["step_id"] == "sky_thresholds"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "dew_point_spread_min": 6.0,
            "visibility_min": 22000.0,
            "jet_stream_wind_max": 18.0,
            "moon_illumination_max": 8.0,
            "precipitation_probability_max": 4.0,
            "wind_speed_max": 9.0,
        },
    )
    assert result["step_id"] == "falloff_spans"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "low_cloud_max": 55.0,
            "mid_cloud_max": 65.0,
            "high_cloud_max": 45.0,
            "dew_point_spread_min": 5.0,
            "visibility_min": 16000.0,
            "jet_stream_wind_max": 35.0,
            "moon_illumination_max": 55.0,
            "precipitation_probability_max": 30.0,
            "wind_speed_max": 25.0,
        },
    )
    assert result["step_id"] == "weights"

    # Finishing schedules an automatic reload (OptionsFlowWithReload); the
    # wizard itself must not depend on one happening inside the flow.
    with patch.object(hass.config_entries, "async_schedule_reload"):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                "low_cloud": 4.0,
                "mid_cloud": 2.0,
                "high_cloud": 1.0,
                "dew_point_spread": 2.0,
                "visibility": 1.0,
                "jet_stream_wind": 1.0,
                "moon_illumination": 3.0,
                "precipitation_probability": 2.0,
                "wind_speed": 1.0,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    options = result["data"]
    assert options[CONF_PRESET] == PRESET_BALANCED
    assert options[CONF_TWILIGHT_TIER] == TIER_CIVIL
    assert options[CONF_SCORE_CONFIG]["edges"]["low_cloud_max"] == 5.0
    assert options[CONF_SCORE_CONFIG]["edges"]["visibility_min"] == 22000.0
    assert options[CONF_SCORE_CONFIG]["spans"]["visibility_min"] == 16000.0
    assert options[CONF_SCORE_CONFIG]["weights"]["moon_illumination"] == 3.0


async def test_options_prefill_keeps_custom_values_when_preset_unchanged(hass):
    # Customized low-cloud ceiling in the data layer; re-running the wizard
    # WITHOUT switching presets must show that value again on page 3.
    entry = make_options_entry(hass, custom_edges={"low_cloud_max": 33.0})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PRESET: PRESET_BALANCED}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL}
    )

    assert result["step_id"] == "cloud_thresholds"
    defaults = form_defaults(result)
    assert defaults["low_cloud_max"] == 33.0  # customization survived
    assert defaults["mid_cloud_max"] == 20.0  # balanced default fills the rest


async def test_options_preset_change_resets_to_new_preset_defaults(hass):
    # Switching preset is a statement that you want its numbers back --
    # the previous customization must NOT leak into the prefills.
    entry = make_options_entry(hass, custom_edges={"low_cloud_max": 33.0})

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PRESET: PRESET_STRICT}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_TWILIGHT_TIER: TIER_ASTRONOMICAL}
    )

    assert result["step_id"] == "cloud_thresholds"
    defaults = form_defaults(result)
    strict_edges = get_preset_values(PRESET_STRICT)["edges"]
    assert defaults["low_cloud_max"] == strict_edges["low_cloud_max"]


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
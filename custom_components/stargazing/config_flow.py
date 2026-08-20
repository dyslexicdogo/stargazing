"""Config flow for stargazing.

Two chained steps, accumulating into self._data (the flow instance stays
alive for the whole wizard, same pattern as sun_bathing's config_flow.py):

1. async_step_user -- latitude/longitude/elevation, defaulting to HA's
   home coordinates, validated with a REAL Open-Meteo call (reusing the
   already-tested client.py, not a separate validation path)
2. async_step_preset -- strict/balanced/relaxed + twilight tier
   (astronomical/nautical/civil minimum), then creates the entry

Full per-factor threshold/span/weight editing is NOT here -- that's
Phase 10's options flow with collapsible sections. This is only the
initial setup wizard, matching PROJECT_PRINCIPLES.md's phase split.

Uses plain voluptuous validators (vol.Coerce, vol.In, vol.Range), not
HA's selector.* classes -- sun_bathing hit a persistent, hard-to-diagnose
"Config flow could not be loaded: 400 Bad Request / Invalid handler"
error from selector.NumberSelector/SelectSelector with translation_key
mismatches, and reverting to plain validators was what actually fixed
it. Not repeating that mistake here.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_ELEVATION, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import OpenMeteoClient, OpenMeteoError
from .const import (
    CONF_PRESET,
    CONF_TWILIGHT_TIER,
    DEFAULT_PRESET,
    DEFAULT_TWILIGHT_TIER,
    DOMAIN,
    PRESETS,
    TWILIGHT_TIER_CHOICES,
)
from .presets import get_preset_values

_LOGGER = logging.getLogger(__name__)


class StargazingConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles the initial setup wizard: location, then preset + tier."""

    VERSION = 2

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            valid = await self._async_validate_location(
                user_input[CONF_LATITUDE], user_input[CONF_LONGITUDE]
            )
            if valid:
                self._data.update(user_input)
                unique_id = f"{user_input[CONF_LATITUDE]:.4f},{user_input[CONF_LONGITUDE]:.4f}"
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return await self.async_step_preset()
            errors["base"] = "cannot_connect"

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_LATITUDE, default=self.hass.config.latitude
                ): vol.All(vol.Coerce(float), vol.Range(min=-90, max=90)),
                vol.Required(
                    CONF_LONGITUDE, default=self.hass.config.longitude
                ): vol.All(vol.Coerce(float), vol.Range(min=-180, max=180)),
                vol.Optional(
                    CONF_ELEVATION, default=self.hass.config.elevation
                ): vol.All(vol.Coerce(float), vol.Range(min=-500, max=9000)),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_preset(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._data[CONF_PRESET] = user_input[CONF_PRESET]
            self._data[CONF_TWILIGHT_TIER] = user_input[CONF_TWILIGHT_TIER]
            self._data["score_config"] = get_preset_values(user_input[CONF_PRESET])

            return self.async_create_entry(
                title=f"Stargazing ({self._data[CONF_LATITUDE]:.2f}, "
                f"{self._data[CONF_LONGITUDE]:.2f})",
                data=self._data,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_PRESET, default=DEFAULT_PRESET): vol.In(PRESETS),
                vol.Required(
                    CONF_TWILIGHT_TIER, default=DEFAULT_TWILIGHT_TIER
                ): vol.In(list(TWILIGHT_TIER_CHOICES)),
            }
        )
        return self.async_show_form(step_id="preset", data_schema=schema)

    async def _async_validate_location(self, latitude: float, longitude: float) -> bool:
        """Real API call, not a mock -- confirms Open-Meteo actually
        accepts these coordinates before the entry is ever created."""
        session = async_get_clientsession(self.hass)
        client = OpenMeteoClient(session)
        try:
            await client.async_get_hourly_forecast(
                latitude=latitude, longitude=longitude, forecast_days=1
            )
        except OpenMeteoError as err:
            _LOGGER.warning("Location validation failed: %s", err)
            return False
        return True

    # No async_get_options_flow yet, deliberately: its mere presence makes
    # HA show an "options" gear icon in the UI. Adding a stub that raises
    # NotImplementedError would let a user click through to a crash before
    # Phase 10 (per-factor options flow) actually exists. Omitting the
    # method entirely means HA correctly shows no options button until
    # there's a real one to show.
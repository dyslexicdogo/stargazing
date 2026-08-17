"""The stargazing integration.

This is the piece that was missing when "Setup failed for custom
integration 'stargazing': No setup or config entry setup function
defined" showed up in the UI -- config_flow.py only creates the entry;
this file is what actually turns that entry into a running coordinator.

No platforms are forwarded yet (no sensor.py exists) -- this only gets
the coordinator polling. Entities are a later phase.
"""

from __future__ import annotations

import logging

from astral import Observer
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ELEVATION, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import OpenMeteoClient
from .const import CONF_TWILIGHT_TIER, TWILIGHT_TIER_CHOICES
from .coordinator import StargazingCoordinator
from .presets import config_entry_to_score_config

_LOGGER = logging.getLogger(__name__)

type StargazingConfigEntry = ConfigEntry[StargazingCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: StargazingConfigEntry) -> bool:
    """Set up stargazing from a config entry."""
    observer = Observer(
        latitude=entry.data[CONF_LATITUDE],
        longitude=entry.data[CONF_LONGITUDE],
        elevation=entry.data.get(CONF_ELEVATION, 0),
    )
    edges, spans, weights = config_entry_to_score_config(entry.data["score_config"])
    tiers = TWILIGHT_TIER_CHOICES[entry.data[CONF_TWILIGHT_TIER]]

    session = async_get_clientsession(hass)
    client = OpenMeteoClient(session)

    coordinator = StargazingCoordinator(
        hass=hass,
        config_entry=entry,
        client=client,
        observer=observer,
        edges=edges,
        spans=spans,
        weights=weights,
        tiers=tiers,
    )

    # async_config_entry_first_refresh() converts an UpdateFailed from
    # our first poll into ConfigEntryNotReady, which HA understands as
    # "retry setup later" rather than a hard failure -- the correct
    # behavior for e.g. a transient Open-Meteo outage or (per astro.py)
    # a missing/corrupted bundled ephemeris on first install.
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    return True


async def async_unload_entry(hass: HomeAssistant, entry: StargazingConfigEntry) -> bool:
    """Unload a config entry. Nothing to clean up yet -- no platforms,
    no listeners, no registered resources -- but HA expects this
    function to exist for reload/unload support."""
    return True
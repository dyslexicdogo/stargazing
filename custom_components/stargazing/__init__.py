"""The stargazing integration.

This is the piece that was missing when "Setup failed for custom
integration 'stargazing': No setup or config entry setup function
defined" showed up in the UI -- config_flow.py only creates the entry;
this file is what actually turns that entry into a running coordinator.

Now forwards to sensor.py (Phase 7): once the coordinator's first
refresh succeeds, entities are set up via the standard
async_forward_entry_setups()/async_unload_platforms() pair, and unload
tears platforms down before shutting down the coordinator itself -- if
platform unload were skipped, entities could keep referencing a
shut-down coordinator.
"""

from __future__ import annotations

import logging

from astral import Observer
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ELEVATION, CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import OpenMeteoClient
from .const import CONF_TWILIGHT_TIER, TWILIGHT_TIER_CHOICES
from .coordinator import StargazingCoordinator
from .presets import config_entry_to_score_config, get_preset_values

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: StargazingConfigEntry) -> bool:
    """Unload a config entry.

    Unloads sensor.py's entities first, then shuts down the polling
    coordinator -- only once platforms have confirmed they're done with
    it. If platform unload fails (unload_ok is False), the coordinator
    is deliberately left running rather than shut down out from under
    entities that are still attached to it.
    """
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator = getattr(entry, "runtime_data", None)
        if coordinator is not None:
            await coordinator.async_shutdown()  # stop the polling

    return unload_ok


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate old config entry data to current version."""
    if entry.version == 1:
        # Future migrations go here (e.g., add new fields, rename keys)
        return True
    return False
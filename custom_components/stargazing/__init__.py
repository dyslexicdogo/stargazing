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
from pathlib import Path
from typing import Any

from astral import Observer
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ELEVATION, CONF_LATITUDE, CONF_LONGITUDE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import OpenMeteoClient
from .const import (
    CARD_RESOURCES,
    CONF_NOTIFY_CHECK_TIME,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_SCORE_THRESHOLD,
    CONF_NOTIFY_TARGET,
    CONF_SCORE_CONFIG,
    CONF_TWILIGHT_TIER,
    DEFAULT_NOTIFY_CHECK_TIME,
    DEFAULT_NOTIFY_ENABLED,
    DEFAULT_NOTIFY_SCORE_THRESHOLD,
    DEFAULT_TWILIGHT_TIER,
    TWILIGHT_TIER_CHOICES,
)
from .coordinator import StargazingCoordinator
from .notifications import StargazingNotifier
from .presets import (
    config_entry_to_score_config,
    get_preset_values,
    overlay_score_config,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]

type StargazingConfigEntry = ConfigEntry[StargazingCoordinator]


# Guards _async_register_frontend() so the static-path + Lovelace-resource
# registration only runs once per HA run, not once per config entry --
# registering the same static path twice raises, and re-adding an
# already-present Lovelace resource is wasted work. Module-level (not
# hass.data) deliberately: these are process-wide web server routes, not
# per-entry state, and don't need to survive a reload of just one entry.
_frontend_registered = False


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the card JS from www/ and register it as a Lovelace resource.

    Mirrors sun_bathing's proven pattern: StaticPathConfig objects (not
    plain dicts -- that raises in current HA core) for serving the file,
    and the storage-collection resources API rather than
    add_extra_js_url(), which has an intermittent render-race on a hard
    browser refresh (see PROJECT_PRINCIPLES.md).
    """
    global _frontend_registered
    if _frontend_registered:
        return

    if hass.http is None:
        # Bare test fixtures don't load the http component (see
        # PROJECT_PRINCIPLES.md's gotchas); on a real HA instance this
        # is always loaded, but there's no reason setup should hard-fail
        # over cards if it somehow isn't ready yet -- same "fail soft,
        # don't crash the whole integration" principle as elsewhere.
        _LOGGER.debug("hass.http not available yet; skipping card registration")
        return

    www_path = Path(__file__).parent / "www"
    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                url_path=url_path,
                path=str(www_path / filename),
                cache_headers=False,
            )
            for filename, url_path in CARD_RESOURCES.items()
        ]
    )

    await _async_register_lovelace_resources(hass)
    _frontend_registered = True


async def _async_register_lovelace_resources(hass: HomeAssistant) -> None:
    """Add each card as a Lovelace resource, if not already present.

    Only works when Lovelace is in "storage" mode (the default). In
    YAML-mode dashboards there's no resources collection to manage, so
    this is a no-op and the user needs to add the resource manually --
    same limitation sun_bathing has, noted in PROJECT_PRINCIPLES.md's
    follow-ups.
    """
    lovelace_data = hass.data.get("lovelace")
    if lovelace_data is None:
        return  # frontend/lovelace not loaded yet or unavailable

    resources = getattr(lovelace_data, "resources", None)
    if resources is None:
        return  # YAML mode has no resources collection to manage

    if not resources.loaded:
        await resources.async_load()

    existing_urls = {item.get("url") for item in resources.async_items()}

    for url_path in CARD_RESOURCES.values():
        if url_path in existing_urls:
            continue
        await resources.async_create_item({"res_type": "module", "url": url_path})


def _notify_settings(entry: StargazingConfigEntry) -> dict[str, Any]:
    """Effective notification settings: options win over data over defaults.

    Standalone function (like determine_night_of in coordinator.py) so it
    can be exercised without a full setup."""
    merged: dict[str, Any] = {}
    for key, default in (
        (CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED),
        (CONF_NOTIFY_SCORE_THRESHOLD, DEFAULT_NOTIFY_SCORE_THRESHOLD),
        (CONF_NOTIFY_CHECK_TIME, DEFAULT_NOTIFY_CHECK_TIME),
        (CONF_NOTIFY_TARGET, None),
    ):
        merged[key] = entry.options.get(key, entry.data.get(key, default))
    return merged


async def async_setup_entry(hass: HomeAssistant, entry: StargazingConfigEntry) -> bool:
    """Set up stargazing from a config entry."""
    observer = Observer(
        latitude=entry.data[CONF_LATITUDE],
        longitude=entry.data[CONF_LONGITUDE],
        elevation=entry.data.get(CONF_ELEVATION, 0),
    )
    # Options layer wins per-key over the data layer: the options wizard
    # writes score_config / twilight_tier into entry.options, while the
    # original values stay untouched in entry.data as the fallback base.
    score_data = overlay_score_config(
        entry.data.get(CONF_SCORE_CONFIG), entry.options.get(CONF_SCORE_CONFIG)
    )
    edges, spans, weights = config_entry_to_score_config(score_data)
    tier_name = entry.options.get(
        CONF_TWILIGHT_TIER, entry.data.get(CONF_TWILIGHT_TIER, DEFAULT_TWILIGHT_TIER)
    )
    tiers = TWILIGHT_TIER_CHOICES[tier_name]

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

    await _async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Notifications (Phase 11): arm the daily threshold check when the
    # user enabled it. Saving the options wizard reloads this entry
    # (OptionsFlowWithReload), re-running setup so changed settings
    # always take effect; the previous timer is cancelled through
    # async_on_unload during that reload.
    notify_settings = _notify_settings(entry)
    if notify_settings[CONF_NOTIFY_ENABLED]:
        notifier = StargazingNotifier(hass, coordinator, notify_settings)
        if notifier.arm():
            entry.async_on_unload(notifier.unsub)

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
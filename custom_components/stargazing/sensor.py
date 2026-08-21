"""Sensor platform for stargazing.

Backs the 3-night forecast Lovelace card (PROJECT_PRINCIPLES.md phase
7/9): one sensor per night (NUM_NIGHTS_AHEAD of them), state = that
night's peak score, attributes carry the darkness window, which twilight
tier was actually achieved, and a per-hour `forecast` list the card's
tap-to-see-breakdown renders.

Deliberately thin, per PROJECT_PRINCIPLES.md ("Coordinator is thin
orchestration glue... no business logic lives here" extends to sensor.py
too): entities just read coordinator.data. All actual scoring/windowing
logic stays in score.py / astro.py / coordinator.py.
"""

from __future__ import annotations

import logging

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NUM_NIGHTS_AHEAD, HourlyScore, NightlyScore, StargazingCoordinator
from .score import HourlyConditions, ScoreBreakdown

_LOGGER = logging.getLogger(__name__)

# Human-friendly labels for each night-ahead index. Falls back to a
# generic "Night +N" label if NUM_NIGHTS_AHEAD ever grows past this list,
# rather than crashing entity setup over a cosmetic mismatch.
_NIGHT_LABELS = ("Tonight", "Tomorrow Night", "In Two Nights")


def _night_label(index: int) -> str:
    if index < len(_NIGHT_LABELS):
        return _NIGHT_LABELS[index]
    return f"Night +{index}"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up stargazing sensors from a config entry."""
    coordinator: StargazingCoordinator = entry.runtime_data

    entities: list[SensorEntity] = [
        StargazingNightScoreSensor(coordinator, entry, index)
        for index in range(NUM_NIGHTS_AHEAD)
    ]

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stargazing",
    )


def _breakdown_dict(breakdown: ScoreBreakdown) -> dict:
    """The nine factor sub-scores as a plain dict, matching the card's
    FACTORS key set. Shared by every `forecast` entry so the card and the
    sensors never drift apart on field names."""
    return {
        "low_cloud": breakdown.low_cloud,
        "mid_cloud": breakdown.mid_cloud,
        "high_cloud": breakdown.high_cloud,
        "dew_point_spread": breakdown.dew_point_spread,
        "visibility": breakdown.visibility,
        "jet_stream_wind": breakdown.jet_stream_wind,
        "moon_illumination": breakdown.moon_illumination,
        "precipitation_probability": breakdown.precipitation_probability,
        "wind_speed": breakdown.wind_speed,
    }


def _raw_dict(conditions: HourlyConditions) -> dict:
    """The nine factors' raw readings as a plain dict, keyed to match
    _breakdown_dict()/the card's FACTORS, so the forecast card can show
    "reading (score)" per factor. Sharing the same factor keys (rather
    than a separate prefixed naming scheme) lets the card pull the score
    and the raw value from one forecast entry without a second
    translation table. The 'raw' bundle is deliberately a sibling of the
    factor-score keys, so existing consumers that only read scores are
    unaffected."""
    return {
        "low_cloud": conditions.low_cloud_cover,
        "mid_cloud": conditions.mid_cloud_cover,
        "high_cloud": conditions.high_cloud_cover,
        "dew_point_spread": conditions.dew_point_spread,
        "visibility": conditions.visibility,
        "jet_stream_wind": conditions.jet_stream_wind_speed,
        "moon_illumination": conditions.moon_illumination,
        "precipitation_probability": conditions.precipitation_probability,
        "wind_speed": conditions.wind_speed,
    }


def _forecast_entry(hourly_score: HourlyScore) -> dict:
    """One scored hour as a dict for the `forecast` attribute -- time,
    total score, the nine factor sub-scores, and the nine raw readings
    (under `raw`). This is what the forecast card's tap-to-see-breakdown
    renders (see README.md's card design spec). The raw values let the
    card show each factor as "reading (score)" rather than score alone."""
    return {
        "time": hourly_score.time.isoformat(),
        "score": hourly_score.breakdown.total,
        "raw": _raw_dict(hourly_score.conditions),
        **_breakdown_dict(hourly_score.breakdown),
    }


class StargazingNightScoreSensor(CoordinatorEntity[StargazingCoordinator], SensorEntity):
    """One night's peak score, for the 3-day overview card."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-night"

    def __init__(
        self,
        coordinator: StargazingCoordinator,
        entry: ConfigEntry,
        night_index: int,
    ) -> None:
        super().__init__(coordinator)
        self._night_index = night_index
        self._attr_name = _night_label(night_index)
        self._attr_unique_id = f"{entry.entry_id}_night_{night_index}"
        self._attr_device_info = _device_info(entry)

    @property
    def _night(self) -> NightlyScore | None:
        """The NightlyScore this entity represents, or None if the
        coordinator hasn't produced data yet or this index is somehow
        out of range (e.g. a stale entity from a config change that
        shrank NUM_NIGHTS_AHEAD -- fail soft rather than raise)."""
        data = self.coordinator.data
        if data is None or self._night_index >= len(data):
            return None
        return data[self._night_index]

    @property
    def native_value(self) -> float | None:
        night = self._night
        if night is None:
            return None
        return night.peak_score

    @property
    def extra_state_attributes(self) -> dict:
        night = self._night
        if night is None:
            return {}

        attrs: dict = {
            "night_of": night.night_of.isoformat(),
            "hourly_scores_count": len(night.hourly_scores),
            "forecast": [_forecast_entry(hs) for hs in night.hourly_scores],
        }
        if night.window is not None:
            attrs["window_start"] = night.window.start.isoformat()
            attrs["window_end"] = night.window.end.isoformat()
            attrs["twilight_tier"] = night.window.tier.name
        else:
            attrs["window_start"] = None
            attrs["window_end"] = None
            attrs["twilight_tier"] = None
        return attrs

"""Sensor platform for stargazing.

Backs the two planned Lovelace cards (PROJECT_PRINCIPLES.md phase 7/9):

- A 3-day overview card -- one sensor per night (NUM_NIGHTS_AHEAD of
  them), state = that night's peak score, attributes carry the darkness
  window and which twilight tier was actually achieved.
- A current-conditions detail card -- one sensor whose state is the
  currently-active hour's total score (None outside any darkness
  window), attributes expose the full ScoreBreakdown so the card can
  show "why is right now a 62".

Deliberately thin, per PROJECT_PRINCIPLES.md ("Coordinator is thin
orchestration glue... no business logic lives here" extends to sensor.py
too): entities just read coordinator.data, and the module-level
current_hourly_score() helper (in coordinator.py) picks out the active
hour. All actual scoring/windowing logic stays in
score.py / astro.py / coordinator.py.
"""

from __future__ import annotations

import logging
from datetime import datetime

import homeassistant.util.dt as dt_util
from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_change
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    NUM_NIGHTS_AHEAD,
    HourlyScore,
    NightlyScore,
    StargazingCoordinator,
    UpcomingHourlyScore,
    current_hourly_score,
    upcoming_hourly_scores,
)
from .score import ScoreBreakdown

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
    entities.append(StargazingCurrentConditionsSensor(coordinator, entry))

    async_add_entities(entities)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Stargazing",
    )


def _breakdown_dict(breakdown: ScoreBreakdown) -> dict:
    """The nine factor sub-scores as a plain dict, shared by the
    current-conditions sensor's top-level attributes and each night
    sensor's per-hour `forecast` entries, so the two never drift apart
    on field names."""
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


def _forecast_entry(hourly_score: HourlyScore) -> dict:
    """One scored hour as a dict for the `forecast` attribute -- time,
    total score, and the nine factor sub-scores. This is what the
    forecast card's tap-to-see-breakdown renders (see README.md's card
    design spec)."""
    return {
        "time": hourly_score.time.isoformat(),
        "score": hourly_score.breakdown.total,
        **_breakdown_dict(hourly_score.breakdown),
    }


def _upcoming_entry(item: UpcomingHourlyScore) -> dict:
    """One future scored hour as a dict for the `upcoming` attribute --
    deliberately just time/score/night_of (not the full breakdown): this
    backs the current-conditions card's "next best hour" fallback line,
    which only needs enough to say *when* and *how good*, not why."""
    return {
        "time": item.hourly_score.time.isoformat(),
        "score": item.hourly_score.breakdown.total,
        "night_of": item.night_of.isoformat(),
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


class StargazingCurrentConditionsSensor(
    CoordinatorEntity[StargazingCoordinator], SensorEntity
):
    """The currently-active hour's full score breakdown, for the
    current-conditions detail card. State is None whenever right now
    falls outside every scored darkness window (e.g. daytime)."""

    _attr_has_entity_name = True
    _attr_name = "Current Conditions"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:telescope"

    def __init__(self, coordinator: StargazingCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_current_conditions"
        self._attr_device_info = _device_info(entry)
        self._current_score: HourlyScore | None = None
        self._upcoming: list[UpcomingHourlyScore] = []

    async def async_added_to_hass(self) -> None:
        """Compute the active hour once at startup -- this HA version's
        CoordinatorEntity.async_added_to_hass only subscribes to the
        coordinator; it does not run _handle_coordinator_update() once,
        so the initial state write would otherwise read a stale None.

        Also registers a local hour-boundary listener so the active hour
        rolls over promptly between coordinator polls (which only run
        every 30 min) -- otherwise right after the top of an hour the
        sensor could keep showing the previous hour's score. Registered
        via async_on_remove so it's torn down on unload/reload."""
        await super().async_added_to_hass()
        self._current_score, self._upcoming = self._compute_current_and_upcoming()
        self.async_on_remove(
            async_track_time_change(self.hass, self._hour_rollover, minute=0, second=0)
        )

    @callback
    def _hour_rollover(self, _now: datetime | None = None) -> None:
        """Recompute the active hour (and upcoming list, since "upcoming"
        shrinks by one entry every time an hour rolls into "current") at
        each local hour boundary and write the new state -- even when
        the coordinator hasn't polled since the last hour (see
        async_added_to_hass)."""
        self._current_score, self._upcoming = self._compute_current_and_upcoming()
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Recompute current + upcoming once per coordinator update, then
        let CoordinatorEntity write the new state -- avoids calling
        current_hourly_score()/upcoming_hourly_scores() twice
        (native_value + attributes) and evaluating dt_util.now()
        separately in each."""
        self._current_score, self._upcoming = self._compute_current_and_upcoming()
        super()._handle_coordinator_update()

    def _compute_current_and_upcoming(
        self,
    ) -> tuple[HourlyScore | None, list[UpcomingHourlyScore]]:
        data = self.coordinator.data
        if data is None:
            return None, []
        now_naive = dt_util.now().replace(tzinfo=None)
        current = current_hourly_score(data, now_naive)
        upcoming = upcoming_hourly_scores(data, now_naive)
        return current, upcoming

    @property
    def native_value(self) -> float | None:
        current = self._current_score
        if current is None:
            return None
        return current.breakdown.total

    @property
    def extra_state_attributes(self) -> dict:
        # "upcoming" is always present, even with no active hour --
        # that's precisely when the current-conditions card needs it
        # for its "come back later, best upcoming hour is..." fallback
        # (see README.md's card design spec).
        attrs: dict = {"upcoming": [_upcoming_entry(item) for item in self._upcoming]}

        current = self._current_score
        if current is None:
            return attrs

        attrs["time"] = current.time.isoformat()
        attrs.update(_breakdown_dict(current.breakdown))
        return attrs
"""Config flow for stargazing.

Two chained steps, accumulating into self._data (the flow instance stays
alive for the whole wizard, same pattern as sun_bathing's config_flow.py):

1. async_step_user -- latitude/longitude/elevation, defaulting to HA's
   home coordinates, validated with a REAL Open-Meteo call (reusing the
   already-tested client.py, not a separate validation path)
2. async_step_preset -- strict/balanced/relaxed + twilight tier
   (astronomical/nautical/civil, preferred darkness), then creates the entry

OPTIONS WIZARD (Phase 10) -- StargazingOptionsFlowHandler below runs six
sequential pages, one concern each, rather than one overwhelming form:
preset -> night type -> cloud thresholds -> sky thresholds ->
falloff spans -> weights. Same chaining pattern as the setup flow.
Saving triggers an automatic entry reload via OptionsFlowWithReload,
which rebuilds the coordinator with the new numbers (the integration has
no update listeners, which is the one precondition for using that base
class).

Prefill rule across wizard reruns (deliberate, see handler docstring):
keeping your current preset preserves stored customizations; picking a
different preset resets every tuning page to that preset's values.

Uses plain voluptuous validators (vol.Coerce, vol.In, vol.Range), not
HA's selector.* classes -- sun_bathing hit a persistent, hard-to-diagnose
"Config flow could not be loaded: 400 Bad Request / Invalid handler"
error from selector.NumberSelector/SelectSelector with translation_key
mismatches, and reverting to plain validators was what actually fixed
it. Not repeating that mistake here.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, fields
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ELEVATION, CONF_LATITUDE, CONF_LONGITUDE
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import OpenMeteoClient, OpenMeteoError
from .const import (
    CONF_PRESET,
    CONF_SCORE_CONFIG,
    CONF_TWILIGHT_TIER,
    DEFAULT_PRESET,
    DEFAULT_TWILIGHT_TIER,
    DOMAIN,
    PRESETS,
    TWILIGHT_TIER_CHOICES,
)
from .presets import (
    config_entry_to_score_config,
    get_preset_values,
    overlay_score_config,
)
from .score import FalloffSpans, PlateauEdges, ScoreWeights

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
            self._data[CONF_SCORE_CONFIG] = get_preset_values(user_input[CONF_PRESET])

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

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> StargazingOptionsFlowHandler:
        """Options entry point -- its mere presence is what makes HA show
        the gear icon on this integration's entry card."""
        return StargazingOptionsFlowHandler()


# ---------------------------------------------------------------------------
# Options-wizard schema helpers. Field name lists are derived from the
# score.py dataclasses rather than hand-copied, so adding a scoring factor
# later automatically adds it to the right page.
# ---------------------------------------------------------------------------

_CLOUD_EDGE_FIELDS = ("low_cloud_max", "mid_cloud_max", "high_cloud_max")
_EDGE_FIELDS = tuple(f.name for f in fields(PlateauEdges))
_SKY_EDGE_FIELDS = tuple(f for f in _EDGE_FIELDS if f not in _CLOUD_EDGE_FIELDS)
_SPAN_FIELDS = tuple(f.name for f in fields(FalloffSpans))  # same names as edges
_WEIGHT_FIELDS = tuple(f.name for f in fields(ScoreWeights))

# (min, max) per numeric field. Edges and spans share field names but can
# share ranges too -- each range is wide enough for both uses (e.g. the
# visibility span and the visibility floor both live inside 0..50000 m).
_FIELD_RANGES: dict[str, tuple[float, float]] = {
    "low_cloud_max": (0.0, 100.0),
    "mid_cloud_max": (0.0, 100.0),
    "high_cloud_max": (0.0, 100.0),
    "dew_point_spread_min": (0.0, 30.0),
    "visibility_min": (0.0, 50000.0),
    "jet_stream_wind_max": (0.0, 120.0),
    "moon_illumination_max": (0.0, 100.0),
    "precipitation_probability_max": (0.0, 100.0),
    "wind_speed_max": (0.0, 150.0),
}
_WEIGHT_RANGE = (0.0, 5.0)


def _numeric_schema(
    section: dict[str, float], names: tuple[str, ...], rng: dict[str, tuple[float, float]]
) -> vol.Schema:
    """One Required float field per name, prefilled from `section`."""
    return vol.Schema(
        {
            vol.Required(name, default=float(section[name])): vol.All(
                vol.Coerce(float), vol.Range(min=rng[name][0], max=rng[name][1])
            )
            for name in names
        }
    )


class StargazingOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    """Six-page options wizard, one concern per page, in order:

    1. init             -- baseline preset (decides what later pages prefill)
    2. night            -- preferred darkness / twilight tier
    3. cloud_thresholds -- low/mid/high plateau ceilings
    4. sky_thresholds   -- dew spread/visibility/jet/moon/precip/wind edges
    5. falloff_spans    -- distance from each edge to a score of 0
    6. weights          -- 0-5 importance sliders

    Prefill rule across reruns (deliberate): keeping your current preset
    keeps whatever is stored (entry.data overlaid by saved options), so
    re-running the wizard to tweak one page preserves earlier tuning on
    the others. Choosing a DIFFERENT preset resets every tuning page to
    that preset's values -- switching preset means you want its numbers.

    Note: config_entry comes from the base-class property (auto-resolved
    from the flow handler id); it must not be taken in __init__.
    """

    def __init__(self) -> None:
        self._options: dict[str, Any] = {}
        self._working: dict[str, dict[str, float]] = {}
        self._preset_changed = False

    @property
    def _current_preset(self) -> str:
        entry = self.config_entry
        return entry.options.get(CONF_PRESET, entry.data.get(CONF_PRESET, DEFAULT_PRESET))

    def _effective_score_config(self) -> dict:
        """Currently-live scoring values: data layer under options layer."""
        entry = self.config_entry
        return overlay_score_config(
            entry.data.get(CONF_SCORE_CONFIG), entry.options.get(CONF_SCORE_CONFIG)
        )

    def _load_working(self, preset_name: str) -> None:
        """Normalize the edit buffer into complete sections via the dataclass
        defaults, so every later page has all its keys to prefill from."""
        source = (
            get_preset_values(preset_name)
            if self._preset_changed
            else self._effective_score_config()
        )
        edges, spans, weights = config_entry_to_score_config(source)
        self._working = {
            "edges": asdict(edges),
            "spans": asdict(spans),
            "weights": asdict(weights),
        }

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            chosen = user_input[CONF_PRESET]
            self._preset_changed = chosen != self._current_preset
            self._options[CONF_PRESET] = chosen
            self._load_working(chosen)
            return await self.async_step_night()

        schema = vol.Schema(
            {vol.Required(CONF_PRESET, default=self._current_preset): vol.In(PRESETS)}
        )
        return self.async_show_form(step_id="init", data_schema=schema)

    async def async_step_night(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._options[CONF_TWILIGHT_TIER] = user_input[CONF_TWILIGHT_TIER]
            return await self.async_step_cloud_thresholds()

        entry = self.config_entry
        current_tier = entry.options.get(
            CONF_TWILIGHT_TIER, entry.data.get(CONF_TWILIGHT_TIER, DEFAULT_TWILIGHT_TIER)
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_TWILIGHT_TIER, default=current_tier): vol.In(
                    list(TWILIGHT_TIER_CHOICES)
                )
            }
        )
        return self.async_show_form(step_id="night", data_schema=schema)

    async def async_step_cloud_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._working["edges"].update(user_input)
            return await self.async_step_sky_thresholds()

        return self.async_show_form(
            step_id="cloud_thresholds",
            data_schema=_numeric_schema(
                self._working["edges"], _CLOUD_EDGE_FIELDS, _FIELD_RANGES
            ),
        )

    async def async_step_sky_thresholds(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._working["edges"].update(user_input)
            return await self.async_step_falloff_spans()

        return self.async_show_form(
            step_id="sky_thresholds",
            data_schema=_numeric_schema(
                self._working["edges"], _SKY_EDGE_FIELDS, _FIELD_RANGES
            ),
        )

    async def async_step_falloff_spans(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            self._working["spans"].update(user_input)
            return await self.async_step_weights()

        return self.async_show_form(
            step_id="falloff_spans",
            data_schema=_numeric_schema(
                self._working["spans"], _SPAN_FIELDS, _FIELD_RANGES
            ),
        )

    async def async_step_weights(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        if user_input is not None:
            self._working["weights"].update(user_input)
            # OptionsFlowWithReload reloads the entry automatically after
            # this returns, rebuilding the coordinator with these numbers.
            return self.async_create_entry(
                title="",
                data={**self._options, CONF_SCORE_CONFIG: self._working},
            )

        return self.async_show_form(
            step_id="weights",
            data_schema=_numeric_schema(
                self._working["weights"],
                _WEIGHT_FIELDS,
                {name: _WEIGHT_RANGE for name in _WEIGHT_FIELDS},
            ),
        )
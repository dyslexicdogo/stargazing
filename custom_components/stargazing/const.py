"""Shared constants for the stargazing integration."""

from astral import Depression

DOMAIN = "stargazing"

CONF_PRESET = "preset"
CONF_TWILIGHT_TIER = "twilight_tier"

PRESET_STRICT = "strict"
PRESET_BALANCED = "balanced"
PRESET_RELAXED = "relaxed"
PRESETS = (PRESET_STRICT, PRESET_BALANCED, PRESET_RELAXED)
DEFAULT_PRESET = PRESET_BALANCED

TIER_ASTRONOMICAL_ONLY = "astronomical_only"
TIER_NAUTICAL_MINIMUM = "nautical_minimum"
TIER_CIVIL_MINIMUM = "civil_minimum"

# Maps a user-facing "minimum acceptable darkness" choice to the actual
# fallback chain get_darkness_window() tries, in order. See astro.py --
# this was designed to support exactly this config option back in Phase 4.
TWILIGHT_TIER_CHOICES: dict[str, tuple[Depression, ...]] = {
    TIER_ASTRONOMICAL_ONLY: (Depression.ASTRONOMICAL,),
    TIER_NAUTICAL_MINIMUM: (Depression.ASTRONOMICAL, Depression.NAUTICAL),
    TIER_CIVIL_MINIMUM: (Depression.ASTRONOMICAL, Depression.NAUTICAL, Depression.CIVIL),
}
DEFAULT_TWILIGHT_TIER = TIER_CIVIL_MINIMUM  # matches the "never miss a window" preference
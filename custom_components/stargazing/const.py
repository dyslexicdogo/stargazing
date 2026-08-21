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

TIER_ASTRONOMICAL = "astronomical"
TIER_NAUTICAL = "nautical"
TIER_CIVIL = "civil"

# Maps a user-facing "preferred darkness" choice to the fallback chain
# get_darkness_window() tries, in order -- PREFERRED depth first, then
# progressively shallower tiers, so a night is never skipped just because
# the darkest tier is unreachable (e.g. Inverness's summer solstice, where
# astronomical/nautical darkness don't occur). "astronomical" prefers the
# darkest sky but accepts nautical/civil when needed; "civil" is the
# shallowest, longest window and has nothing further to fall back to.
# See PROJECT_PRINCIPLES.md / README "Twilight tiers".
TWILIGHT_TIER_CHOICES: dict[str, tuple[Depression, ...]] = {
    TIER_ASTRONOMICAL: (Depression.ASTRONOMICAL, Depression.NAUTICAL, Depression.CIVIL),
    TIER_NAUTICAL: (Depression.NAUTICAL, Depression.CIVIL),
    TIER_CIVIL: (Depression.CIVIL,),
}
DEFAULT_TWILIGHT_TIER = TIER_ASTRONOMICAL  # darkest-first, never miss a window

# Lovelace card resources this integration ships from www/. Keyed by
# filename so the registration loop in __init__.py can add more cards
# later without touching the loop itself -- just add an entry here.
CARD_RESOURCES: dict[str, str] = {
    "stargazing-forecast-card.js": f"/{DOMAIN}/stargazing-forecast-card.js",
}
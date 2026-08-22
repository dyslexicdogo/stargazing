"""Shared constants for the stargazing integration."""

from astral import Depression

DOMAIN = "stargazing"

CONF_PRESET = "preset"
CONF_TWILIGHT_TIER = "twilight_tier"
# Key holding the {"edges"/"spans"/"weights"} scoring dict. Lives in
# entry.data (written by the config flow's preset step) AND in
# entry.options (written by the options wizard); __init__.py overlays
# options over data per-key via presets.overlay_score_config().
CONF_SCORE_CONFIG = "score_config"

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

# --- Notifications (Phase 11) --------------------------------------------
# All four live in entry.options (written by the options wizard's two
# notification pages); _notify_settings() in __init__.py reads them back
# with these defaults as the fallback, options winning over data.
CONF_NOTIFY_ENABLED = "notify_enabled"
CONF_NOTIFY_SCORE_THRESHOLD = "notify_score_threshold"
CONF_NOTIFY_CHECK_TIME = "notify_check_time"  # local wall clock, "HH:MM"
CONF_NOTIFY_TARGET = "notify_target"  # a notify.* entity id

DEFAULT_NOTIFY_ENABLED = False
DEFAULT_NOTIFY_SCORE_THRESHOLD = 70.0
DEFAULT_NOTIFY_CHECK_TIME = "19:00"

# Lovelace card resources this integration ships from www/. Keyed by
# filename so the registration loop in __init__.py can add more cards
# later without touching the loop itself -- just add an entry here.
CARD_RESOURCES: dict[str, str] = {
    "stargazing-forecast-card.js": f"/{DOMAIN}/stargazing-forecast-card.js",
}
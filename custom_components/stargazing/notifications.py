"""Notification scheduling for stargazing (Phase 11).

Separation of concerns, same as everywhere else in this integration:

- ``parse_check_time`` / ``should_notify`` / ``build_notification`` are
  pure functions -- no HA behaviour, trivially unit-testable.
- :class:`StargazingNotifier` is thin glue only: arm a daily timer at the
  user's chosen check time, ask the coordinator for fresh data, compare
  tonight's peak score against the threshold, and fire ONE direct call
  to the stored ``domain.service`` target. No business logic beyond that
  comparison lives here.

Target model (adopted from sun_bathing after real-world testing): the
stored target is a full ``domain.service`` string -- e.g.
``notify.mobile_app_pixel`` or ``persistent_notification.create`` --
called directly at send time. Discovery therefore scans the SERVICE
registry (hass.services.async_services()["notify"]), which lists notify
platforms even when they expose no state-machine entity, and always
offers HA's built-in ``persistent_notification.create`` so the options
picker is never empty and notifications always have somewhere to go.
A bare legacy value with no dot (e.g. ``mobile_app_pixel``) is treated
as an object under the notify domain for backwards compatibility.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_change

from .const import (
    CONF_NOTIFY_CHECK_TIME,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_SCORE_THRESHOLD,
    CONF_NOTIFY_TARGET,
    DEFAULT_NOTIFY_CHECK_TIME,
    DEFAULT_NOTIFY_ENABLED,
    DEFAULT_NOTIFY_SCORE_THRESHOLD,
)

if TYPE_CHECKING:
    from .coordinator import StargazingCoordinator

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"
ATTR_MESSAGE = "message"
ATTR_TITLE = "title"
# HA's built-in notification sink -- exists on every install, so the
# options wizard's target picker can never be empty (sun_bathing trick).
FALLBACK_TARGET = "persistent_notification.create"

# Strict local "HH:MM", 00:00-23:59; the hour may be written unpadded
# ("9:30") but minutes must be two digits. Shared by the options wizard
# (vol.Match) and parse_check_time so validation cannot drift apart.
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")


def parse_check_time(value: str) -> time:
    """Turn "19:30" into ``time(19, 30)``; anything else is a ValueError."""
    match = TIME_PATTERN.match(value.strip())
    if not match:
        raise ValueError(f"Invalid check time {value!r}; expected HH:MM")
    return time(int(match.group(1)), int(match.group(2)))


def should_notify(peak_score: float | None, threshold: float) -> bool:
    """True only when tonight has a real scored peak at or above threshold.

    A ``None`` peak means either "no darkness window tonight" or "the
    coordinator has no data yet" -- neither is worth pinging anyone about,
    so both stay silent rather than sending a confusing empty alert.
    """
    return peak_score is not None and peak_score >= threshold


def build_notification(
    night_of: date, peak_score: float, threshold: float
) -> dict[str, str]:
    """Compose the title/message payload sent to the notify entity."""
    return {
        ATTR_TITLE: "Stargazing tonight",
        ATTR_MESSAGE: (
            f"{night_of.strftime('%a %d %b')}: peak score {peak_score:.0f}"
            f"/100 (your threshold: {threshold:.0f}). "
            "Worth getting the scope out!"
        ),
    }


def list_notify_entities(hass: HomeAssistant) -> list[str]:
    """Notify targets for the options picker (sun_bathing's model).

    Scans the SERVICE registry rather than the state machine: notify
    platforms register one service per target even when they expose no
    matching entity state, so ``hass.states`` misses real targets. HA's
    built-in ``persistent_notification.create`` always leads the list so
    the picker is never empty and delivery always has somewhere to go.
    """
    services = sorted(hass.services.async_services().get(NOTIFY_DOMAIN, {}))
    return [FALLBACK_TARGET, *(f"{NOTIFY_DOMAIN}.{name}" for name in services)]


class StargazingNotifier:
    """Arms the daily check and sends at most one notification per day.

    Deliberately dumb: it holds a snapshot of the (already merged)
    settings, so rerunning setup -- which is what saving the options
    wizard does, via OptionsFlowWithReload's automatic reload -- re-arms
    it from scratch with whatever the user last chose.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: StargazingCoordinator,
        settings: dict[str, Any],
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._settings = settings
        self._unsub: Any = None

    @property
    def enabled(self) -> bool:
        return bool(self._settings.get(CONF_NOTIFY_ENABLED, DEFAULT_NOTIFY_ENABLED))

    def arm(self) -> bool:
        """Schedule the daily check. Returns False (with a logged reason)
        when disabled or misconfigured, so setup skips wiring an unload
        handler for a timer that was never created."""
        if not self.enabled:
            return False
        raw_time = (
            self._settings.get(CONF_NOTIFY_CHECK_TIME) or DEFAULT_NOTIFY_CHECK_TIME
        )
        try:
            check_at = parse_check_time(raw_time)
        except ValueError:
            _LOGGER.warning(
                "Stargazing notifications enabled but check time %r is not "
                "HH:MM -- staying disabled until fixed in options",
                raw_time,
            )
            return False
        if not self._settings.get(CONF_NOTIFY_TARGET):
            _LOGGER.warning(
                "Stargazing notifications enabled but no notify target is "
                "set -- staying disabled until one is picked in options"
            )
            return False
        self._unsub = async_track_time_change(
            self._hass,
            self._async_check_and_send,
            hour=check_at.hour,
            minute=check_at.minute,
            second=0,
        )
        return True

    def unsub(self) -> None:
        """Cancel the timer; safe to call twice. Registered via
        entry.async_on_unload so unload/reload always cleans up."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None

    async def _async_check_and_send(self, now: datetime | None = None) -> bool:
        """One evaluation pass. Returns whether a notification was sent.

        The optional ``now`` just matches async_track_time_change's
        callback signature -- the wall-clock moment is irrelevant here
        because the scheduler already did the time gating.
        """
        if not self.enabled:
            return False
        # The check time can sit up to 24h after the last poll; refresh
        # first so the decision uses tonight's actual forecast rather
        # than a stale cache.
        await self._coordinator.async_request_refresh()
        nights = self._coordinator.data or []
        peak = nights[0].peak_score if nights else None
        threshold = float(
            self._settings.get(
                CONF_NOTIFY_SCORE_THRESHOLD, DEFAULT_NOTIFY_SCORE_THRESHOLD
            )
        )
        if not should_notify(peak, threshold):
            _LOGGER.debug(
                "Stargazing: tonight's peak (%s) below threshold %.0f -- "
                "no notification",
                peak,
                threshold,
            )
            return False
        target = str(self._settings[CONF_NOTIFY_TARGET]).strip()
        if "." not in target:
            # Bare legacy value ("mobile_app_test") -> assume notify domain.
            target = f"{NOTIFY_DOMAIN}.{target}"
        domain, _, service = target.rpartition(".")
        payload = build_notification(nights[0].night_of, peak, threshold)
        await self._hass.services.async_call(domain, service, payload, blocking=False)
        _LOGGER.info(
            "Stargazing: notified via %s (tonight's peak %.0f >= %.0f)",
            self._settings[CONF_NOTIFY_TARGET],
            peak,
            threshold,
        )
        return True
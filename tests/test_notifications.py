"""Unit tests for notifications.py (Phase 11).

Pure helpers (parse/decide/compose) get plain inputs. StargazingNotifier
gets minimal hand-built fakes -- a coordinator stand-in exposing just
``data`` + ``async_request_refresh``, and a hass stand-in exposing just
the service registry (``services.async_services``) plus
``services.async_call`` -- per PROJECT_PRINCIPLES' test discipline
(fakes over mocks, never real network).
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.stargazing.const import (
    CONF_NOTIFY_CHECK_TIME,
    CONF_NOTIFY_ENABLED,
    CONF_NOTIFY_SCORE_THRESHOLD,
    CONF_NOTIFY_TARGET,
)
from custom_components.stargazing.notifications import (
    FALLBACK_TARGET,
    NOTIFY_DOMAIN,
    StargazingNotifier,
    build_notification,
    list_notify_entities,
    parse_check_time,
    should_notify,
)

NIGHT_OF = date(2026, 8, 22)


def make_settings(**overrides):
    settings = {
        CONF_NOTIFY_ENABLED: True,
        CONF_NOTIFY_SCORE_THRESHOLD: 70.0,
        CONF_NOTIFY_CHECK_TIME: "19:30",
        CONF_NOTIFY_TARGET: "notify.mobile_app_test",
    }
    settings.update(overrides)
    return settings


class FakeCoordinator:
    def __init__(self, nights):
        self.data = nights
        self.async_request_refresh = AsyncMock(return_value=True)


def make_hass(notify_services=()):
    """hass stand-in with only what the notifier/list helpers touch.

    Mirrors the SERVICE registry (hass.services.async_services), which is
    what target discovery actually reads -- notify platforms register
    services even when they expose no state-machine entity.
    """
    services = SimpleNamespace(
        async_call=AsyncMock(return_value=True),
        async_services=lambda: {
            NOTIFY_DOMAIN: {name: object() for name in notify_services}
        },
    )
    return SimpleNamespace(services=services)


def night(peak, night_of=NIGHT_OF):
    return SimpleNamespace(night_of=night_of, peak_score=peak)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("19:30", (19, 30)),
        ("0:05", (0, 5)),
        ("00:05", (0, 5)),
        ("23:59", (23, 59)),
        (" 7:15 ", (7, 15)),  # surrounding whitespace tolerated
    ],
)
def test_parse_check_time_valid(raw, expected):
    from datetime import time

    assert parse_check_time(raw) == time(*expected)


@pytest.mark.parametrize(
    "raw",
    ["24:00", "19:5", "19:60", "ab:cd", "", "1930", "19:30:00"],
)
def test_parse_check_time_invalid_raises(raw):
    with pytest.raises(ValueError):
        parse_check_time(raw)


@pytest.mark.parametrize(
    ("peak", "threshold", "expected"),
    [
        (None, 70.0, False),  # no window tonight / no data yet
        (69.9, 70.0, False),
        (70.0, 70.0, True),  # boundary is inclusive
        (95.0, 70.0, True),
    ],
)
def test_should_notify_boundary(peak, threshold, expected):
    assert should_notify(peak, threshold) is expected


def test_build_notification_payload():
    payload = build_notification(NIGHT_OF, 82.4, 70.0)
    assert "82" in payload["message"] and "/100" in payload["message"]
    assert "70" in payload["message"]
    assert NIGHT_OF.strftime("%a %d %b") in payload["message"]
    assert payload["title"]


def test_list_notify_targets_registry_scan_with_fallback_first():
    hass = make_hass(notify_services=["mobile_app_b", "mobile_app_a"])
    assert list_notify_entities(hass) == [
        FALLBACK_TARGET,
        "notify.mobile_app_a",
        "notify.mobile_app_b",
    ]


def test_list_notify_targets_never_empty():
    """Fresh installs with no notify platform still get the built-in
    sink, so the options wizard's picker always has a choice."""
    assert list_notify_entities(make_hass()) == [FALLBACK_TARGET]


# ---------------------------------------------------------------------------
# StargazingNotifier.arm -- configuration gating + timer wiring
# ---------------------------------------------------------------------------


@patch("custom_components.stargazing.notifications.async_track_time_change")
async def test_arm_disabled_creates_no_timer(track):
    notifier = StargazingNotifier(
        make_hass(), FakeCoordinator([]), make_settings(**{CONF_NOTIFY_ENABLED: False})
    )
    assert notifier.arm() is False
    track.assert_not_called()


@patch("custom_components.stargazing.notifications.async_track_time_change")
async def test_arm_bad_time_stays_disabled_and_warns(track, caplog):
    notifier = StargazingNotifier(
        make_hass(), FakeCoordinator([]), make_settings(**{CONF_NOTIFY_CHECK_TIME: "99:99"})
    )
    assert notifier.arm() is False
    track.assert_not_called()
    assert "HH:MM" in caplog.text


@patch("custom_components.stargazing.notifications.async_track_time_change")
async def test_arm_missing_target_stays_disabled_and_warns(track, caplog):
    notifier = StargazingNotifier(
        make_hass(), FakeCoordinator([]), make_settings(**{CONF_NOTIFY_TARGET: None})
    )
    assert notifier.arm() is False
    track.assert_not_called()
    assert "target" in caplog.text


@patch("custom_components.stargazing.notifications.async_track_time_change")
async def test_arm_schedules_daily_timer_and_unsub_cancels(track):
    cancel = MagicMock()
    track.return_value = cancel
    notifier = StargazingNotifier(make_hass(), FakeCoordinator([]), make_settings())

    assert notifier.arm() is True
    track.assert_called_once()
    _, kwargs = track.call_args
    assert kwargs["hour"] == 19
    assert kwargs["minute"] == 30
    assert kwargs["second"] == 0

    notifier.unsub()
    cancel.assert_called_once()
    notifier.unsub()  # idempotent -- must not raise or double-cancel
    cancel.assert_called_once()


# ---------------------------------------------------------------------------
# StargazingNotifier._async_check_and_send -- the daily decision
# ---------------------------------------------------------------------------


async def test_check_sends_when_peak_meets_threshold():
    hass = make_hass()
    coord = FakeCoordinator([night(82.4)])
    notifier = StargazingNotifier(hass, coord, make_settings())

    assert await notifier._async_check_and_send() is True
    coord.async_request_refresh.assert_awaited_once()  # freshest forecast first
    hass.services.async_call.assert_awaited_once()
    args, kwargs = hass.services.async_call.call_args
    domain, service, payload = args[0], args[1], args[2]
    # The stored "domain.service" target is called DIRECTLY -- this is
    # what makes persistent_notification.create (which has no entity
    # state at all) deliverable.
    assert (domain, service) == (NOTIFY_DOMAIN, "mobile_app_test")
    assert "entity_id" not in payload
    assert "82" in payload["message"]
    assert kwargs["blocking"] is False


async def test_check_fallback_target_reaches_persistent_notification():
    hass = make_hass()
    coord = FakeCoordinator([night(80.0)])
    settings = make_settings(**{CONF_NOTIFY_TARGET: FALLBACK_TARGET})
    notifier = StargazingNotifier(hass, coord, settings)

    assert await notifier._async_check_and_send() is True
    domain, service, _payload = hass.services.async_call.call_args.args
    assert (domain, service) == ("persistent_notification", "create")


async def test_check_legacy_bare_target_assumes_notify_domain():
    hass = make_hass()
    coord = FakeCoordinator([night(80.0)])
    settings = make_settings(**{CONF_NOTIFY_TARGET: "mobile_app_test"})
    notifier = StargazingNotifier(hass, coord, settings)

    assert await notifier._async_check_and_send() is True
    domain, service, _payload = hass.services.async_call.call_args.args
    assert (domain, service) == (NOTIFY_DOMAIN, "mobile_app_test")


async def test_check_skips_below_threshold():
    hass = make_hass()
    coord = FakeCoordinator([night(55.0)])
    notifier = StargazingNotifier(hass, coord, make_settings())

    assert await notifier._async_check_and_send() is False
    hass.services.async_call.assert_not_awaited()


async def test_check_skips_when_no_data_yet():
    hass = make_hass()
    coord = FakeCoordinator([])  # coordinator has no scored nights at all
    notifier = StargazingNotifier(hass, coord, make_settings())

    assert await notifier._async_check_and_send() is False
    hass.services.async_call.assert_not_awaited()


async def test_check_short_circuits_when_disabled():
    hass = make_hass()
    coord = FakeCoordinator([night(99.0)])
    notifier = StargazingNotifier(
        hass, coord, make_settings(**{CONF_NOTIFY_ENABLED: False})
    )

    assert await notifier._async_check_and_send() is False
    coord.async_request_refresh.assert_not_awaited()  # not even a poll
    hass.services.async_call.assert_not_awaited()


async def test_check_boundary_inclusive_sends_at_exact_threshold():
    hass = make_hass()
    coord = FakeCoordinator([night(70.0)])
    notifier = StargazingNotifier(hass, coord, make_settings())

    assert await notifier._async_check_and_send() is True
    hass.services.async_call.assert_awaited_once()
"""Smoke test: confirms pytest-homeassistant-custom-component's hass
fixture actually works in this environment, before writing real
coordinator tests against it. If this fails, nothing coordinator-related
will work either -- fix this first.
"""


async def test_hass_fixture_instantiates(hass):
    assert hass is not None


async def test_hass_can_set_and_read_state(hass):
    hass.states.async_set("stargazing.smoke_test", "42")
    state = hass.states.get("stargazing.smoke_test")
    assert state is not None
    assert state.state == "42"


async def test_hass_config_has_a_timezone(hass):
    assert hass.config.time_zone is not None
"""Shared test fixtures.

pytest_plugins registers pytest-homeassistant-custom-component's fixtures
(hass, etc). enable_custom_integrations is required specifically for
testing code under custom_components/ -- without it, HA's test harness
assumes core-only integrations and won't discover this one.
"""

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield
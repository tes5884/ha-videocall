"""Test fixtures for the home-hardened videocall fork.

Run (no local HA needed):
  docker run --rm -v "$PWD":/src -w /src vc-hatest pytest -q tests
where vc-hatest = python:3.14-slim + pytest-homeassistant-custom-component.
"""

from __future__ import annotations

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def videocall_options() -> dict:
    """Override in a test module to set up with specific entry options."""
    return {}


@pytest.fixture
async def setup_videocall(hass, videocall_options):
    """The real integration, set up with its HA dependencies (http, lovelace…)."""
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    assert await async_setup_component(hass, "http", {})
    entry = MockConfigEntry(domain="videocall", options=videocall_options)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry

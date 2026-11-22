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

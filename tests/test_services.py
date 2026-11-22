"""Blocker 3: videocall.ring is gone."""

from __future__ import annotations

import pytest
from homeassistant.exceptions import ServiceNotFound

from custom_components.videocall.const import DOMAIN


async def test_ring_service_not_registered(hass, setup_videocall):
    assert not hass.services.has_service(DOMAIN, "ring")
    with pytest.raises(ServiceNotFound):
        await hass.services.async_call(
            DOMAIN, "ring",
            {"name": "Front Door", "webrtc_url": "https://evil.example/api/webrtc",
             "stream": "x", "all": True},
            blocking=True,
        )


async def test_ring_not_in_services_yaml():
    from pathlib import Path

    import yaml

    p = Path(__file__).parents[1] / "custom_components" / DOMAIN / "services.yaml"
    assert "ring" not in yaml.safe_load(p.read_text())


async def test_hangup_and_prune_registered(hass, setup_videocall):
    assert hass.services.has_service(DOMAIN, "hangup")
    assert hass.services.has_service(DOMAIN, "prune_endpoints")

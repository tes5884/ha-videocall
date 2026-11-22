"""Blocker 2 (server half): LAN-only ICE by default, [] honoured."""

from __future__ import annotations

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.videocall import VideocallData
from custom_components.videocall.const import DOMAIN, OPT_ICE_SERVERS


def _data(hass, **opts) -> VideocallData:
    entry = MockConfigEntry(domain=DOMAIN, options=opts)
    entry.add_to_hass(hass)
    return VideocallData(hass, entry)


async def test_default_has_no_public_stun(hass):
    assert _data(hass).ice_servers == []


async def test_empty_and_blank_are_lan_only(hass):
    for raw in ("", "[]", "null", "not json"):
        assert _data(hass, **{OPT_ICE_SERVERS: raw}).ice_servers == [], raw


async def test_explicit_list_used(hass):
    servers = _data(hass, **{OPT_ICE_SERVERS: '[{"urls":"stun:10.0.0.1:3478"}]'}).ice_servers
    assert servers == [{"urls": "stun:10.0.0.1:3478"}]

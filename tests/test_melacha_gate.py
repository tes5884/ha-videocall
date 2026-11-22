"""Blocker 4: Shabbos/Yom Tov gate — fail closed, server-side.

Also an end-to-end websocket check that an un-allowlisted drop-in rings
instead of auto-answering (blocker 1).
"""

from __future__ import annotations

import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.videocall import VideocallData
from custom_components.videocall.const import DOMAIN, MELACHA_GATE_ENTITY
from custom_components.videocall.gate import melacha_clear
from custom_components.videocall.models import Call

CLOSED_STATES = ["on", "unknown", "unavailable", "", "true"]


async def test_gate_missing_entity_is_closed(hass):
    assert hass.states.get(MELACHA_GATE_ENTITY) is None
    assert melacha_clear(hass) is False


@pytest.mark.parametrize("state", CLOSED_STATES)
async def test_gate_closed_states(hass, state):
    hass.states.async_set(MELACHA_GATE_ENTITY, state)
    assert melacha_clear(hass) is False


async def test_gate_open_only_on_off(hass):
    hass.states.async_set(MELACHA_GATE_ENTITY, "off")
    assert melacha_clear(hass) is True


# --- phone pushes ----------------------------------------------------------

def _ringer(hass):
    entry = MockConfigEntry(domain=DOMAIN, options={})
    entry.add_to_hass(hass)
    return VideocallData(hass, entry).mobile


def _call() -> Call:
    return Call(call_id="c" * 16, caller_id="tablet-client-0001", media="video")


CALLER = {"name": "Kitchen tablet", "user_name": "tablet"}


@pytest.mark.parametrize("state", [None, *CLOSED_STATES])
async def test_ring_push_blocked_when_gate_closed(hass, state):
    if state is not None:
        hass.states.async_set(MELACHA_GATE_ENTITY, state)
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    failed = await _ringer(hass).async_send_ring(_call(), CALLER, ["mobile_app_pixel"])
    await hass.async_block_till_done()
    assert calls == []
    assert failed == {"mobile_app_pixel"}  # never counted as ringing → no missed note


@pytest.mark.parametrize("state", [None, *CLOSED_STATES])
async def test_missed_push_blocked_when_gate_closed(hass, state):
    if state is not None:
        hass.states.async_set(MELACHA_GATE_ENTITY, state)
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    await _ringer(hass).async_send_missed(_call(), CALLER, ["mobile_app_pixel"])
    await hass.async_block_till_done()
    assert calls == []


async def test_pushes_go_out_when_gate_off(hass):
    hass.states.async_set(MELACHA_GATE_ENTITY, "off")
    calls = async_mock_service(hass, "notify", "mobile_app_pixel")
    ringer = _ringer(hass)
    assert await ringer.async_send_ring(_call(), CALLER, ["mobile_app_pixel"]) == set()
    await ringer.async_send_missed(_call(), CALLER, ["mobile_app_pixel"])
    await hass.async_block_till_done()
    assert len(calls) == 2


# --- end to end over the websocket -----------------------------------------

async def _register(client, msg_id: int, client_id: str) -> None:
    await client.send_json({"id": msg_id, "type": "videocall/register", "client_id": client_id})
    res = await client.receive_json()
    assert res["success"], res


async def _two_clients(hass, hass_ws_client):
    tablet = await hass_ws_client(hass)
    pixel = await hass_ws_client(hass)
    await _register(tablet, 1, "tablet-client-0001")
    await _register(pixel, 1, "pixel-client-0001")
    return tablet, pixel


def _invite(msg_id: int, drop_in: bool = False) -> dict:
    return {
        "id": msg_id, "type": "videocall/invite", "call_id": f"call-{msg_id:08d}",
        "caller_client_id": "tablet-client-0001",
        "target": {"type": "endpoint", "id": "pixel-client-0001"},
        "drop_in": drop_in,
    }


@pytest.mark.parametrize("state", [None, "on", "unknown", "unavailable"])
async def test_invite_refused_when_gate_closed(hass, hass_ws_client, setup_videocall, state):
    if state is not None:
        hass.states.async_set(MELACHA_GATE_ENTITY, state)
    tablet, pixel = await _two_clients(hass, hass_ws_client)
    await tablet.send_json(_invite(2))
    res = await tablet.receive_json()
    assert res["success"] is False
    assert res["error"]["code"] == "issur_melacha"
    assert list(hass.data[DOMAIN].calls.active()) == []


async def test_invite_rings_when_gate_off_and_drop_in_not_auto_answered(
    hass, hass_ws_client, setup_videocall
):
    hass.states.async_set(MELACHA_GATE_ENTITY, "off")
    tablet, pixel = await _two_clients(hass, hass_ws_client)
    await tablet.send_json(_invite(2, drop_in=True))
    res = await tablet.receive_json()
    assert res["success"], res
    assert res["result"]["ringing"] == ["pixel-client-0001"]
    # the Pixel gets a normal ring: drop_in False (default options = no drop-in)
    while True:
        evt = await pixel.receive_json()
        if evt.get("type") == "event" and evt["event"].get("event_type") == "ring":
            break
    assert evt["event"]["drop_in"] is False

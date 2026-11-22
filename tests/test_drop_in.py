"""Blocker 1: drop-in is deny-by-default and needs a server-side allowlist."""

from __future__ import annotations

import json

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.videocall import VideocallData
from custom_components.videocall.const import (
    DEFAULT_ALLOW_DROP_IN,
    DOMAIN,
    OPT_ALLOW_DROP_IN,
    OPT_DROP_IN_ALLOWLIST,
)
from custom_components.videocall.models import Endpoint


def _data(hass, **opts) -> VideocallData:
    entry = MockConfigEntry(domain=DOMAIN, options=opts)
    entry.add_to_hass(hass)
    return VideocallData(hass, entry)


def _tablet(**kw) -> Endpoint:
    return Endpoint(
        client_id="tablet-client-0001", device_id="dev_tablet",
        user_id="uid_tablet", user_name="tablet", **kw,
    )


def _tzvi() -> Endpoint:
    return Endpoint(client_id="pixel-client-0001", device_id="dev_pixel",
                    user_id="uid_tzvi", user_name="Tzvi")


def _acl(d: dict) -> str:
    return json.dumps(d)


def test_default_is_off():
    assert DEFAULT_ALLOW_DROP_IN is False


async def test_default_options_deny(hass):
    data = _data(hass)
    assert data.allow_drop_in is False
    assert not data.drop_in_allowed(_tablet(), _tzvi())


async def test_master_on_without_allowlist_denies(hass):
    data = _data(hass, **{OPT_ALLOW_DROP_IN: True})
    assert not data.drop_in_allowed(_tablet(), _tzvi())


async def test_empty_allowlist_denies(hass):
    for acl in ("{}", _acl({"tablet-client-0001": []}), ""):
        data = _data(hass, **{OPT_ALLOW_DROP_IN: True, OPT_DROP_IN_ALLOWLIST: acl})
        assert not data.drop_in_allowed(_tablet(), _tzvi()), acl


async def test_allowlisted_caller_allowed(hass):
    for acl in (
        {"tablet-client-0001": ["uid_tzvi"]},          # by client_id + user_id
        {"dev_tablet": ["tzvi"]},                       # by device_id + name, case-insensitive
        {"TABLET-CLIENT-0001": ["UID_TZVI"]},
    ):
        data = _data(hass, **{OPT_ALLOW_DROP_IN: True, OPT_DROP_IN_ALLOWLIST: _acl(acl)})
        assert data.drop_in_allowed(_tablet(), _tzvi()), acl


async def test_allowlist_is_per_target_and_per_caller(hass):
    data = _data(hass, **{
        OPT_ALLOW_DROP_IN: True,
        OPT_DROP_IN_ALLOWLIST: _acl({"tablet-client-0001": ["uid_tzvi"]}),
    })
    # the tablet user may NOT drop in on the Pixel (Pixel isn't listed)
    assert not data.drop_in_allowed(_tzvi(), _tablet())
    # another household user may not drop in on the tablet
    other = Endpoint(client_id="other-client-01", user_id="uid_kid", user_name="kid")
    assert not data.drop_in_allowed(_tablet(), other)


async def test_master_off_beats_allowlist(hass):
    data = _data(hass, **{
        OPT_ALLOW_DROP_IN: False,
        OPT_DROP_IN_ALLOWLIST: _acl({"tablet-client-0001": ["uid_tzvi"]}),
    })
    assert not data.drop_in_allowed(_tablet(), _tzvi())


async def test_device_list_cannot_widen(hass):
    """Upstream bug: a non-empty device-declared list overrode the global off."""
    target = _tablet(drop_in_allow=["uid_tzvi"])
    assert not _data(hass).drop_in_allowed(target, _tzvi())
    assert not _data(hass, **{OPT_ALLOW_DROP_IN: True}).drop_in_allowed(target, _tzvi())


async def test_device_list_can_narrow(hass):
    opts = {
        OPT_ALLOW_DROP_IN: True,
        OPT_DROP_IN_ALLOWLIST: _acl({"tablet-client-0001": ["uid_tzvi"]}),
    }
    assert not _data(hass, **opts).drop_in_allowed(_tablet(drop_in_allow=[]), _tzvi())
    assert not _data(hass, **opts).drop_in_allowed(
        _tablet(drop_in_allow=["someone_else"]), _tzvi()
    )
    assert _data(hass, **opts).drop_in_allowed(_tablet(drop_in_allow=["Tzvi"]), _tzvi())


async def test_malformed_allowlist_denies(hass):
    for acl in ("not json", "[]", "null", _acl({"tablet-client-0001": "uid_tzvi"})):
        data = _data(hass, **{OPT_ALLOW_DROP_IN: True, OPT_DROP_IN_ALLOWLIST: acl})
        assert not data.drop_in_allowed(_tablet(), _tzvi()), acl


async def test_caller_without_user_denied(hass):
    data = _data(hass, **{
        OPT_ALLOW_DROP_IN: True,
        OPT_DROP_IN_ALLOWLIST: _acl({"tablet-client-0001": ["uid_tzvi"]}),
    })
    anon = Endpoint(client_id="anon-client-001")
    assert not data.drop_in_allowed(_tablet(), anon)

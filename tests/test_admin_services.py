"""hangup / prune_endpoints are admin-only (home-hardened extra)."""

from __future__ import annotations

import pytest
from homeassistant.core import Context
from homeassistant.exceptions import Unauthorized

from custom_components.videocall.const import DOMAIN


@pytest.mark.parametrize("service", ["hangup", "prune_endpoints"])
async def test_non_admin_refused(hass, setup_videocall, hass_read_only_user, service):
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, service, {}, blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


@pytest.mark.parametrize("service", ["hangup", "prune_endpoints"])
async def test_plain_user_refused(hass, setup_videocall, hass_read_only_user, service):
    # a regular non-admin user (like the kitchen `tablet` login), no groups
    hass_read_only_user.groups = []
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN, service, {}, blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )


@pytest.mark.parametrize("service", ["hangup", "prune_endpoints"])
async def test_admin_and_automation_allowed(hass, setup_videocall, hass_admin_user, service):
    await hass.services.async_call(
        DOMAIN, service, {}, blocking=True, context=Context(user_id=hass_admin_user.id),
    )
    await hass.services.async_call(DOMAIN, service, {}, blocking=True)  # no user

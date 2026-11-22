"""videocall — HA-native WebRTC video calling. See SPEC.md."""

from __future__ import annotations

import json
import logging
import re

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.service import async_register_admin_service
import voluptuous as vol

from . import ws_api
from .const import (
    SIGNAL_ENDPOINT_REMOVED,
    DEFAULT_ALLOW_DROP_IN,
    DEFAULT_ANSWER_DASHBOARD,
    DEFAULT_ICE_SERVERS,
    DEFAULT_RING_TIMEOUT,
    DEFAULT_TURN_STUN,
    DEFAULT_TURN_TLS,
    DOMAIN,
    ENDED_CALL_LOG_SIZE,
    OPT_ALLOW_DROP_IN,
    OPT_ANSWER_DASHBOARD,
    OPT_DROP_IN_ALLOWLIST,
    OPT_ICE_SERVERS,
    OPT_RING_TIMEOUT,
    OPT_TURN_CREDENTIAL,
    OPT_TURN_HOST,
    OPT_TURN_LAN_HOST,
    OPT_TURN_STUN,
    OPT_TURN_TLS,
    OPT_TURN_USERNAME,
)
from .frontend import async_register_frontend
from .mobile import MobileRinger
from .models import CallRegistry, EndpointRegistry, EndReason, Endpoint

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["binary_sensor", "sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BROWSER_MOD_DOMAIN = "browser_mod"


class VideocallData:
    """hass.data[DOMAIN] — everything the ws_api/platforms need."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.endpoints = EndpointRegistry()
        self.calls = CallRegistry(log_size=ENDED_CALL_LOG_SIZE)
        self.ring_timeouts: dict[str, callable] = {}
        self.roster_unsub = None
        self.mobile = MobileRinger(hass, self._options, self._phone_declined)

    # --- live options (no reload needed for value reads) --------------------

    def _options(self) -> dict:
        return dict(self.entry.options)

    @property
    def ice_servers(self) -> list:
        opts = self.entry.options
        host = (opts.get(OPT_TURN_HOST) or "").strip()
        lan = (opts.get(OPT_TURN_LAN_HOST) or "").strip()
        tls = bool(opts.get(OPT_TURN_TLS, DEFAULT_TURN_TLS))

        def _hostport(h: str, default: int = 3478) -> str:
            return h if ":" in h else f"{h}:{default}"

        # Base STUN list, by precedence:
        #   1) "Use my TURN server for STUN" checkbox (+ a TURN host) → your own
        #      coturn for STUN, no public dependency, no JSON to hand-write.
        #   2) advanced ice_servers JSON override: blank → skip to (3);
        #      [] / null → NO default servers; a valid RTCIceServer[] → used as-is.
        #   3) the default list (home-hardened: [] = LAN-only, no public STUN).
        # Malformed advanced JSON falls back to the default (with a warning).
        # (a TLS-only public port can't answer STUN — browsers have no stuns: —
        # so with TLS on only the LAN host qualifies)
        stun = [f"stun:{_hostport(host)}"] if host and not tls else []
        if host and lan:
            stun.append(f"stun:{_hostport(lan)}")
        if opts.get(OPT_TURN_STUN, DEFAULT_TURN_STUN) and stun:
            servers: list = [{"urls": stun}]
        else:
            raw = opts.get(OPT_ICE_SERVERS, "")
            if isinstance(raw, str) and raw.strip():
                try:
                    servers = json.loads(raw)
                except ValueError:
                    _LOGGER.warning("Invalid ice_servers option JSON; using default")
                    servers = json.loads(DEFAULT_ICE_SERVERS)
                if not isinstance(servers, list):  # e.g. null → no default servers
                    servers = []
            else:
                servers = json.loads(DEFAULT_ICE_SERVERS)

        # Compose the TURN entry from the simple fields (preferred config path)
        # so users never hand-write RTCIceServer JSON. host is "host" or
        # "host:port" (defaults to 3478, or 5349 for TLS); the optional LAN
        # address lets on-network clients relay without NAT hairpinning.
        if host:
            if tls:  # TLS is TCP-only
                urls = [f"turns:{_hostport(host, 5349)}?transport=tcp"]
            else:
                urls = [
                    f"turn:{_hostport(host)}?transport=udp",
                    f"turn:{_hostport(host)}?transport=tcp",
                ]
            if lan:
                urls.append(f"turn:{_hostport(lan)}?transport=udp")
            turn: dict = {"urls": urls}
            user = (opts.get(OPT_TURN_USERNAME) or "").strip()
            cred = opts.get(OPT_TURN_CREDENTIAL) or ""
            if user:
                turn["username"] = user
            if cred:
                turn["credential"] = cred
            servers.append(turn)

        # De-dupe: the checkbox/TURN fields can compose an entry a user also
        # pasted into the advanced JSON — emit each server once.
        seen: set[str] = set()
        deduped = []
        for s in servers:
            key = json.dumps(s, sort_keys=True)
            if key not in seen:
                seen.add(key)
                deduped.append(s)
        return deduped

    @property
    def ring_timeout(self) -> int:
        return int(self.entry.options.get(OPT_RING_TIMEOUT, DEFAULT_RING_TIMEOUT))

    @property
    def allow_drop_in(self) -> bool:
        return bool(self.entry.options.get(OPT_ALLOW_DROP_IN, DEFAULT_ALLOW_DROP_IN))

    @property
    def drop_in_allowlist(self) -> dict[str, set[str]]:
        """Server-side drop-in allowlist: {target key: {caller keys}}, lowercased.

        Blank, malformed or non-dict JSON is an empty map (deny all)."""
        raw = self.entry.options.get(OPT_DROP_IN_ALLOWLIST) or ""
        try:
            parsed = json.loads(raw) if isinstance(raw, str) and raw.strip() else {}
        except ValueError:
            _LOGGER.warning("Invalid drop_in_allowlist JSON; drop-in denied for all")
            return {}
        if not isinstance(parsed, dict):
            return {}
        out: dict[str, set[str]] = {}
        for target, callers in parsed.items():
            if isinstance(callers, list):
                out[str(target).lower()] = {
                    c.lower() for c in callers if isinstance(c, str) and c
                }
        return out

    def drop_in_allowed(self, target: Endpoint, caller: Endpoint) -> bool:
        """May `caller` drop in (auto-answer) on `target`? HOME-HARDENED.

        Deny unless ALL of these hold:
          1. the global allow_drop_in switch is on (default off);
          2. the SERVER-SIDE drop_in_allowlist names this target (endpoint
             client_id or HA device_id) and lists this caller (HA user_id or
             user name); a missing or empty entry denies;
          3. if the target device declared its own consent list at register,
             the caller is on that too. Device-owned consent can only narrow
             the server policy, never widen it ([] = nobody).
        """
        if not self.allow_drop_in:
            return False
        caller_keys = {k.lower() for k in (caller.user_id, caller.user_name) if k}
        if not caller_keys:
            return False
        allowlist = self.drop_in_allowlist
        target_keys = [k.lower() for k in (target.client_id, target.device_id) if k]
        if not any(caller_keys & allowlist.get(t, set()) for t in target_keys):
            return False
        device = target.drop_in_allow
        if device is None:
            return True
        return bool(caller_keys & {str(a).lower() for a in device})

    # --- endpoint device / area resolution (SPEC §4.2) ----------------------

    async def async_resolve_endpoint_device(
        self, endpoint: Endpoint, requested_name: str = ""
    ) -> None:
        dev_reg = dr.async_get(self.hass)

        suggested_area = None
        adopted_name = None

        # 1) companion app → unify with its mobile_app device (SPEC §4.4):
        #    the phone HA already knows ("Mark's iPhone …") IS this endpoint.
        #    The link is our device's via device, so it survives restarts.
        match = None
        if endpoint.ua_kind.startswith("companion") and endpoint.user_id:
            match = self.mobile.find_companion_match(
                endpoint.user_id, endpoint.ua_kind, endpoint.ua_hint,
                linked=endpoint.phone_device_id,
            )
        endpoint.phone_device_id = match["device_id"] if match else None
        if match:
            adopted_name = match["name"]
            suggested_area = match["area_id"]

        # 2) browser_mod adoption — but IGNORE its auto-generated junk names
        #    (browser_mod_<hex>_<hex>); those are ids, not names.
        if endpoint.browser_mod_id:
            bm_device = dev_reg.async_get_device(
                identifiers={(BROWSER_MOD_DOMAIN, endpoint.browser_mod_id)}
            )
            if bm_device:
                if suggested_area is None:
                    suggested_area = bm_device.area_id
                bm_name = bm_device.name_by_user or bm_device.name
                if (
                    adopted_name is None
                    and bm_name
                    and not re.match(r"^browser_mod_[0-9a-f]{6,}", bm_name)
                ):
                    adopted_name = bm_name

        # 3) fallback: name by USER + browser/OS — the client_id lives in the
        #    device's serial_number field, never in the display name.
        kind_label = {
            "companion-ios": "iOS app",
            "companion-android": "Android app",
        }.get(endpoint.ua_kind) or endpoint.browser_hint or "browser"
        # derived afresh at every registration (endpoint.name is only the last
        # display name), so a renamed phone's new name follows
        name = requested_name or adopted_name or (
            f"{endpoint.user_name}'s {kind_label}"
            if endpoint.user_name
            else f"Videocall {kind_label}"
        )

        # name collision with a DIFFERENT endpoint's device → short #suffix
        # (only then; hex stays out of names otherwise)
        taken = {
            (d.name_by_user or d.name)
            for d in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id)
            if (DOMAIN, endpoint.client_id) not in d.identifiers
        }
        if name in taken:
            name = f"{name} #{endpoint.client_id[:4]}"

        device = dev_reg.async_get_or_create(
            config_entry_id=self.entry.entry_id,
            identifiers={(DOMAIN, endpoint.client_id)},
            name=name,
            manufacturer="videocall",
            model=endpoint.ua_kind,
            serial_number=endpoint.client_id,
        )
        # migrate earlier auto-generated names (hex-in-name / browser_mod junk /
        # v0.6.0's undifferentiated "X's browser") — but never a user rename
        if (
            device.name_by_user is None
            and device.name != name
            and re.match(
                r"^(Videocall (browser|companion)|browser_mod_[0-9a-f]"
                r"|.+'s (browser|iOS app|Android app)$)",
                device.name or "",
            )
        ):
            dev_reg.async_update_device(device.id, name=name)
            device = dev_reg.async_get(device.id)
        # adopt the suggested area only if the user hasn't set one on OUR device
        if suggested_area and device.area_id is None:
            dev_reg.async_update_device(device.id, area_id=suggested_area)
            device = dev_reg.async_get(device.id)
        if device.via_device_id != endpoint.phone_device_id:
            dev_reg.async_update_device(device.id, via_device_id=endpoint.phone_device_id)
            device = dev_reg.async_get(device.id)

        endpoint.device_id = device.id
        endpoint.name = device.name_by_user or device.name or name
        endpoint.area_id = device.area_id
        endpoint.area_name = _area_name(self.hass, device.area_id)

    def restore_endpoints(self) -> None:
        """Every known endpoint, offline until it registers: its entities
        exist from startup on, and an app keeps its phone link (SPEC §8)."""
        dev_reg = dr.async_get(self.hass)
        for device in dr.async_entries_for_config_entry(dev_reg, self.entry.entry_id):
            client_id = next((i[1] for i in device.identifiers if i[0] == DOMAIN), None)
            if client_id is None:
                continue
            self.endpoints.upsert(
                Endpoint(
                    client_id=client_id,
                    ua_kind=device.model or "browser",
                    name=device.name_by_user or device.name or "",
                    device_id=device.id,
                    area_id=device.area_id,
                    area_name=_area_name(self.hass, device.area_id),
                    phone_device_id=device.via_device_id,
                )
            )

    # --- mobile decline hook --------------------------------------------------

    @callback
    def _phone_declined(self, call_id: str, service: str) -> None:
        call = self.calls.get(call_id)
        if call is not None:
            ws_api.async_decline_phone(self.hass, call, service)


def _area_name(hass: HomeAssistant, area_id: str | None) -> str | None:
    area = ar.async_get(hass).async_get_area(area_id) if area_id else None
    return area.name if area else None


async def async_setup(hass: HomeAssistant, config) -> bool:
    return True


@callback
def async_prune_offline_endpoints(hass: HomeAssistant) -> int:
    """Remove every videocall DEVICE whose endpoint is not online right now.

    Must iterate the persistent device registry, NOT the in-memory endpoint
    map — that map is empty after each HA restart (endpoints exist there only
    once a browser re-registers), so an in-memory prune reports "0 removed"
    while a dozen stale devices remain. Shared by the
    videocall.prune_endpoints service and the options-flow menu action.
    Online endpoints are never touched.
    """
    data: VideocallData | None = hass.data.get(DOMAIN)
    if data is None:
        return 0
    dev_reg = dr.async_get(hass)
    online_ids = {ep.client_id for ep in data.endpoints.online()}
    pruned = 0
    for device in dr.async_entries_for_config_entry(dev_reg, data.entry.entry_id):
        client_ids = [ident[1] for ident in device.identifiers if ident[0] == DOMAIN]
        if not client_ids or client_ids[0] in online_ids:
            continue
        dev_reg.async_update_device(
            device.id, remove_config_entry_id=data.entry.entry_id
        )
        data.endpoints.remove(client_ids[0])
        # tell the entity platforms to forget this client_id — otherwise a
        # re-registering endpoint is "already known" and never gets its
        # online/call-state entities back after a prune
        async_dispatcher_send(hass, SIGNAL_ENDPOINT_REMOVED, client_ids[0])
        pruned += 1
    _LOGGER.info("videocall: pruned %d offline endpoint device(s)", pruned)
    return pruned


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = VideocallData(hass, entry)
    hass.data[DOMAIN] = data
    data.restore_endpoints()

    ws_api.async_register_commands(hass)
    await async_register_frontend(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # ---- services (SPEC §8) -------------------------------------------------

    async def svc_hangup(call: ServiceCall) -> None:
        call_id = call.data.get("call_id")
        targets = (
            [data.calls.get(call_id)] if call_id else list(data.calls.active())
        )
        for c in targets:
            if c:
                ws_api.async_end_call(hass, c, EndReason.HANGUP)

    async def svc_prune(call: ServiceCall) -> None:
        async_prune_offline_endpoints(hass)

    # Home-hardened: admin-only (non-admin users such as the kitchen tablet
    # login get Unauthorized); automations and scripts run unrestricted.
    async_register_admin_service(
        hass, DOMAIN, "hangup", svc_hangup,
        vol.Schema({vol.Optional("call_id"): str}),
    )
    async_register_admin_service(
        hass, DOMAIN, "prune_endpoints", svc_prune,
        vol.Schema({vol.Optional("days", default=30): int}),
    )
    # Home-hardened: the upstream videocall.ring (go2rtc doorbell) service is
    # NOT registered. It let any user able to call services forge a doorbell
    # ring whose answer streamed the callee's mic to a caller-chosen URL.

    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    # options are read live (VideocallData properties) so there's nothing to
    # reload — BUT already-connected clients only learn of new config
    # (ice_servers/TURN, ring_timeout, …) via a roster push. Broadcast one now,
    # otherwise the change doesn't reach open dashboards until they happen to
    # get an unrelated roster event or the page is reloaded/re-registered.
    # never log the TURN secret — users paste debug logs into issues
    _LOGGER.debug(
        "videocall options updated: %s",
        {k: "***" if k == OPT_TURN_CREDENTIAL else v for k, v in entry.options.items()},
    )
    if DOMAIN in hass.data:
        ws_api.async_push_roster(hass)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    data: VideocallData = hass.data.pop(DOMAIN, None)
    if data:
        for unsub in data.ring_timeouts.values():
            unsub()
        data.mobile.async_unload()
    # NOTE: websocket commands cannot be unregistered; handlers no-op once
    # hass.data[DOMAIN] is gone (guarded lookups raise unknown_call/not_registered).
    return ok

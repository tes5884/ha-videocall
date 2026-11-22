"""Shabbos / Yom Tov gate (home-hardened).

House policy: no alerts of any kind during issur melacha. Every path that
starts a call or pushes a phone notification asks `melacha_clear` first, and
it fails CLOSED: only an explicit "off" from the Jewish Calendar sensor opens
the gate. A missing entity, "on", "unknown" or "unavailable" all block.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .const import MELACHA_GATE_ENTITY


def melacha_clear(hass: HomeAssistant) -> bool:
    """True only when issur melacha is definitely NOT in effect."""
    state = hass.states.get(MELACHA_GATE_ENTITY)
    return state is not None and state.state == "off"

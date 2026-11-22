# Home-hardened build (tes5884/ha-videocall, branch `home`)

Fork of markfrancisonly/ha-videocall at release v2026.10.3
(`af90e1e97d96614190462ef178eed759337c50d1`), patched for one household after a
security review. Not intended for upstream as-is. Version `2026.10.3.1`.

| # | Change | Where |
|---|---|---|
| 1 | Drop-in is off by default and needs a **server-side** per-target caller allowlist (`drop_in_allowlist`, Advanced options). Missing/empty/malformed = deny. A device's own consent list can only narrow it. JS auto-answers only when the server flagged the ring as drop-in and `allow_drop_in` is explicitly true. | `const.py`, `__init__.py` `drop_in_allowed`, `config_flow.py`, JS `_onEvent`/`_applyRoster` |
| 2 | No public STUN. Default ICE list is `[]` (LAN-only); the JS starts with `[]` and honours an empty configured list. | `const.py`, JS core constructor / `_applyRoster` |
| 3 | `videocall.ring` (go2rtc doorbell) service removed, with its server fan-out. | `__init__.py`, `ws_api.py`, `services.yaml` |
| 4 | Shabbos/Yom Tov gate: no call starts and no ring or missed-call phone push goes out unless `binary_sensor.jewish_calendar_issur_melacha_in_effect` is exactly `off` (fail closed). | `gate.py`, `ws_api.py` `ws_invite` + late ring, `mobile.py` |
| + | `videocall.hangup` / `videocall.prune_endpoints` are admin-only. | `__init__.py` |

Requires the Jewish Calendar integration; without that sensor no call can start.

## Tests

```
docker run --rm -v "$PWD":/src -w /src vc-hatest pytest -q -p no:cacheprovider tests
node tests/js/test_ice.mjs
```
`vc-hatest` = `python:3.14-slim` + gcc + `pip install pytest-homeassistant-custom-component`.

## Build

```
git archive --format=zip -o videocall.zip <commit>:custom_components/videocall
```
Same layout as the upstream release asset (zip root = the integration folder's
contents). `git archive` stamps entries with the commit time, so the same
commit always yields the same zip bytes (same git version).

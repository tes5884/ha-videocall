// Blocker 2: the browser core must never use public STUN and must honour an
// empty configured ICE list.  Run: node tests/js/test_ice.mjs
import { readFileSync } from "node:fs";
import vm from "node:vm";
import assert from "node:assert/strict";

const src = readFileSync(
  new URL("../../custom_components/videocall/www/videocall-card.js", import.meta.url), "utf8");

assert.ok(!/stun\.l\.google\.com/.test(src), "no Google STUN literal in the shipped JS");

const noop = () => {};
const store = new Map();
const evTarget = { addEventListener: noop, removeEventListener: noop, dispatchEvent: noop };
const ctx = {
  console: { ...console, warn: noop, debug: noop },
  location: { search: "" },
  localStorage: {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
  },
  document: { ...evTarget, visibilityState: "visible", createElement: () => ({}) },
  HTMLElement: class {},
  customElements: { get: () => undefined, define: noop },
  EventTarget, CustomEvent,
  URLSearchParams, crypto: globalThis.crypto, setTimeout, clearTimeout,
  navigator: { userAgent: "node" },
};
ctx.window = ctx;
ctx.window.addEventListener = noop;
ctx.window.top = ctx;
vm.createContext(ctx);
vm.runInContext(src, ctx);

const core = ctx.VideoCallCore;
assert.ok(core, "VideoCallCore booted");
assert.deepEqual([...core.iceServers], [], "startup ICE list is empty (no public STUN)");

core._applyRoster({ config: { ice_servers: [{ urls: "turn:lan:3478" }], ring_timeout: 30 } });
assert.equal(core.iceServers.length, 1, "non-empty configured list applied");

core._applyRoster({ config: { ice_servers: [], ring_timeout: 30 } });
assert.deepEqual([...core.iceServers], [], "empty configured list is honoured, not discarded");

// drop-in master flag must be explicitly true (blocker 1 defence in depth)
core._applyRoster({ config: { ice_servers: [] } });
assert.equal(core.options.allow_drop_in, false, "missing allow_drop_in = off");
core._applyRoster({ config: { ice_servers: [], allow_drop_in: true } });
assert.equal(core.options.allow_drop_in, true);

console.log("test_ice.mjs: all assertions passed");

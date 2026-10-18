"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("probes/runtime/contents/code/runtimeCore.js", "utf8")
    .replace(/^\.pragma library\s*/m, "");
const core = {};
vm.runInNewContext(source + "\nthis.api = { boundedExchangeResult, windowSnapshot };", core);

assert.deepEqual(JSON.parse(JSON.stringify(core.api.boundedExchangeResult("e", 2, "e", 2, { ok: true, payload: "ok" }))),
    { accepted: true, ok: true, payload: "ok" });
assert.equal(core.api.boundedExchangeResult("old", 2, "new", 2, { ok: true }).reason, "stale");
assert.equal(core.api.boundedExchangeResult("e", 1, "e", 2, { ok: true }).reason, "stale");
assert.equal(core.api.boundedExchangeResult("e", 2, "e", 2, null).reason, "failed");
const snapshot = core.api.windowSnapshot({
    internalId: "opaque-1", desktopFileName: "sample-app", pid: 42,
    frameGeometry: { x: -12.5, y: 3.25, width: 640, height: 480 },
    output: { name: "DP-1", geometry: { x: -100, y: 0, width: 1000, height: 800 } },
    minimized: false, maximized: false, fullScreen: false, moveable: true, resizeable: true,
}, { x: -100, y: 24, width: 1000, height: 776 });
assert.equal(snapshot.identity, "opaque-1");
assert.equal(snapshot.appId, "sample-app");
assert.equal(snapshot.geometry.x, -12.5);
assert.equal(snapshot.workArea.y, 24);
assert.equal(snapshot.output.name, "DP-1");
console.log("runtime core: 9 assertions passed");

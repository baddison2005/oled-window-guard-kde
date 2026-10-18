"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("core/model.js", "utf8").replace(/^\.pragma library\s*/m, "");
const context = {};
vm.runInNewContext(source + "\nthis.api = { applicationWindowType, rectangle, rectangleContains, rectangleOverlaps, rectanglesClose, roundingResizeAllowed,roundingContactAllowed,movementCollision, createSnapshot, createPlan, validateSnapshot, validatePlan, seededRandom, monotonicClock };", context);
const api = context.api;
const rect = (x, y, width, height) => ({ x, y, width, height });
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,400,100),true,2),true);
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,398,100),true,2),false);
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,400,100),false,2),false);
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,402,100),true,2),true);
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,400,100),true,2,null),false);
assert.equal(api.roundingResizeAllowed(rect(0,0,401,100),rect(0,0,400,100),true,2,{width:401,height:100}),false);
const windowRecord = (overrides = {}) => Object.assign({
    identity: "opaque", appId: "org.example.app", outputIds: ["DP-1"], rect: rect(-100, 30, 640, 480),
    visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false,
    managed: true, modal: false, transient: false, movable: true, resizable: true,
    minimized: false, maximized: false, fullscreen: false, tiled: false,
    windowType: "normal", skipReasons: []
}, overrides);

assert.equal(api.rectangle(rect(-12.5, 2.25, 50, 40)).x, -12.5);
assert.equal(api.rectangle(rect(0, 0, NaN, 1)), null);
assert.equal(api.rectangle(rect(1e308, 0, 1e308, 1)), null);
assert.equal(api.rectangle(rect(0, 0, -1, 1)), null);
assert.equal(api.rectangleContains(rect(-10, -10, 20, 20), rect(-10, -10, 20, 20)), true);
assert.equal(api.rectangleContains(rect(0, 0, 10, 10), rect(0, 0, 10.001, 10)), false);
assert.equal(api.rectangleOverlaps(rect(0, 0, 10, 10), rect(9.99, 0, 10, 10)), false);
assert.equal(api.rectangleOverlaps(rect(0, 0, 10, 10), rect(9.989, 0, 10, 10)), true);
assert.equal(api.rectangleOverlaps(rect(0, 0, 10, 10), rect(10, 0, 1, 1)), false);
assert.equal(api.rectanglesClose(rect(0, 0, 10, 10), rect(0.5, -0.5, 10.5, 9.5)), true);
assert.equal(api.rectanglesClose(rect(0, 0, 10, 10), rect(0.501, 0, 10, 10)), false);

const snap = api.createSnapshot({ outputs: [{ id: "DP-1", geometry: rect(-1920, 0, 1920, 1080), usableRect: rect(-1920, 24, 1920, 1056), scale: 1.25 }], windows: [windowRecord()], capturedAt: 4, topologyRevision: "topo-1" });
assert.equal(api.validateSnapshot(snap).valid, true);
assert.equal(api.validateSnapshot({ outputs: [{ id: "x", geometry: rect(0, 0, 10, 10), usableRect: rect(0, 0, NaN, 10), scale: 1 }], windows: [], topologyRevision: "r" }).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ outputs: [{ id: "x", geometry: rect(0, 0, 100, 100), usableRect: rect(0, 0, 100, 90), scale: 0 }], topologyRevision: "r" })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ outputs: [{ id: "x", geometry: rect(0, 0, 100, 100), usableRect: rect(0, -1, 100, 90), scale: 1 }], topologyRevision: "r" })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ outputs: [snap.outputs[0], snap.outputs[0]], topologyRevision: "r" })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [windowRecord({ identity: "a" }), windowRecord({ identity: "a" })] })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [windowRecord({ identity: "bad", outputIds: ["missing"] })] })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ outputs: [snap.outputs[0]], topologyRevision: "r", windows: [windowRecord({ identity: "bad", outputIds: null, focused: null, appId: null })] })).valid, true);
assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [windowRecord({ identity: "bad", outputIds: ["DP-1", "DP-1"] })] })).valid, false);
assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [windowRecord({ identity: "bad", movable: "yes" })] })).valid, false);
const incompleteWindow = windowRecord({ identity: "bad" }); delete incompleteWindow.tiled;
assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [incompleteWindow] })).valid, false);
for (const field of ["managed", "modal", "transient"]) {
    const incomplete = windowRecord(); delete incomplete[field];
    assert.equal(api.validateSnapshot(api.createSnapshot({ windows: [incomplete] })).valid, false, field);
}
assert.equal(api.validatePlan(api.createPlan({ snapshotRevision: "topo-1", outputIds: ["DP-1"], moves: [{ identity: "opaque", fromRect: rect(0, 0, 10, 10), toRect: rect(1.5, 2.25, 10, 10) }] })).valid, true);
assert.equal(api.validatePlan(api.createPlan({ snapshotRevision: 1, outputIds: ["DP-1", "DP-1"] })).valid, false);
assert.equal(api.validatePlan({ snapshotRevision: "r", outputIds: [], moves: [{ identity: "bad", fromRect: rect(0, 0, 10, 10), toRect: { x: 0, y: 0, width: Infinity, height: 10 } }] }).valid, false);
const cyclic = {}; cyclic.self = cyclic;
assert.throws(() => api.createSnapshot({ windows: [cyclic] }), /cyclic/);
assert.throws(() => api.createSnapshot({ focusedIdentity: new Date() }), /plain object/);
assert.throws(() => api.createPlan({ metadata: { date: new Date() } }), /plain object/);

const randomA = api.seededRandom(1234), randomB = api.seededRandom(1234);
assert.deepEqual([randomA(), randomA(), randomA()], [randomB(), randomB(), randomB()]);
assert.throws(() => api.seededRandom(NaN), /finite integer/);
const referenceRandom = api.seededRandom(1234);
assert.deepEqual([referenceRandom(), referenceRandom(), referenceRandom()], [0.07329497812315822, 0.7034119898453355, 0.9028560190927237]);
let ticks = [10, 11], clock = api.monotonicClock(() => ticks.shift());
assert.equal(clock.now(), 10); assert.equal(clock.now(), 11);
let backwards = [3, 2]; clock = api.monotonicClock(() => backwards.shift()); clock.now();
assert.throws(() => clock.now(), /monotonic/);
console.log("core model: tests passed");

assert.equal(api.applicationWindowType("normal","cairo-dock",false,false),"dock");
for (const args of [["normal","cairo-dock",true,true],["dialog","cairo-dock",false,false],["normal","other",false,false],["normal","cairo-dock",null,false]])
    assert.equal(api.applicationWindowType(...args),args[0]);

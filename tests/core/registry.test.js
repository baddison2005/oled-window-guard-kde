"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const modelSource = fs.readFileSync("core/model.js", "utf8").replace(/^\.pragma library\s*/m, "");
const source = fs.readFileSync("core/registry.js", "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import ".*" as Model\s*/m, "");
const context = {};
vm.runInNewContext(modelSource + "\nthis.Model = { applicationExcluded, roundingResizeAllowed,roundingContactAllowed,movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, validateSnapshot };\n" + source + "\nthis.api = { createRegistryState, reconcile, acknowledgeGuard, reconnectOutputSelection, diagnosticSummary, snapshotSignature };", context);
const api = context.api;
const strictDeepEqual = assert.deepStrictEqual.bind(assert);
assert.deepEqual = (actual, expected, message) => strictDeepEqual(JSON.parse(JSON.stringify(actual)), JSON.parse(JSON.stringify(expected)), message);
const plain = value => JSON.parse(JSON.stringify(value));
const rect = (x, y, width, height) => ({ x, y, width, height });
const output = (id = "DP-1", overrides = {}) => Object.assign({ id, connector: "DP-1", edid: "edid-1", name: "Same name", geometry: rect(-100, 0, 1000, 800), usableRect: rect(-100, 0, 1000, 800), scale: 1.25 }, overrides);
const win = (identity, overrides = {}) => Object.assign({ identity, appId: "app", title: "secret title", pid: 123, outputIds: ["DP-1"], rect: rect(0, 0, 200, 100), visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false, managed: true, modal: false, transient: false, movable: true, resizable: true, minimized: false, maximized: false, fullscreen: false, tiled: false, windowType: "normal", skipReasons: [] }, overrides);
const snapshot = (windows, outputs = [output()], extra = {}) => Object.assign({ outputs, windows, focusedIdentity: null, topologyRevision: "topo-1" }, extra);

let state = api.reconcile(snapshot([win("epoch1:1")]), api.createRegistryState("epoch1"), { sessionEpoch: "epoch1", selectedOutputIds: ["DP-1"] });
let item = state.windows[0];
assert.equal(item.candidate, true); assert.equal(item.obstacle, true); assert.deepEqual(item.originFrame, rect(0, 0, 200, 100));
// Guard acknowledgement changes last/previous and revision while preserving origin.
state = api.acknowledgeGuard(plain(state), "epoch1:1", rect(10.5, -2.25, 200, 100));
assert.deepEqual(state.windows[0].originFrame, rect(0, 0, 200, 100));
assert.deepEqual(state.windows[0].previousFrame, rect(0, 0, 200, 100));
assert.equal(state.windows[0].geometryRevision, 1);
state = api.reconcile(snapshot([win("epoch1:1", { rect: rect(10.5, -2.25, 200, 100) })]), plain(state), { sessionEpoch: "epoch1" });
assert.deepEqual(state.windows[0].originFrame, rect(0, 0, 200, 100));
// A manual/external edit adopts a new origin, including fractional and negative coordinates.
state = api.reconcile(snapshot([win("epoch1:1", { rect: rect(-12.75, 4.5, 200, 100) })]), plain(state), { sessionEpoch: "epoch1" });
assert.deepEqual(state.windows[0].originFrame, rect(-12.75, 4.5, 200, 100));
assert.deepEqual(state.windows[0].previousFrame, rect(10.5, -2.25, 200, 100));
assert.equal(state.windows[0].geometryRevision, 2);
// Session changes and deletion/new identity never transfer anchors, even for same app/title/PID.
let fresh = api.reconcile(snapshot([win("epoch2:9", { rect: rect(20, 20, 200, 100) })]), plain(state), { sessionEpoch: "epoch2" });
assert.deepEqual(fresh.windows[0].originFrame, rect(20, 20, 200, 100));
state = api.reconcile(snapshot([]), plain(state), { sessionEpoch: "epoch1" });
assert.deepEqual(state.removedIdentities, ["epoch1:1"]);
fresh = api.reconcile(snapshot([win("epoch1:2")]), state, { sessionEpoch: "epoch1" });
assert.deepEqual(fresh.windows[0].originFrame, rect(0, 0, 200, 100));

// Explicit unknown state is ineligible and conservative visible windows remain obstacles.
const unknownFields = ["visible", "onCurrentDesktop", "onCurrentActivity", "focused", "managed", "modal", "transient", "movable", "minimized", "maximized", "fullscreen", "tiled"];
for (const field of unknownFields) {
    const w = win("u-" + field, { [field]: null });
    const result = api.reconcile(snapshot([w]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
    assert.equal(result.candidate, false, field);
    if (field !== "visible") assert.equal(result.obstacle, true, field);
}
const focused = api.reconcile(snapshot([win("f", { focused: true })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(focused.candidate, false); assert.ok(focused.skipReasons.includes("focused")); assert.equal(focused.obstacle, true);
const desktop = api.reconcile(snapshot([win("d", { onCurrentDesktop: false }), win("all", { onCurrentDesktop: null })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] });
assert.equal(desktop.windows[0].obstacle, false); assert.ok(desktop.windows[0].skipReasons.includes("other_desktop"));
assert.equal(desktop.windows[1].obstacle, true); assert.ok(desktop.windows[1].skipReasons.includes("unknown_desktop"));
const otherActivity = api.reconcile(snapshot([win("other-activity", { onCurrentActivity: false })]), api.createRegistryState("s"),
    { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(otherActivity.candidate, false); assert.equal(otherActivity.obstacle, false);
for (const pair of [["dialog", "unsupported_window_type"], ["maximized", "maximized"], ["fullscreen", "fullscreen"], ["minimized", "minimized"], ["tiled", "tiled"], ["unknown", "unknown_window_type"]]) {
    const result = api.reconcile(snapshot([win(pair[0], pair[0] === "dialog" || pair[0] === "unknown" ? { windowType: pair[0] } : { [pair[0]]: true })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
    assert.equal(result.candidate, false, pair[0]); assert.ok(result.skipReasons.includes(pair[1])); assert.equal(result.obstacle, pair[0]!=="minimized");
}
const spanning = api.reconcile(snapshot([win("span", { outputIds: ["DP-1", "DP-2"], rect: rect(850, 20, 200, 300) })], [output(), output("DP-2", { edid: "edid-2", geometry: rect(900, 0, 1000, 800), usableRect: rect(900, 0, 1000, 800) })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(spanning.candidate, false); assert.equal(spanning.obstacle, true); assert.equal(spanning.spanning, true); assert.ok(spanning.skipReasons.includes("spanning"));
const outside = api.reconcile(snapshot([win("outside", { rect: rect(850, 10, 200, 100) })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(outside.candidate, false); assert.ok(outside.skipReasons.includes("spanning"));
const immovable = api.reconcile(snapshot([win("imm", { movable: false })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(immovable.candidate, false); assert.ok(immovable.skipReasons.includes("immovable")); assert.equal(immovable.obstacle, true);
const excluded = api.reconcile(snapshot([win("excluded", { appId: "blocked.app" })]), api.createRegistryState("s"),
    { sessionEpoch: "s", selectedOutputIds: ["DP-1"], excludedApps: ["blocked.app"] }).windows[0];
assert.equal(excluded.candidate, false); assert.equal(excluded.obstacle, true); assert.ok(excluded.skipReasons.includes("excluded_app"));
const unknownApp = api.reconcile(snapshot([win("unknown-app", { appId: null })]), api.createRegistryState("s"),
    { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(unknownApp.candidate, false); assert.ok(unknownApp.skipReasons.includes("unknown_app_identity"));
const modal = api.reconcile(snapshot([win("modal", { skipReasons: ["modal"] })]), api.createRegistryState("s"),
    { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(modal.candidate, false); assert.equal(modal.obstacle, true);
for (const [field, reason] of [["managed", "unmanaged"], ["modal", "modal"], ["transient", "transient"]]) {
    const value = field === "managed" ? false : true;
    const result = api.reconcile(snapshot([win(field, { [field]: value })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
    assert.equal(result.candidate, false, field); assert.ok(result.skipReasons.includes(reason), field);
    assert.equal(result.obstacle, true, field);
}
const overlaps = api.reconcile(snapshot([win("a"), win("b", { rect: rect(100, 10, 100, 100) })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] });
assert.ok(overlaps.windows.every(w => !w.candidate && w.skipReasons.includes("overlapping_window")));
const obstacleOverlap = api.reconcile(snapshot([win("candidate"), win("popup", { windowType: "popup", rect: rect(100, 20, 100, 100) })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] });
assert.equal(obstacleOverlap.windows[0].candidate, false); assert.equal(obstacleOverlap.windows[0].obstacle, true);
assert.equal(obstacleOverlap.windows[1].candidate, false); assert.equal(obstacleOverlap.windows[1].obstacle, true);
for (const role of ['notification','osd']) {
    const base=snapshot([win('app')]);
    const showing=snapshot([win('app'),win('overlay',{windowType:role,rect:rect(50,10,300,150)})]);
    const registry=api.reconcile(showing,api.createRegistryState('s'),{sessionEpoch:'s',selectedOutputIds:['DP-1']});
    assert.equal(registry.windows[0].candidate,true);
    assert.equal(registry.windows[1].candidate,false);
    assert.equal(registry.windows[1].obstacle,false);
    assert.equal(api.snapshotSignature(base),api.snapshotSignature(showing));
    showing.windows[1].rect.x+=30;
    assert.equal(api.snapshotSignature(base),api.snapshotSignature(showing));
    showing.windows[1].windowType='dialog';
    assert.notEqual(api.snapshotSignature(base),api.snapshotSignature(showing));
}
const unknownOutputs = api.reconcile(snapshot([win("no-outputs", { outputIds: null })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(unknownOutputs.candidate, false); assert.ok(unknownOutputs.skipReasons.includes("unknown_output_association"));
assert.throws(() => api.reconcile(snapshot([win("bad-ref", { outputIds: ["missing"] })]), api.createRegistryState("s"), { sessionEpoch: "s" }), /invalid snapshot/);
assert.throws(() => api.reconcile(snapshot([win("missing-state", { tiled: undefined })]), api.createRegistryState("s"), { sessionEpoch: "s" }), /invalid snapshot/);
const emptyOutputs = api.reconcile(snapshot([win("empty-outputs", { outputIds: [] })]), api.createRegistryState("s"), { sessionEpoch: "s", selectedOutputIds: ["DP-1"] }).windows[0];
assert.equal(emptyOutputs.candidate, false); assert.ok(emptyOutputs.skipReasons.includes("missing_output_association"));

// Missed events converge from a fresh snapshot; topology revision and disappearance are explicit.
const changed = api.reconcile(snapshot([win("epoch1:2", { rect: rect(2, 3, 200, 100) })], [output()], { topologyRevision: "topo-2" }), plain(fresh), { sessionEpoch: "epoch1" });
assert.equal(changed.topologyChanged, true); assert.deepEqual(changed.windows[0].originFrame, rect(2, 3, 200, 100));
let anchored = api.reconcile(snapshot([win("topo-anchor")]), api.createRegistryState("ta"), { sessionEpoch: "ta", selectedOutputIds: ["DP-1"] });
anchored = api.reconcile(snapshot([win("topo-anchor", { rect: rect(15, 15, 200, 100) })], [output()], { topologyRevision: "topo-2" }), plain(anchored), { sessionEpoch: "ta" });
assert.equal(anchored.windows[0].anchorAdopted, true); assert.deepEqual(anchored.windows[0].originFrame, rect(15, 15, 200, 100));
const summary = api.diagnosticSummary([focused, spanning, overlaps.windows[0]]);
assert.equal(summary.eligible, 0); assert.equal(summary.obstacles, 3); assert.equal(summary.byReason.focused, 1); assert.equal(summary.byReason.spanning, 1);
assert.equal("title" in focused, false); assert.equal("appId" in focused, false); assert.equal("pid" in focused, false);

// Monitor selection reconnection requires connector + hardware identity, is unambiguous,
// ignores human-readable names, and never chooses newly attached outputs.
let selected = api.reconnectOutputSelection([output("old")], ["old"], [output("new")]);
assert.deepEqual(selected.selectedOutputIds, ["new"]);
selected = api.reconnectOutputSelection([output("old")], ["old"], [output("new", { edid: "different" })]);
assert.deepEqual(selected.selectedOutputIds, []); assert.deepEqual(selected.missingOldIds, ["old"]);
selected = api.reconnectOutputSelection([output("old")], ["old"], [output("a"), output("b")]);
assert.deepEqual(selected.selectedOutputIds, []); assert.deepEqual(selected.ambiguousOldIds, ["old"]);
selected = api.reconnectOutputSelection([output("old", { connector: null, edid: null, serial: null, vendor: null, product: null })], ["old"], [output("new", { connector: null, name: "Same name" })]);
assert.deepEqual(selected.selectedOutputIds, []); assert.deepEqual(selected.missingOldIds, ["old"]);
selected = api.reconnectOutputSelection([output("old", { edid: null, serial: null, vendor: "same", product: "same" })], ["old"], [output("new", { edid: null, serial: null, vendor: "same", product: "same" })]);
assert.deepEqual(selected.selectedOutputIds, []);
let monitorState = api.reconcile(snapshot([]), api.createRegistryState("m"), { sessionEpoch: "m", selectedOutputIds: ["DP-1"] });
monitorState = api.reconcile(snapshot([]), plain(monitorState), { sessionEpoch: "m" });
assert.deepEqual(monitorState.selectedOutputIds, ["DP-1"]);
monitorState = api.reconcile(snapshot([], [output("DP-2", { connector: "DP-1" }), output("DP-3", { connector: "DP-3", edid: "other" })], { topologyRevision: "topo-2" }), plain(monitorState), { sessionEpoch: "m" });
assert.deepEqual(monitorState.selectedOutputIds, ["DP-2"]);
assert.deepEqual(monitorState.outputSelection.selectedOutputIds, ["DP-2"]);
let reconnectState = api.reconcile(snapshot([]), api.createRegistryState("replug"), { sessionEpoch: "replug", selectedOutputIds: ["DP-1"] });
reconnectState = api.reconcile(snapshot([], [], { topologyRevision: "disconnected" }), plain(reconnectState), { sessionEpoch: "replug" });
assert.deepEqual(reconnectState.selectedOutputIds, []);
assert.deepEqual(reconnectState.requestedOutputSelections, [{ requestedId: "DP-1", key: "DP-1|edid-1" }]);
reconnectState = api.reconcile(snapshot([], [output("DP-4")], { topologyRevision: "reconnected" }), plain(reconnectState), { sessionEpoch: "replug" });
assert.deepEqual(reconnectState.selectedOutputIds, ["DP-4"]);
let ambiguousReconnect = api.reconcile(snapshot([]), api.createRegistryState("ambig"), { sessionEpoch: "ambig", selectedOutputIds: ["DP-1"] });
ambiguousReconnect = api.reconcile(snapshot([], [], { topologyRevision: "disconnected" }), plain(ambiguousReconnect), { sessionEpoch: "ambig" });
ambiguousReconnect = api.reconcile(snapshot([], [output("DP-4"), output("DP-5")], { topologyRevision: "ambiguous" }), plain(ambiguousReconnect), { sessionEpoch: "ambig" });
assert.deepEqual(ambiguousReconnect.selectedOutputIds, []);
assert.deepEqual(ambiguousReconnect.outputSelection.ambiguousOldIds, ["DP-1"]);
const hardwareless = output("HDMI-A-1", { connector: "HDMI-A-1", edid: null, serial: null, vendor: null, product: null });
let hardwarelessState = api.reconcile(snapshot([], [hardwareless]), api.createRegistryState("h"), { sessionEpoch: "h", selectedOutputIds: ["HDMI-A-1"] });
hardwarelessState = api.reconcile(snapshot([], [hardwareless]), plain(hardwarelessState), { sessionEpoch: "h" });
assert.deepEqual(hardwarelessState.selectedOutputIds, ["HDMI-A-1"]);
hardwarelessState = api.reconcile(snapshot([], [hardwareless], { topologyRevision: "topo-2" }), plain(hardwarelessState), { sessionEpoch: "h" });
assert.deepEqual(hardwarelessState.selectedOutputIds, []);
const cycle = {}; cycle.self = cycle;
assert.throws(() => api.reconcile({ outputs: [], windows: [cycle] }, null, { sessionEpoch: "m" }), /cyclic/);
console.log("core registry: tests passed");

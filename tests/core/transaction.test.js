"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Load the pure QML-compatible modules in one isolated JavaScript context. The
// fixtures below model adapter snapshots and command acknowledgements only.
const context = {};
const source = name => fs.readFileSync("core/" + name, "utf8")
    .replace(/^\.pragma library\s*/m, "")
    .replace(/^\.import ".*" as (?:Model|Registry|Planner|Safety)\s*/gm, "");
const combined = source("model.js") +
    "\nthis.Model = { applicationExcluded, roundingResizeAllowed,roundingContactAllowed,movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, rectanglesClose, READBACK_TOLERANCE, createPlan, validateSnapshot, validatePlan };" +
    "\n" + source("registry.js") + "\nthis.Registry = { createRegistryState, reconcile, snapshotSignature };" +
    "\n" + source("planner.js") + "\nthis.Planner = { planOrdinaryShift, validateFinalPlan };" +
    "\n" + source("safety.js") + "\nthis.Safety = { evaluate };" + "\n" + source("transaction.js") +
    "\nthis.Registry = { createRegistryState, reconcile, snapshotSignature };" +
    "\nthis.api = { createTransactionState, transition, evaluate, planOrdinaryShift };";
vm.runInNewContext(combined, context);
const api = context.api;
const deep = value => JSON.parse(JSON.stringify(value));
const strictDeepEqual = assert.deepStrictEqual.bind(assert);
assert.deepEqual = (actual, expected, message) => strictDeepEqual(deep(actual), deep(expected), message);
const rect = (x, y, width, height) => ({ x, y, width, height });
const output = { id: "DP-1", connector: "DP-1", edid: "panel", geometry: rect(0, 0, 1000, 800), usableRect: rect(0, 0, 1000, 800), scale: 1 };
const makeWindow = (identity = "w1", frame = rect(100, 100, 100, 100), extra = {}) => Object.assign({
    identity, appId: "fixture.app", outputIds: ["DP-1"], rect: frame,
    visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false,
    managed: true, modal: false, transient: false, movable: true, resizable: true,
    minimized: false, maximized: false, fullscreen: false, tiled: false,
    windowType: "normal", skipReasons: []
}, extra);
const makeSnapshot = (windows = [makeWindow()], outputs = [deep(output)], extra = {}) => Object.assign({
    outputs, windows, focusedIdentity: null, topologyRevision: "topo-1"
}, extra);
const gate = (now = 10000, changes = {}) => Object.assign({
    observedAt: now, companion: "connected", locked: false, suspended: false,
    topologyStable: true, topologyGeneration: 1, topologyOutputIds: ["DP-1"],
    allOutputsQuiet: true, heldButtons: false,
    outputs: [{ id: "DP-1", known: true, quiet: true, buttonsDown: false }],
    capabilities: { keyboard: true, pointer: true, scroll: true, buttonState: true },
    quietSince: now - 5000, quietMs: 1000
}, changes);
function fixture(overrides = {}) {
    const snapshot = overrides.snapshot || makeSnapshot();
    const registry = context.Registry.reconcile(snapshot, context.Registry.createRegistryState("transaction-test"), {
        sessionEpoch: "transaction-test", selectedOutputIds: ["DP-1"]
    });
    const planned = context.planOrdinaryShift(snapshot, registry, {
        outputId: "DP-1", shiftRangePercent: 100, maximumWindows: 1,
        random: () => 0.5, maxEvaluations: 5000, now: () => 10
    });
    assert.equal(planned.plan.moves.length, 1, "fixture must produce one valid move");
    return {
        snapshot, registry, plan: planned.plan,
        settingsRevision: overrides.settingsRevision || "settings-1",
        state: api.createTransactionState(), now: 10000
    };
}
function transition(f, event) {
    const result = api.transition(f.state, event);
    f.state = result.state;
    return result.commands;
}
function resumeAndBegin(f, beginOverrides = {}) {
    transition(f, { type: "resume" });
    const gen = f.state.generation;
    const commands = transition(f, Object.assign({
        type: "begin", generation: gen, now: f.now, snapshot: f.snapshot,
        registry: f.registry, plan: f.plan, settingsRevision: f.settingsRevision,
        currentSettingsRevision: f.settingsRevision, safety: gate(f.now),
        safetyMaxAgeMs: 1000, warningAvailable: true, warningMs: 3000
    }, beginOverrides));
    return commands;
}
function ack(f, commands, type, fields = {}) {
    const command = commands.find(c => c.type === type);
    assert.ok(command, "expected " + type + " command");
    return Object.assign({ generation: f.state.generation, commandId: command.id,
        now: fields.type === "warningShown" ? f.now : undefined }, fields);
}
function reachPreWrite(f) {
    const show = resumeAndBegin(f);
    assert.equal(f.state.phase, "Warning");
    transition(f, ack(f, show, "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: show[0].id, now: f.now + 3000 });
    assert.ok(hide.some(c => c.type === "hideWarning"));
    const request = transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    return request;
}
function freshSnapshot(f, snapshot = f.snapshot, registry = f.registry, extra = {}) {
    const request = f.state.pendingCommand;
    return transition(f, Object.assign({
        type: "snapshotReady", generation: f.state.generation, commandId: request.id,
        now: f.now + 3100, snapshot, registry,
        currentSettingsRevision: f.settingsRevision, safety: gate(f.now + 3100), safetyMaxAgeMs: 1000
    }, extra));
}
function liveAt(f, frame) {
    const snapshot = deep(f.snapshot);
    snapshot.windows.find(w => w.identity === f.plan.moves[0].identity).rect = deep(frame);
    const registry = context.Registry.reconcile(snapshot, context.Registry.createRegistryState("transaction-test"), {
        sessionEpoch: "transaction-test", selectedOutputIds: ["DP-1"]
    });
    return { snapshot, registry };
}
function observe(f, frame, observationId, now = f.now + 3200) {
    const live = liveAt(f, frame);
    return { type: "geometryObserved", generation: f.state.generation, observationId, now,
        accepted: true, geometry: frame, snapshot: live.snapshot, registry: live.registry,
        currentSettingsRevision: f.settingsRevision, safety: gate(now), safetyMaxAgeMs: 1000 };
}

// The controller starts paused, and a valid one-window transaction only moves
// after the warning is shown, its deadline passes, it is hidden, and a fresh
// snapshot passes all guards.
{
    const f = fixture();
    assert.equal(f.state.phase, "Paused");
    assert.deepEqual(resumeAndBegin(f).map(c => c.type), ["showWarning"]);
    assert.equal(f.state.phase, "Warning");
    const warning = f.state.pendingCommand;
    const unshown = fixture(), unshownCommands = resumeAndBegin(unshown), unshownCommand = unshownCommands[0];
    transition(unshown, { type: "warningDeadline", generation: unshown.state.generation,
        commandId: unshownCommand.id, now: unshown.now + 3000 });
    assert.equal(unshown.state.phase, "Paused", "unconfirmed warning must fail closed");
    assert.equal(unshown.state.pauseReason, "warning-not-shown");
    transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: warning.id, now: f.now + 2999 });
    assert.equal(f.state.phase, "Warning", "early timer must not end warning");
    transition(f, ack(f, [{ type: "showWarning", id: warning.id }], "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: warning.id, now: f.now + 3000 });
    assert.deepEqual(hide.map(c => c.type), ["hideWarning"]);
    const request = transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    assert.deepEqual(request.map(c => c.type), ["requestSnapshot"]);
    const write = freshSnapshot(f);
    assert.deepEqual(write.map(c => c.type), ["writeGeometry"]);
    assert.deepEqual(write[0].destination, f.plan.moves[0].toRect);
    assert.equal(f.state.phase, "Applying");
}

// Skip is an explicit cancellation. Failed warning creation/removal cannot
// reach snapshot validation or issue a geometry command.
{
    const f = fixture(), show = resumeAndBegin(f), cmd = show[0];
    transition(f, { type: "skip", generation: f.state.generation, commandId: cmd.id });
    assert.equal(f.state.phase, "Paused");
    assert.equal(f.state.lastResult.status, "cancelled");
    const failedShow = fixture(), shown = resumeAndBegin(failedShow);
    transition(failedShow, ack(failedShow, shown, "showWarning", { type: "warningShown", shown: false }));
    assert.equal(failedShow.state.phase, "Paused");
    const failedHide = fixture(), visible = resumeAndBegin(failedHide);
    transition(failedHide, ack(failedHide, visible, "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(failedHide, { type: "warningDeadline", generation: failedHide.state.generation, commandId: visible[0].id, now: failedHide.now + 3000 });
    transition(failedHide, ack(failedHide, hide, "hideWarning", { type: "warningHidden", hidden: false }));
    assert.equal(failedHide.state.phase, "Paused");
    assert.notEqual(failedHide.state.lastResult.status, "committed");
    const activityDeadline = fixture(), warning = resumeAndBegin(activityDeadline);
    transition(activityDeadline, ack(activityDeadline, warning, "showWarning", { type: "warningShown", shown: true }));
    const cancelled = transition(activityDeadline, { type: "activity", reason: "input-at-warning-deadline" });
    assert.equal(activityDeadline.state.phase, "Paused");
    assert.equal(activityDeadline.state.lastResult.status, "cancelled");
    assert.equal(cancelled.some(c => c.type === "writeGeometry"), false);
}

// Every relevant change after the warning cancels before the write: focus,
// obstacle/window-set changes, a closed target, activity, held buttons,
// settings revisions, and output removal.
{
    const staleCases = [
        ["focus", f => makeSnapshot([makeWindow("w1", rect(100, 100, 100, 100), { focused: true })], [deep(output)], { focusedIdentity: "w1" })],
        ["obstacle", f => makeSnapshot([makeWindow(), makeWindow("new", rect(500, 400, 100, 100), { movable: false })])],
        ["closed window", f => makeSnapshot([])],
        ["output removal", f => makeSnapshot([makeWindow("w1", rect(100, 100, 100, 100), { outputIds: null })], [])]
    ];
    for (const [label, changed] of staleCases) {
        const f = fixture(), request = reachPreWrite(f), changedSnapshot = changed(f);
        const changedRegistry = context.Registry.reconcile(changedSnapshot, context.Registry.createRegistryState("transaction-test"), {
            sessionEpoch: "transaction-test", selectedOutputIds: ["DP-1"]
        });
        const writes = freshSnapshot(f, changedSnapshot, changedRegistry);
        assert.equal(writes.some(c => c.type === "writeGeometry"), false, label + " must block write");
        assert.equal(f.state.phase, "Paused", label + " must pause");
        void request;
    }
    for (const [label, safetyChange] of [["activity", { allOutputsQuiet: false }], ["held button", { heldButtons: true }]]) {
        const f = fixture(); reachPreWrite(f);
        const writes = freshSnapshot(f, f.snapshot, f.registry, { safety: gate(f.now + 3100, safetyChange) });
        assert.equal(writes.some(c => c.type === "writeGeometry"), false, label + " must block write");
        assert.equal(f.state.phase, "Paused");
    }
    const changedSettings = fixture(); reachPreWrite(changedSettings);
    const noWrite = freshSnapshot(changedSettings, changedSettings.snapshot, changedSettings.registry, { currentSettingsRevision: "settings-2" });
    assert.equal(noWrite.some(c => c.type === "writeGeometry"), false);
    const reconnected = fixture(); reachPreWrite(reconnected);
    const sameIdsNewGeneration = freshSnapshot(reconnected, reconnected.snapshot, reconnected.registry,
        { safety: gate(reconnected.now + 3100, { topologyGeneration: 2 }) });
    assert.equal(sameIdsNewGeneration.some(c => c.type === "writeGeometry"), false);
    assert.equal(reconnected.state.phase, "Paused");
}

// Generation and command IDs make duplicate and late callbacks inert.
{
    const f = fixture(), show = resumeAndBegin(f), oldGeneration = f.state.generation, showId = show[0].id;
    transition(f, { type: "pause", reason: "test-cancel" });
    const late = transition(f, { type: "warningShown", generation: oldGeneration, commandId: showId, shown: true });
    assert.deepEqual(late, []);
    assert.equal(f.state.phase, "Paused");
    const resumed = transition(f, { type: "resume" });
    assert.deepEqual(resumed, []);
    const wrongCommand = transition(f, { type: "begin", generation: f.state.generation, now: f.now, snapshot: f.snapshot,
        registry: f.registry, plan: f.plan, settingsRevision: f.settingsRevision, currentSettingsRevision: f.settingsRevision,
        safety: gate(f.now), warningAvailable: true, warningMs: 3000 });
    assert.equal(wrongCommand.length, 1);
    const duplicate = transition(f, { type: "warningShown", generation: f.state.generation, commandId: "not-the-command", shown: true });
    assert.deepEqual(duplicate, []);
}

// A rejected assignment, quantized geometry, or a configure that arrives only
// after the settlement deadline cannot be reported as success.
{
    for (const scenario of ["rejected", "quantized", "delayed configure"]) {
        const f = fixture(), request = reachPreWrite(f), writes = freshSnapshot(f);
        if (scenario === "rejected") {
            transition(f, ack(f, writes, "writeGeometry", { type: "writeAck", accepted: false }));
            assert.equal(f.state.phase, "Paused");
            assert.equal(f.state.lastResult.status, "failed");
        } else {
            transition(f, ack(f, writes, "writeGeometry", { type: "writeAck", accepted: true }));
            if (scenario === "quantized") {
                const quantized = rect(writes[0].destination.x + 2, writes[0].destination.y, 100, 100);
                const rollbackRequest = transition(f, observe(f, quantized, "obs-1"));
                assert.ok(rollbackRequest.some(c => c.type === "requestSnapshot" && c.purpose === "rollback"));
                assert.equal(f.state.phase, "RollingBack");
                const rb = f.state.pendingCommand;
                transition(f, { type: "snapshotReady", generation: f.state.generation, commandId: rb.id,
                    purpose: "rollback", now: f.now + 3250, snapshot: liveAt(f, quantized).snapshot,
                    registry: liveAt(f, quantized).registry, currentSettingsRevision: f.settingsRevision,
                    safety: gate(f.now + 3250), safetyMaxAgeMs: 1000 });
                assert.equal(f.state.phase, "Paused", "quantized external frame must prevent reversal");
                assert.equal(f.state.lastResult.reason, "external-frame-won");
                assert.notEqual(f.state.lastResult.status, "committed");
            } else {
                const deadline = { type: "settlementDeadline", generation: f.state.generation,
                    commandId: writes[0].id, now: f.now + 4000 };
                const rollbackRequest = transition(f, deadline);
                assert.equal(f.state.phase, "RollingBack");
                assert.ok(rollbackRequest.some(c => c.type === "requestSnapshot" && c.purpose === "rollback"));
                const rollbackSnapshot = f.state.pendingCommand, live = liveAt(f, writes[0].expectedFrom);
                const reverse = transition(f, { type: "snapshotReady", generation: f.state.generation,
                    commandId: rollbackSnapshot.id, purpose: "rollback", now: f.now + 4050,
                    snapshot: live.snapshot, registry: live.registry, currentSettingsRevision: f.settingsRevision,
                    safety: gate(f.now + 4050), safetyMaxAgeMs: 1000 });
                assert.equal(reverse.some(c => c.type === "writeGeometry" && c.rollback === true), false,
                    "delayed configure still at source must not reverse-write");
                assert.equal(f.state.phase, "Paused");
                assert.equal(f.state.lastResult.status, "failed");
                const tooLate = transition(f, observe(f, writes[0].destination, "late", f.now + 4001));
                assert.deepEqual(tooLate, []);
            }
        }
        void request;
    }
}

// Independent observations are required for commit; duplicate observation IDs
// do not satisfy readback. The pure API exposes only a geometry command.
{
    const f = fixture(), show = resumeAndBegin(f), forbidden = new Set(["activate", "raise", "desktop", "activity"]);
    assert.equal(show.some(c => forbidden.has(c.type)), false);
    transition(f, ack(f, show, "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: show[0].id, now: f.now + 3000 });
    transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    const write = freshSnapshot(f);
    assert.deepEqual(write.map(c => c.type), ["writeGeometry"]);
    transition(f, ack(f, write, "writeGeometry", { type: "writeAck", accepted: true }));
    const observed = () => observe(f, write[0].destination, "same-observation", f.now + 3200);
    transition(f, observed());
    transition(f, observed());
    assert.equal(f.state.phase, "Settling");
    transition(f, Object.assign(observed(), { observationId: "second-observation", now: f.now + 3300, safety: gate(f.now + 3300) }));
    assert.equal(f.state.phase, "Waiting");
    assert.equal(f.state.lastResult.status, "committed");
}

// A user move during settling is preserved. The executor must never send a
// second geometry command as an implicit retry or reverse operation.
{
    // An accepted destination may be reversed only while a fresh snapshot still
    // shows that exact destination and the inverse plan remains safe.
    const safe = fixture(), shown = resumeAndBegin(safe);
    transition(safe, ack(safe, shown, "showWarning", { type: "warningShown", shown: true }));
    const hideSafe = transition(safe, { type: "warningDeadline", generation: safe.state.generation,
        commandId: shown[0].id, now: safe.now + 3000 });
    transition(safe, ack(safe, hideSafe, "hideWarning", { type: "warningHidden", hidden: true }));
    const applied = freshSnapshot(safe);
    transition(safe, ack(safe, applied, "writeGeometry", { type: "writeAck", accepted: true }));
    const destination = applied[0].destination;
    const failedRead = observe(safe, destination, "bad-observation");
    failedRead.safety = gate(failedRead.now, { allOutputsQuiet: false });
    const rollback = transition(safe, failedRead);
    assert.ok(rollback.some(c => c.type === "requestSnapshot" && c.purpose === "rollback"));
    const rollbackSnapshot = safe.state.pendingCommand, destinationLive = liveAt(safe, destination);
    const reverse = transition(safe, { type: "snapshotReady", generation: safe.state.generation,
        commandId: rollbackSnapshot.id, purpose: "rollback", now: safe.now + 3250,
        snapshot: destinationLive.snapshot, registry: destinationLive.registry,
        currentSettingsRevision: safe.settingsRevision, safety: gate(safe.now + 3250), safetyMaxAgeMs: 1000 });
    const reverseWrite = reverse.find(c => c.type === "writeGeometry");
    assert.ok(reverseWrite && reverseWrite.rollback === true, "unchanged accepted destination allows guarded reverse");
    assert.deepEqual(reverseWrite.expectedFrom, destination);
    assert.deepEqual(reverseWrite.destination, safe.plan.moves[0].fromRect);

    const f = fixture(), show = resumeAndBegin(f);
    transition(f, ack(f, show, "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: show[0].id, now: f.now + 3000 });
    transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    const write = freshSnapshot(f);
    transition(f, ack(f, write, "writeGeometry", { type: "writeAck", accepted: true }));
    const userFrame = rect(700, 600, 100, 100);
    const followup = transition(f, observe(f, userFrame, "user-edit"));
    assert.equal(f.state.phase, "RollingBack");
    assert.equal(followup.some(c => c.type === "writeGeometry"), false);
    const rb = f.state.pendingCommand, live = liveAt(f, userFrame);
    const noReverse = transition(f, { type: "snapshotReady", generation: f.state.generation, commandId: rb.id,
        purpose: "rollback", now: f.now + 3250, snapshot: live.snapshot, registry: live.registry,
        currentSettingsRevision: f.settingsRevision, safety: gate(f.now + 3250), safetyMaxAgeMs: 1000 });
    assert.equal(noReverse.some(c => c.type === "writeGeometry" && c.rollback === true), false);
    assert.equal(f.state.phase, "Paused");
    assert.equal(f.state.lastResult.reason, "external-frame-won");
}

// The countdown begins at confirmed presentation, and a long settlement
// request cannot turn a bounded readback into an unbounded wait.
{
    const f = fixture(), show = resumeAndBegin(f);
    transition(f, ack(f, show, "showWarning", { type: "warningShown", shown: true, now: f.now + 500 }));
    const early = transition(f, { type: "warningDeadline", generation: f.state.generation,
        commandId: show[0].id, now: f.now + 3000 });
    assert.deepEqual(early, []);
    assert.equal(f.state.phase, "Warning");
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation,
        commandId: show[0].id, now: f.now + 3500 });
    assert.deepEqual(hide.map(c => c.type), ["hideWarning"]);
    transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    const noWrite = freshSnapshot(f, f.snapshot, f.registry, { now: f.now + 3600, safety: gate(f.now + 3600), settlementMs: 100000 });
    assert.equal(noWrite.some(c => c.type === "writeGeometry"), false);
    assert.equal(f.state.phase, "Paused");
}

// A compositor-adjusted frame can be within readback tolerance yet outside
// the configured strict normalized range. It must not become a committed move.
{
    const f = fixture();
    f.plan.metadata.shiftRangePercent = 1;
    f.plan.moves[0].toRect = rect(110, 100, 100, 100);
    const show = resumeAndBegin(f);
    assert.equal(show[0].type, "showWarning");
    transition(f, ack(f, show, "showWarning", { type: "warningShown", shown: true }));
    const hide = transition(f, { type: "warningDeadline", generation: f.state.generation, commandId: show[0].id, now: f.now + 3000 });
    transition(f, ack(f, hide, "hideWarning", { type: "warningHidden", hidden: true }));
    const write = freshSnapshot(f);
    transition(f, ack(f, write, "writeGeometry", { type: "writeAck", accepted: true }));
    const roundedBeyondRange = rect(110.5, 100, 100, 100);
    const next = transition(f, observe(f, roundedBeyondRange, "rounded"));
    assert.equal(next.some(c => c.type === "requestSnapshot" && c.purpose === "rollback"), true);
    assert.notEqual(f.state.lastResult && f.state.lastResult.status, "committed");
}

// A guarded reverse remains a failure until its own assignment and two
// temporally separate readbacks confirm that the source was restored.
{
    const f = fixture(); reachPreWrite(f);
    const write = freshSnapshot(f);
    transition(f, ack(f, write, "writeGeometry", { type: "writeAck", accepted: true }));
    const failedRead = observe(f, write[0].destination, "unsafe-read");
    failedRead.safety = gate(failedRead.now, { heldButtons: true });
    const rollbackRequest = transition(f, failedRead);
    const destinationLive = liveAt(f, write[0].destination);
    const reverse = transition(f, { type: "snapshotReady", generation: f.state.generation,
        commandId: rollbackRequest[0].id, purpose: "rollback", now: f.now + 3250,
        snapshot: destinationLive.snapshot, registry: destinationLive.registry,
        currentSettingsRevision: f.settingsRevision, safety: gate(f.now + 3250) });
    assert.equal(f.state.phase, "RollingBack");
    assert.equal(reverse.length, 1);
    transition(f, ack(f, reverse, "writeGeometry", { type: "writeAck", accepted: true }));
    const source = f.plan.moves[0].fromRect;
    transition(f, observe(f, source, "reverse-1", f.now + 3300));
    assert.equal(f.state.phase, "RollingBack");
    transition(f, observe(f, source, "reverse-too-soon", f.now + 3301));
    assert.equal(f.state.phase, "RollingBack");
    transition(f, observe(f, source, "reverse-2", f.now + 3320));
    assert.equal(f.state.phase, "Paused");
    assert.equal(f.state.lastResult.status, "failed-rolled-back");
}

console.log("core transaction: adversarial fixtures passed");

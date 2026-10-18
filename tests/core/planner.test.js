"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const modelSource = fs.readFileSync("core/model.js", "utf8").replace(/^\.pragma library\s*/m, "");
const registrySource = fs.readFileSync("core/registry.js", "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import ".*" as Model\s*/m, "");
const context = {};
vm.runInNewContext(modelSource + "\nthis.Model = { applicationExcluded, roundingResizeAllowed,roundingContactAllowed,movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, validateSnapshot, createPlan, validatePlan };\n" + registrySource + "\nthis.Registry = { createRegistryState, reconcile, snapshotSignature };\n" + fs.readFileSync("core/planner.js", "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import ".*" as Model\s*/m, "").replace(/^\.import ".*" as Registry\s*/m, "") + "\nthis.api = { planOrdinaryShift, validateFinalPlan };", context);
const api = context.api;
const registry = context.Registry;
const strictDeepEqual = assert.deepStrictEqual.bind(assert);
assert.deepEqual = (actual, expected, message) => strictDeepEqual(JSON.parse(JSON.stringify(actual)), JSON.parse(JSON.stringify(expected)), message);
const rect = (x, y, width, height) => ({ x, y, width, height });
const output = (id = "DP-1", bounds = rect(0, 0, 1000, 800)) => ({ id, connector: id, edid: "edid-" + id, name: id, geometry: bounds, usableRect: bounds, scale: 1 });
const win = (identity, frame, overrides = {}) => Object.assign({
    identity, appId: "app", title: "private", pid: 1, outputIds: ["DP-1"], rect: frame,
    visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false,
    managed: true, modal: false, transient: false, movable: true, resizable: true,
    minimized: false, maximized: false, fullscreen: false, tiled: false,
    windowType: "normal", skipReasons: []
}, overrides);
const snapshot = (windows, outputs = [output()], extra = {}) => Object.assign({ outputs, windows, focusedIdentity: null, topologyRevision: "r1" }, extra);
function setup(snap, outputIds = ["DP-1"], registryOptions = {}) {
    return registry.reconcile(snap, registry.createRegistryState("planner-test"), Object.assign({ sessionEpoch: "planner-test", selectedOutputIds: outputIds }, registryOptions));
}
function plan(snap, state, options = {}) {
    return api.planOrdinaryShift(snap, state, Object.assign({ outputId: "DP-1", shiftRangePercent: 100, maximumWindows: 4, random: () => 0.37, maxEvaluations: 5000, now: () => 10 }, options));
}
function assertSafe(snap, state, result) {
    const checked = api.validateFinalPlan(snap, state, result.plan);
    assert.equal(checked.valid, true, JSON.stringify(checked.errors));
    const moves = result.plan.moves;
    assert.equal(new Set(moves.map(m => m.identity)).size, moves.length);
    const outputRecord = snap.outputs.find(o => o.id === "DP-1");
    for (const move of moves) {
        const original = snap.windows.find(w => w.identity === move.identity);
        assert.ok(original);
        assert.deepEqual(move.fromRect, original.rect);
        assert.deepEqual(move.toRect.width, original.rect.width);
        assert.deepEqual(move.toRect.height, original.rect.height);
        assert.ok(move.toRect.x !== original.rect.x || move.toRect.y !== original.rect.y);
        assert.ok(move.toRect.x >= original.rect.x - outputRecord.usableRect.width);
        assert.ok(move.toRect.x + move.toRect.width <= outputRecord.usableRect.x + outputRecord.usableRect.width);
        assert.ok(move.toRect.y >= outputRecord.usableRect.y);
        assert.ok(move.toRect.y + move.toRect.height <= outputRecord.usableRect.y + outputRecord.usableRect.height);
    }
    for (let i = 0; i < moves.length; i++) for (let j = i + 1; j < moves.length; j++)
        assert.equal(context.rectangleOverlaps(moves[i].toRect, moves[j].toRect), false);
    return moves;
}

// Range is normalized against usable dimensions, clamped to [1,100], and both axes
// together obey the unit-circle cap while each move preserves its own size.
{
    const snap = snapshot([win("a", rect(400, 300, 120, 80))]);
    const state = setup(snap);
    for (const requested of [1, 10, 10.5, 100]) {
        const result = plan(snap, state, { shiftRangePercent: requested, random: () => 0.99 });
        const moves = assertSafe(snap, state, result);
        assert.ok(moves.length <= 1);
        for (const m of moves) {
            const dx = (m.toRect.x - m.fromRect.x) / 1000;
            const dy = (m.toRect.y - m.fromRect.y) / 800;
            const effective = requested / 100;
            assert.ok(Math.hypot(dx, dy) <= effective + 1e-9);
        }
    }
    for (const invalidRange of [-100, 0, 100.1, 1000])
        assert.throws(() => plan(snap, state, { shiftRangePercent: invalidRange }), /range|percent|invalid/i);
}

// The selected output scopes movement; a candidate on another output stays stationary.
{
    const outputs = [output(), output("DP-2", rect(1000, 0, 1000, 800))];
    const snap = snapshot([win("a", rect(100, 100, 120, 80)), win("b", rect(1100, 100, 120, 80), { outputIds: ["DP-2"] })], outputs);
    const state = setup(snap, ["DP-1", "DP-2"]);
    const result = plan(snap, state, { maximumWindows: 2 });
    assert.deepEqual(assertSafe(snap, state, result).map(m => m.identity), ["a"]);
}

// Existing/unsupported/spanning visible windows remain obstacles; final destinations
// cannot collide with them, and pre-existing overlaps are left alone.
{
    const snap = snapshot([
        win("candidate", rect(100, 100, 100, 100)),
        win("stationary", rect(300, 100, 160, 160), { movable: false }),
        win("span", rect(700, 100, 250, 200), { outputIds: ["DP-1", "DP-2"] })
    ], [output(), output("DP-2", rect(1000, 0, 1000, 800))]);
    const state = setup(snap);
    const result = plan(snap, state, { shiftRangePercent: 100 });
    const moves = assertSafe(snap, state, result);
    for (const m of moves) {
        assert.equal(context.rectangleOverlaps(m.toRect, snap.windows[1].rect), false);
        assert.equal(context.rectangleOverlaps(m.toRect, snap.windows[2].rect), false);
    }
    const overlapped = snapshot([win("left", rect(100, 100, 200, 150)), win("right", rect(250, 120, 200, 150))]);
    const overlapResult = plan(overlapped, setup(overlapped));
    assert.equal(overlapResult.plan.moves.length, 0);
}

// Candidate order is randomized reproducibly, while both planning work and the
// number of moved windows stay within explicit budgets.
{
    const windows = Array.from({ length: 5 }, (_, i) => win("w" + i, rect(30 + i * 180, 100, 90, 70)));
    const snap = snapshot(windows), state = setup(snap);
    const a = plan(snap, state, { maximumWindows: 2, random: () => 0.1 });
    const b = plan(snap, state, { maximumWindows: 2, random: () => 0.9 });
    assert.ok(a.plan.moves.length <= 2 && b.plan.moves.length <= 2);
    assert.ok(a.stats.evaluations <= 5000 && b.stats.evaluations <= 5000);
    assert.ok(a.plan.moves.length > 0);
    assert.notDeepEqual(a.plan.moves.map(m => m.identity), b.plan.moves.map(m => m.identity));
    assertSafe(snap, state, a); assertSafe(snap, state, b);
}

// Previous positions are avoided when another safe destination exists.
{
    const snap = snapshot([win("a", rect(400, 300, 100, 100))]);
    const state = setup(snap);
    state.windows[0].previousFrame = rect(200, 300, 100, 100);
    const result = plan(snap, state, { random: () => 0.5 });
    for (const m of assertSafe(snap, state, result)) assert.notDeepEqual(m.toRect, state.windows[0].previousFrame);
}

// Gaussian amplitude uses the documented clipped distribution m=clamp(0.6+N(0,.8/3),.2,1).
// A seeded batch checks broad stable moments and the hard clipping bounds.
{
    const snap = snapshot([win("a", rect(400, 300, 100, 100))]);
    const state = setup(snap);
    const random = (() => { let x = 0x12345678; return () => { x = (1664525 * x + 1013904223) >>> 0; return x / 4294967296; }; })();
    const amplitudes = [];
    for (let i = 0; i < 160; i++) {
        const result = plan(snap, state, { random });
        const move = result.plan.moves[0];
        if (!move) continue;
        const normalized = Math.hypot((move.toRect.x - move.fromRect.x) / 1000, (move.toRect.y - move.fromRect.y) / 800);
        amplitudes.push(normalized);
        assert.ok(normalized <= 1 + 1e-9);
    }
    assert.ok(amplitudes.length > 120);
    const mean = amplitudes.reduce((a, b) => a + b, 0) / amplitudes.length;
    assert.ok(mean > 0.48 && mean < 0.78, "clipped Gaussian sample mean: " + mean);
}

// Large legal destinations should be used; crowded/no-solution, cancellation and
// evaluation exhaustion return a valid safe partial plan without throwing.
{
    const spacious = snapshot([win("a", rect(430, 330, 100, 100))]);
    const spaciousResult = plan(spacious, setup(spacious), { shiftRangePercent: 20, random: () => 0.5 });
    for (const m of assertSafe(spacious, setup(spacious), spaciousResult))
        assert.ok(Math.hypot((m.toRect.x - m.fromRect.x) / 1000, (m.toRect.y - m.fromRect.y) / 800) >= 0.015);
    const crowded = snapshot([win("a", rect(0, 0, 1000, 800), { movable: false })]);
    const crowdedResult = plan(crowded, setup(crowded));
    assert.equal(crowdedResult.plan.moves.length, 0);
    assert.equal(crowdedResult.stats.complete, true);
    const cancelled = plan(spacious, setup(spacious), { shouldCancel: () => true });
    assert.equal(cancelled.plan.moves.length, 0);
    assert.equal(cancelled.stats.complete, false);
    const bounded = plan(spacious, setup(spacious), { maxEvaluations: 1 });
    assert.equal(bounded.plan.moves.length, 0);
    assert.ok(bounded.stats.evaluations <= 1);
    assert.equal(bounded.stats.complete, false);
}

// Anchor frames remain origin metadata while ordinary movement may roam from lastFrame.
{
    const snap = snapshot([win("a", rect(400, 300, 100, 100))]);
    const state = setup(snap);
    state.windows[0].originFrame = rect(10, 20, 100, 100);
    state.windows[0].lastFrame = rect(400, 300, 100, 100);
    const result = plan(snap, state, { random: () => 0.4 });
    assertSafe(snap, state, result);
    assert.deepEqual(state.windows[0].originFrame, rect(10, 20, 100, 100));
}

// Invalid snapshots fail clearly; a plan from a different geometry is rejected as stale.
{
    const invalid = snapshot([win("bad", rect(0, 0, -1, 100))]);
    assert.throws(() => plan(invalid, setup(snapshot([]))), /invalid|snapshot/i);
    const snap = snapshot([win("a", rect(100, 100, 100, 100))]);
    const state = setup(snap), result = plan(snap, state);
    const changed = snapshot([win("a", rect(120, 100, 100, 100))]);
    const checked = api.validateFinalPlan(changed, setup(changed), result.plan);
    assert.equal(checked.valid, false);
    assert.ok(checked.errors.length > 0);
    const focused = snapshot([win("a", rect(100, 100, 100, 100), { focused: true })]);
    const staleFocus = api.validateFinalPlan(focused, setup(focused, ["DP-1"], { excludeFocused: true }), result.plan);
    assert.equal(staleFocus.valid, false);
    assert.ok(staleFocus.errors.length > 0);
    const unresolved = snapshot([win("a", rect(100, 100, 100, 100))], [output()], { focusedIdentity: "missing" });
    assert.throws(() => plan(unresolved, setup(unresolved)), /focus|mismatch/i);
    assert.equal(api.validateFinalPlan(unresolved, setup(unresolved), result.plan).valid, false);
}

// If obstacles leave the previous frame as the sole legal destination, use it as
// a fallback after seeking alternatives.
{
    const candidate = win("fallback", rect(400, 300, 100, 100));
    const blockers = [
        win("left-wall", rect(0, 300, 200, 100), { movable: false }),
        win("middle-wall", rect(300, 300, 100, 100), { movable: false }),
        win("right-wall", rect(500, 300, 500, 100), { movable: false }),
        win("top-wall", rect(200, 0, 300, 300), { movable: false }),
        win("bottom-wall", rect(200, 400, 300, 400), { movable: false })
    ];
    const snap = snapshot([candidate].concat(blockers));
    const state = setup(snap);
    state.windows.find(w => w.identity === "fallback").previousFrame = rect(200, 300, 100, 100);
    const result = plan(snap, state, { random: () => 0.37 });
    const moves = assertSafe(snap, state, result);
    assert.equal(moves.length, 1);
    assert.deepEqual(moves[0].toRect, rect(200, 300, 100, 100));
}

// When every sampled direction is blocked, obstacle-edge alignment finds a safe
// destination beyond the obstacle.
{
    const snap = snapshot([
        win("candidate", rect(100, 100, 100, 100)),
        win("wall", rect(210, 0, 100, 800), { movable: false })
    ]);
    const state = setup(snap);
    let calls = 0;
    const blockedDirections = () => {
        // No shuffle draw for a single candidate; gaussian u=.5,v=0 gives a
        // substantial sample, then all random directions point into the wall.
        calls++;
        if (calls === 1) return 0.5;
        if (calls === 2) return 0;
        return calls % 2 === 1 ? 0 : 0.99;
    };
    const result = plan(snap, state, { random: blockedDirections, shiftRangePercent: 10 });
    const moves = assertSafe(snap, state, result);
    assert.equal(moves.length, 1);
    assert.equal(moves[0].toRect.x, 110);
    assert.equal(moves[0].toRect.y, 100);
}

// The independent validator rejects forged sources, no-op moves, destinations
// outside the configured range, and collisions with stationary visible windows.
{
    const snap = snapshot([
        win("moving", rect(100, 100, 100, 100)),
        win("stationary", rect(700, 500, 100, 100), { movable: false })
    ]);
    const state = setup(snap);
    const result = plan(snap, state, { random: () => 0.5 });
    assert.ok(result.plan.moves.length > 0);
    const cloned = () => JSON.parse(JSON.stringify(result.plan));
    const forged = cloned();
    forged.moves[0].fromRect.x += 1;
    assert.equal(api.validateFinalPlan(snap, state, forged).valid, false);
    const noop = cloned();
    noop.moves[0].toRect = JSON.parse(JSON.stringify(noop.moves[0].fromRect));
    assert.equal(api.validateFinalPlan(snap, state, noop).valid, false);
    const outOfRange = cloned();
    outOfRange.moves[0].toRect.x = 950;
    assert.equal(api.validateFinalPlan(snap, state, outOfRange).valid, false);
    const collision = cloned();
    collision.moves[0].toRect = JSON.parse(JSON.stringify(snap.windows[1].rect));
    assert.equal(api.validateFinalPlan(snap, state, collision).valid, false);
}

// An app identity change with identical geometry makes the registry stale.
{
    const snap = snapshot([win("a", rect(100, 100, 100, 100))]);
    const state = setup(snap);
    const result = plan(snap, state);
    const changedApp = snapshot([win("a", rect(100, 100, 100, 100), { appId: "different.app" })]);
    assert.throws(() => plan(changedApp, state), /mismatch/i);
    assert.equal(api.validateFinalPlan(changedApp, state, result.plan).valid, false);
}

// Deadlines and evaluation limits stop bounded search cleanly, and public traces
// contain only opaque identity/reason/geometry data, never title or PID fields.
{
    const snap = snapshot([win("private-window", rect(400, 300, 100, 100))]);
    const state = setup(snap);
    const deadline = plan(snap, state, { deadlineMs: 0 });
    assert.equal(deadline.stats.complete, false);
    assert.equal(deadline.stats.stopReason, "deadline_exceeded");
    assert.equal(deadline.plan.moves.length, 0);
    const exhausted = plan(snap, state, { maxEvaluations: 1 });
    assert.equal(exhausted.stats.complete, false);
    assert.equal(exhausted.stats.stopReason, "evaluation_budget_exhausted");
    assert.ok(exhausted.stats.evaluations <= 1);
    const result = plan(snap, state);
    const traceText = JSON.stringify(result.trace);
    assert.equal(traceText.includes("secret title"), false);
    assert.equal(traceText.includes("title"), false);
    assert.equal(traceText.includes("pid"), false);
}
console.log("core planner: tests passed");

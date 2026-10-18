"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const { evaluate, generation, REQUIRED_ACTIVITY } = require("../../probes/safety/safety_gate.js");
const context = {};
vm.runInNewContext(fs.readFileSync("core/safety.js", "utf8").replace(/^\.pragma library\s*/m, "") +
    "\nthis.coreEvaluate = evaluate;", context);
const evaluateCore = (snapshot, now, maxAgeMs) => JSON.parse(JSON.stringify(context.coreEvaluate(snapshot, now, maxAgeMs)));

function safeSnapshot() {
    return {
        observedAt: 10_000,
        companion: "connected",
        locked: false,
        suspended: false,
        topologyStable: true,
        topologyGeneration: 4,
        topologyOutputIds: ["A"],
        allOutputsQuiet: true,
        heldButtons: false,
        outputs: [{ id: "A", known: true, quiet: true, buttonsDown: false }],
        quietSince: 8_000,
        quietMs: 1_000,
        capabilities: Object.fromEntries(REQUIRED_ACTIVITY.map(k => [k, true]))
    };
}

assert.deepEqual(evaluate(safeSnapshot(), 10_000, 1_000), { safe: true, reason: "safe" });
assert.deepEqual(evaluateCore(safeSnapshot(), 10_000, 1_000), { safe: true, reason: "safe" });
for (const field of ["keyboard", "pointer", "scroll", "buttonState"]) {
    const state = safeSnapshot();
    state.capabilities[field] = false;
    assert.equal(evaluate(state, 10_000, 1_000).safe, false, `${field} coverage must gate writes`);
    assert.equal(evaluateCore(state, 10_000, 1_000).safe, false, `${field} core coverage must gate writes`);
}
for (const patch of [
    { allOutputsQuiet: false }, { allOutputsQuiet: undefined },
    { heldButtons: true }, { heldButtons: undefined },
    { locked: true }, { locked: undefined }, { suspended: true },
    { topologyStable: false }, { companion: "lost" }, { observedAt: 8_999 },
    { quietSince: 9_500 }, { outputs: [{ id: "B", known: false, quiet: true, buttonsDown: false }] },
    { outputs: [{ id: "A", known: true, quiet: false, buttonsDown: false }] },
    { outputs: [{ id: "A", known: true, quiet: true, buttonsDown: true }] }, { outputs: [] },
    { quietMs: undefined }, { quietMs: -1 }, { quietSince: 10_001 },
    { topologyOutputIds: undefined }, { topologyGeneration: undefined },
    { topologyOutputIds: ["A", "B"] },
    { outputs: [
        { id: "A", known: true, quiet: true, buttonsDown: false },
        { id: "A", known: true, quiet: true, buttonsDown: false }
    ] },
    { topologyOutputIds: ["A", "A"] },
    { outputs: [{ id: "B", known: true, quiet: true, buttonsDown: false }] }
]) {
    assert.equal(evaluate({ ...safeSnapshot(), ...patch }, 10_000, 1_000).safe, false,
        `unsafe/unknown state ${JSON.stringify(patch)} must suspend`);
    assert.deepEqual(evaluateCore({ ...safeSnapshot(), ...patch }, 10_000, 1_000),
        evaluate({ ...safeSnapshot(), ...patch }, 10_000, 1_000),
        `canonical core and Phase 1 probe must agree for ${JSON.stringify(patch)}`);
}
assert.equal(evaluate(null, 10_000, 1_000).safe, false);
assert.equal(evaluateCore(null, 10_000, 1_000).safe, false);

const epoch = generation();
const queuedCallback = epoch.current();
assert.equal(epoch.accepts(queuedCallback), true);
epoch.invalidate(); // lock, topology change, activity, or companion loss
assert.equal(epoch.accepts(queuedCallback), false, "stale callbacks cannot cross suspension");
console.log("safety gate fake-state checks passed");

"use strict";
// Reproducible synthetic workload; run manually to measure the pure search.
const fs = require("node:fs");
const vm = require("node:vm");
const { performance } = require("node:perf_hooks");

const source = name => fs.readFileSync(name, "utf8")
    .replace(/^\.pragma library\s*/m, "").replace(/^\.import ".*" as Model\s*/m, "")
    .replace(/^\.import ".*" as Registry\s*/m, "");
const context = {};
vm.runInNewContext(source("core/model.js") +
    "\nthis.Model = { applicationExcluded, roundingResizeAllowed,roundingContactAllowed,movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, validateSnapshot, createPlan, validatePlan };\n" +
    source("core/registry.js") + "\nthis.Registry = { createRegistryState, reconcile, snapshotSignature };\n" +
    source("core/planner.js") + "\nthis.api = { planOrdinaryShift, validateFinalPlan, seededRandom };", context);
const { api, Registry } = context;
const rect = (x, y, width, height) => ({ x, y, width, height });
const obstacleCount = process.argv[2] === undefined ? 24 : Number(process.argv[2]);
if (!Number.isInteger(obstacleCount) || obstacleCount < 0 || obstacleCount > 1000)
    throw new Error("obstacle count must be an integer from 0 to 1000");
const candidateCount = process.argv[3] === undefined ? 4 : Number(process.argv[3]);
if (!Number.isInteger(candidateCount) || candidateCount < 1 || candidateCount > 12)
    throw new Error("candidate count must be an integer from 1 to 12");
const windows = [];
function win(identity, frame, movable) {
    return { identity, appId: "benchmark", outputIds: ["virtual"], rect: frame,
        visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false,
        managed: true, modal: false, transient: false, movable, resizable: true,
        minimized: false, maximized: false, fullscreen: false, tiled: false,
        windowType: "normal", skipReasons: [] };
}
for (let i = 0; i < candidateCount; i++) windows.push(win("candidate-" + i, rect(i * 500 + 30, 100, 200, 160), true));
for (let i = 0; i < obstacleCount; i++) windows.push(win("obstacle-" + i,
    rect((i % 24) * 390 + 20, 5100 + Math.floor(i / 24) * 350, 180, 160), false));
const snapshot = { topologyRevision: "benchmark", focusedIdentity: null,
    outputs: [{ id: "virtual", geometry: rect(0, 0, 10000, 10000), usableRect: rect(0, 0, 10000, 10000), scale: 1 }], windows };
const state = Registry.reconcile(snapshot, Registry.createRegistryState("benchmark"),
    { sessionEpoch: "benchmark", selectedOutputIds: ["virtual"] });
const samples = [], evaluations = [], moved = [], completed = [];
for (let i = 0; i < 30; i++) {
    const start = performance.now();
    const result = api.planOrdinaryShift(snapshot, state, { outputId: "virtual",
        shiftRangePercent: 50, maximumWindows: 12, random: api.seededRandom(i + 1) });
    const checked = api.validateFinalPlan(snapshot, state, result.plan);
    if (result.stats.complete && !checked.valid) throw new Error("unsafe benchmark plan: " + checked.errors.join(", "));
    if (!result.stats.complete && (result.plan.moves.length || checked.valid)) throw new Error("incomplete plan was executable");
    samples.push(performance.now() - start);
    evaluations.push(result.stats.evaluations);
    moved.push(result.plan.moves.length);
    completed.push(result.stats.complete);
}
samples.sort((a, b) => a - b);
const percentile = p => samples[Math.floor((samples.length - 1) * p)].toFixed(2);
console.log("workload=" + candidateCount + " candidates, " + obstacleCount + " obstacles, 30 runs, default 2400-evaluation cap, default 30ms deadline");
console.log("median_ms=" + percentile(0.5) + " p95_ms=" + percentile(0.95) +
    " max_ms=" + samples[samples.length - 1].toFixed(2) +
    " max_evaluations=" + Math.max(...evaluations) + " complete=" + completed.filter(Boolean).length +
    " min_moves=" + Math.min(...moved));

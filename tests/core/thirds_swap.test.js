"use strict";
// Regression for adjacent thirds: similarity, exact packing, rigid neighbours,
// bounded rounding resize, and staging are distinct requirements.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
function load(file, imports, names) {
    const source = fs.readFileSync("core/" + file, "utf8")
        .replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/gm, "");
    const context = Object.assign({}, imports);
    vm.runInNewContext(source + "\nthis.api={" + names.join(",") + "};", context);
    return context.api;
}
const Model = load("model.js", {}, ["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain", "rectangle", "rectangleContains", "rectangleOverlaps", "createPlan", "validateSnapshot", "validatePlan"]);
const Registry = load("registry.js", { Model }, ["createRegistryState", "reconcile", "snapshotSignature"]);
const Planner = load("planner.js", { Model, Registry }, ["registryMatches", "focusResolved", "eligible", "rawObstacle", "validateSwapFinalPlan", "validateGroupCommonFinalPlan"]);
const Cycles = require("../../core/swap_cycles.js");
const FreeSwap = load("free_swap.js", {}, ["proposeFreeSwap"]);
const Groups = load("groups.js", { Model, Registry }, ["builtinGroups", "groupFrames", "groupsFromLibrary", "matchingZones", "allowedShrink"]);
const Rotation = require("../../core/group_rotation.js");
const GroupSafety = load("group_safety.js", { Model, Planner, Groups }, ["validateGroupFinalPlan"]);
const Steps = load("swap_steps.js", { Model, Planner, Groups, GroupSafety }, ["orderSwapSteps", "validateSwapSteps"]);
const Swap = load("swap.js", { Model, Planner, Cycles, FreeSwap, Steps }, ["planSwap"]);
const GroupExecution = load("group_execution.js", { Model, Planner, Groups, Rotation, GroupSafety, Steps }, ["planGroupMovement"]);
const rect = (x, y, width, height) => ({ x, y, width, height });
const win = (identity, frame) => ({ identity, appId: "app", outputIds: ["DP-1"], rect: frame,
    visible: true, onCurrentDesktop: true, onCurrentActivity: true, focused: false, managed: true,
    modal: false, transient: false, movable: true, resizable: true, minimized: false,
    maximized: false, fullscreen: false, tiled: false, windowType: "normal", skipReasons: [] });
function prepared(area, frames, scale = 1) {
    const snapshot = { outputs: [{ id: "DP-1", connector: "DP-1", edid: "edid", name: "display",
        geometry: area, usableRect: area, scale }], windows: frames.map((r, i) => win(String(i), r)),
        focusedIdentity: null, topologyRevision: "thirds" };
    const registry = Registry.reconcile(snapshot, Registry.createRegistryState("thirds"),
        { sessionEpoch: "thirds", selectedOutputIds: ["DP-1"] });
    return { snapshot, registry };
}
function packed(widths, x, y = 0) {
    return widths.map(width => { const frame = rect(x, y, width, 100); x += width; return frame; });
}
const opts = extra => Object.assign({ outputId: "DP-1", maximumWindows: 3, sizeTolerance: .1,
    allowTransientOverlap: true, keepSimilarWindowsTogether: false,
    random: () => .2, now: () => 0, deadlineMs: 1000, maxEvaluations: 50000 }, extra);
function validSwap(f, result) {
    assert.ok(result.plan, JSON.stringify(result));
    assert.equal(result.plan.moves.length, 3);
    assert.equal(Planner.validateSwapFinalPlan(f.snapshot, f.registry, result.plan).valid, true);
    assert.equal(Steps.validateSwapSteps(f.snapshot, f.registry, result.plan, result.steps).valid, true);
    for (const move of result.plan.moves) {
        assert.ok(Math.abs(move.toRect.width-move.fromRect.width)<=2 && Math.abs(move.toRect.height-move.fromRect.height)<=2);
        if (move.toRect.width!==move.fromRect.width || move.toRect.height!==move.fromRect.height) {
            assert.equal(move.permitsRoundingResize,true);
            assert.ok(Math.abs(move.fromRect.width-move.toRect.width)<=2 && Math.abs(move.fromRect.height-move.toRect.height)<=2);
        }
    }
}
let cases = 0;
for (const fractional of [false, true]) for (const wider of [0, 1, 2]) {
    const widths = fractional ? [400.25, 400.25, 400.25] : [400, 400, 400];
    widths[wider] += fractional ? .75 : 1;
    const x = fractional ? -1201.5 : 0, y = fractional ? -100.5 : 0;
    const width = widths.reduce((a, b) => a + b, 0);
    const frames = packed(widths, x, y);
    const f = prepared(rect(x, y, width, 100), frames, fractional ? 1.5 : 1);
    for (const frame of frames) assert.equal(Cycles.closeSize(frames[0], frame, .1), true);

    // Both cyclic permutations of the source origins are unsafe when the wider
    // frame moves. Exact repacking preserves sizes and succeeds in both directions.
    for (const direction of [1, -1]) {
        const moves = frames.map((r, i) => ({ identity: String(i), fromRect: r,
            toRect: rect(frames[(i + direction + 3) % 3].x, y, r.width, r.height) }));
        const metadata = { mode: "swap", maximumWindows: 3, complete: true, allowTransientOverlap: true };
        const unsafe = Model.createPlan({ snapshotRevision: "thirds", outputIds: ["DP-1"], moves, metadata });
        assert.equal(Planner.validateSwapFinalPlan(f.snapshot, f.registry, unsafe).valid, false);
        const order = direction === 1 ? [2, 0, 1] : [1, 2, 0];
        const targets = packed(order.map(i => widths[i]), x, y);
        const safeMoves = order.map((i, slot) => ({ identity: String(i), fromRect: frames[i], toRect: targets[slot] }));
        const plan = Model.createPlan({ snapshotRevision: "thirds", outputIds: ["DP-1"], moves: safeMoves, metadata });
        const ordered = Steps.orderSwapSteps(f.snapshot, f.registry, plan, opts());
        validSwap(f, { plan, steps: ordered.steps });
        plan.metadata.allowTransientOverlap = false;
        assert.equal(Steps.orderSwapSteps(f.snapshot, f.registry, plan, opts()).reason, "no-safe-step-order");
        const roomy = prepared(rect(x, y, width, 210), frames, fractional ? 1.5 : 1);
        const staged = Steps.orderSwapSteps(roomy.snapshot, roomy.registry, plan, opts());
        assert.ok(staged.steps.some(s => s.kind === "stage"));
        validSwap(roomy, { plan, steps: staged.steps });
        cases++;
    }
    for (const grouped of [false, true]) for (const overlap of [false, true]) {
        const result = Swap.planSwap(f.snapshot, f.registry,
            opts({ keepSimilarWindowsTogether: grouped, allowTransientOverlap: overlap }));
        if (grouped) {
            assert.equal(result.plan, null);
            assert.equal(result.attempts[0].reason, "no_grouped_cycle");
            assert.equal(Cycles.proposeGroupedCycles(f.snapshot.windows, f.snapshot.outputs[0].usableRect, opts()).stats.groups, 1);
        } else if (overlap) {
            validSwap(f, result);
            assert.ok(["similar-cycle","free-swap"].includes(result.plan.metadata.strategy));
        } else {
            assert.equal(result.plan, null);
            assert.ok(result.attempts.some(a => a.reason === "no-safe-step-order"));
        }
        cases++;
    }
}

// Built-in and provider thirds use their own rounded edges, not assumed equal
// widths. Force both directions with a reproducible alternative ordering.
for (const provider of [false, true]) for (const width of [1201, 1202]) for (const direction of [1, -1]) {
    const area = rect(-1201, -100, width, 1000);
    const library = { sourceId: "kde:window-layouts", status: "available", executionReady: true,
        revision: "thirds-1", padding: 0,
        layouts: [0, 1, 2].map(i => ({ id: "zone" + i, name: "Third", x: i / 3, y: 0,
            width: 1 / 3, height: 1, groupId: "thirds" })),
        groups: [{ id: "thirds", name: "Thirds", layoutIds: ["zone0", "zone1", "zone2"] }] };
    const group = provider ? Groups.groupsFromLibrary(library)[0] : Groups.builtinGroups(0)[4];
    const frames = Groups.groupFrames(group, area);
    // Fractional client frames inside the provider-rounded logical zones.
    const contained = frames.map(r => rect(r.x + .25, r.y + .25, r.width - .5, r.height - .5));
    const f = prepared(area, contained, 1.5);
    const result = GroupExecution.planGroupMovement(f.snapshot, f.registry,
        opts({ group, library: provider ? library : undefined, rotateGroup: true,
            random: () => direction === 1 ? .999 : 0 }));
    assert.ok(result.plan, JSON.stringify(result));
    assert.equal(result.plan.moves.length, 3);
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot, f.registry, result.plan, group, provider ? library : undefined).valid, true);
    assert.equal(Steps.validateSwapSteps(f.snapshot, f.registry, result.plan, result.steps,
        { group, library: provider ? library : undefined }).valid, true);
    for (const move of result.plan.moves) {
        const from = contained.findIndex(r => r.x === move.fromRect.x);
        const to = frames.findIndex(r => Model.rectangleContains(r, move.toRect));
        assert.equal(to, (from + direction + 3) % 3);
        if (move.toRect.width !== move.fromRect.width || move.toRect.height !== move.fromRect.height) {
            assert.equal(move.permitsRoundingResize, true);
            assert.ok(move.fromRect.width - move.toRect.width <= 2);
            assert.ok(move.fromRect.height - move.toRect.height <= 2);
        }
    }
    // Group rotation currently opts into transient overlap. Revalidate the
    // same result under the strict policy: these full-height thirds have no
    // staging space, and the step planner must refuse rather than loosen it.
    const strict = Model.copyPlain(result.plan);
    strict.metadata.allowTransientOverlap = false;
    assert.equal(Steps.orderSwapSteps(f.snapshot, f.registry, strict,
        Object.assign(opts(), { group, library: provider ? library : undefined })).reason, "no-safe-step-order");
    cases++;
}
console.log("thirds swap regression: " + cases + " matrix cases passed");

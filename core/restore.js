.pragma library
.import "model.js" as Model
.import "registry.js" as Registry

// Session-local, one-shot inverse transaction data. This module only creates
// validated plans; an adapter remains responsible for warnings and writes.
function restoreCopy(value) { return Model.copyPlain(value); }
function validRevision(value) { return Model.validRevision(value); }
function sameRect(a, b) {
    return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}
function findWindow(snapshot, identity) {
    for (var i = 0; i < snapshot.windows.length; i++)
        if (snapshot.windows[i].identity === identity) return snapshot.windows[i];
    return null;
}
function rejected(reason) { return { record:null, plan:null, steps:[], reason:reason }; }

// Call only after the executor has verified the final committed layout. `steps`
// is the sequence that actually succeeded, in forward order.
function captureUndo(args) {
    args = args || {};
    var plan = args.plan, steps = args.steps, snapshot = args.postSnapshot;
    if (!Model.validatePlan(plan).valid || !Array.isArray(steps) || !steps.length || steps.length > 36 ||
            !Model.validateSnapshot(snapshot).valid || !validRevision(args.settingsRevision) ||
            args.sessionEpoch === undefined || args.sessionEpoch === null || args.sessionEpoch === "") return null;

    var moved = Object.create(null), expected = Object.create(null), original = Object.create(null);
    plan.moves.forEach(function(move) { moved[move.identity] = true; expected[move.identity] = move.toRect; original[move.identity] = move.fromRect; });
    var first = Object.create(null), last = Object.create(null);
    for (var i = 0; i < steps.length; i++) {
        var step = steps[i];
        if (!step || !moved[step.identity] || !Model.rectangle(step.fromRect) || !Model.rectangle(step.toRect) ||
                (step.kind !== "stage" && step.kind !== "place" && step.kind !== "resize" &&
                    step.kind !== "resize-partial" && step.kind !== "reverse") ||
                (last[step.identity] && !sameRect(last[step.identity], step.fromRect))) return null;
        if (!first[step.identity]) first[step.identity] = step.fromRect;
        last[step.identity] = step.toRect;
    }
    // Every committed movement must finish at the plan's declared destination.
    for (var id in moved) if (Object.prototype.hasOwnProperty.call(moved, id)) {
        if (!first[id] || !sameRect(first[id], original[id]) || !last[id] || !sameRect(last[id], expected[id])) return null;
        var live = findWindow(snapshot, id);
        if (!live || !sameRect(live.rect, expected[id])) return null;
    }
    if (args.preSnapshot) {
        if (!Model.validateSnapshot(args.preSnapshot).valid ||
                args.preSnapshot.topologyRevision !== snapshot.topologyRevision) return null;
        var beforeComparable = restoreCopy(snapshot);
        for (id in moved) if (Object.prototype.hasOwnProperty.call(moved, id))
            findWindow(beforeComparable, id).rect = restoreCopy(original[id]);
        if (Registry.snapshotSignature(beforeComparable) !== Registry.snapshotSignature(args.preSnapshot)) return null;
    }

    var inverseMoves = [];
    plan.moves.forEach(function(move) {
        var inverse={ identity:move.identity, fromRect:restoreCopy(move.toRect), toRect:restoreCopy(move.fromRect) };
        if (move.permitsRoundingResize===true) inverse.permitsRoundingResize=true;
        inverseMoves.push(inverse);
    });
    var inverseSteps = [];
    for (i = steps.length - 1; i >= 0; i--) {
        step = steps[i];
        inverseSteps.push({ identity:step.identity, fromRect:restoreCopy(step.toRect), toRect:restoreCopy(step.fromRect), kind:"reverse" });
    }
    var inverseMetadata = restoreCopy(plan.metadata || {});
    inverseMetadata.sourceMode = inverseMetadata.mode || null;
    inverseMetadata.mode = "restore";
    inverseMetadata.restore = true;
    inverseMetadata.complete = true;
    var inversePlan = Model.createPlan({ snapshotRevision:snapshot.topologyRevision,
        outputIds:restoreCopy(plan.outputIds), moves:inverseMoves, metadata:inverseMetadata });
    return { version:1, plan:inversePlan, steps:inverseSteps,
        postSnapshot:restoreCopy(snapshot), postSnapshotSignature:Registry.snapshotSignature(snapshot),
        settingsRevision:args.settingsRevision, sessionEpoch:args.sessionEpoch,
        libraryRevision:plan.metadata && plan.metadata.libraryRevision || null,
        identities:plan.moves.map(function(move) { return move.identity; }) };
}

// Always consumes the supplied record. Invoke this at the point Restore is
// requested, before queuing a warning; a later skip/cancel cannot revive it.
// Validation must be injected from the shared movement safety path.
function consumeRestore(record, args) {
    args = args || {};
    if (!record) return rejected("undo-unavailable");
    var snapshot = args.snapshot;
    if (typeof args.validateInverse !== "function") return rejected("inverse-validation-required");
    if (record.version !== 1 || !Model.validateSnapshot(snapshot).valid ||
            !Model.validateSnapshot(record.postSnapshot).valid || !Model.validatePlan(record.plan).valid ||
            !Array.isArray(record.steps) || !Array.isArray(record.identities) ||
            typeof record.postSnapshotSignature !== "string") return rejected("invalid-snapshot-or-record");
    if (args.settingsRevision !== record.settingsRevision) return rejected("settings-changed");
    if (args.sessionEpoch !== record.sessionEpoch) return rejected("session-changed");
    if (record.libraryRevision && (!args.currentLibrary || args.currentLibrary.status !== "available" ||
            args.currentLibrary.executionReady !== true || args.currentLibrary.revision !== record.libraryRevision ||
            args.libraryFresh !== true)) return rejected("library-changed");
    if (snapshot.topologyRevision !== record.postSnapshot.topologyRevision ||
            Registry.snapshotSignature(snapshot) !== record.postSnapshotSignature) return rejected("desktop-changed");
    for (var i = 0; i < record.identities.length; i++)
        if (!findWindow(snapshot, record.identities[i])) return rejected("identity-missing");
    var plan, steps, validation;
    try { plan = restoreCopy(record.plan); steps = restoreCopy(record.steps);
        validation = args.validateInverse(snapshot, plan, steps); }
    catch (error) { return rejected("inverse-validation-failed"); }
    if (validation !== true && (!validation || validation.valid !== true))
        return rejected("inverse-validation-failed");
    return { record:null, plan:plan, steps:steps, reason:null };
}

// Convert a verified single-window commit into the same inverse format used by
// ordered execution. Quantized readback is the actual committed destination.
function captureSingleUndo(args) {
    args=args || {};
    var tx=args.transaction, result=args.result;
    if (!tx || !Model.validatePlan(tx.plan).valid || !tx.plan.metadata || tx.plan.metadata.mode!=="ordinary_shift" ||
            tx.plan.moves.length!==1 || !result || result.status!=="committed" ||
            result.identity!==tx.plan.moves[0].identity || !Model.rectangle(result.frame)) return null;
    var plan=restoreCopy(tx.plan), move=plan.moves[0];
    move.toRect=restoreCopy(result.frame);
    plan.metadata.allowTransientOverlap=false;
    return captureUndo({plan:plan,steps:[{identity:move.identity,fromRect:restoreCopy(move.fromRect),
        toRect:restoreCopy(move.toRect),kind:"place"}],preSnapshot:tx.snapshot,postSnapshot:args.snapshot,
        settingsRevision:tx.settingsRevision,sessionEpoch:args.sessionEpoch});
}

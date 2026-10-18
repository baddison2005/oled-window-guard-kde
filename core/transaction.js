.pragma library
.import "model.js" as Model
.import "registry.js" as Registry
.import "planner.js" as Planner
.import "safety.js" as Safety

// Pure, explicit-event controller. Commands describe requested adapter work;
// this module never holds KWin objects or writes geometry. Every asynchronous
// event must echo both generation and commandId. Timeouts happen only when the
// adapter explicitly sends the corresponding deadline event.
var WARNING_MS_DEFAULT = 10000;
var SETTLEMENT_MS_DEFAULT = 700;
var SAFETY_MAX_AGE_MS_DEFAULT = 1000;
var MIN_CONFIRM_DELAY_MS = 16;

function createTransactionState() {
    return { phase: "Paused", generation: 0, nextCommand: 1, transaction: null,
        lastResult: null, pauseReason: "startup", pendingCommand: null };
}
function copy(v) { return Model.copyPlain(v); }
function command(state, type, fields) {
    var c = fields || {}; c.id = "cmd-" + state.nextCommand++; c.type = type;
    state.pendingCommand = { id: c.id, generation: state.generation, type: type };
    return c;
}
function bump(state) { state.generation++; state.pendingCommand = null; state.transaction = null; }
function result(state, commands) { return { state: state, commands: commands || [] }; }
function transition(inputState, event) {
    var s = copy(inputState || createTransactionState()), cmds = [];
    if (!event || typeof event.type !== "string") return result(s, cmds);
    if (event.type === "resume") {
        if (s.phase === "Paused" || s.phase === "Suspended") { bump(s); s.phase = "Waiting"; s.pauseReason = null; }
        return result(s, cmds);
    }
    if (event.type === "pause" || event.type === "invalidate" || event.type === "close" || event.type === "activity" || event.type === "session" || event.type === "topology" || event.type === "settingsChanged") {
        var old = s.transaction;
        bump(s); s.phase = event.type === "session" && event.suspended === true ? "Suspended" : "Paused";
        s.pauseReason = event.reason || event.type;
        if (old && old.warningRequested) cmds.push(command(s, "hideWarning", { transactionId: old.id, reason: s.pauseReason }));
        s.lastResult = { status: old && old.writeCommandId ? "cancelled-write-outcome-unknown" : "cancelled",
            reason: s.pauseReason, identity: old && old.move.identity,
            observedFrame: old && old.lastObservedFrame ? copy(old.lastObservedFrame) : null };
        return result(s, cmds);
    }
    if (!accepts(s, event)) return result(s, cmds);
    if (event.type === "begin") {
        if (s.phase !== "Waiting" || s.transaction || !validNow(event.now)) return result(s, cmds);
        var checked = validateBegin(event);
        if (!checked.valid) { s.phase = "Paused"; s.pauseReason = checked.reason; s.lastResult = { status: "skipped", reason: checked.reason }; return result(s, cmds); }
        var duration = event.warningMs === undefined ? WARNING_MS_DEFAULT : event.warningMs;
        if (!validDuration(duration, 3000, 60000) || event.warningAvailable !== true) {
            s.phase = "Paused"; s.pauseReason = event.warningAvailable === true ? "invalid-warning-duration" : "warning-unavailable";
            s.lastResult = { status: "skipped", reason: s.pauseReason }; return result(s, cmds);
        }
        s.generation++;
        s.transaction = { id: "txn-" + s.generation, plan: copy(event.plan), snapshot: copy(event.snapshot),
            registry: copy(event.registry), move: copy(event.plan.moves[0]), settingsRevision: event.settingsRevision,
            safetyTopologyGeneration: event.safety.topologyGeneration,
            topologyRevision: event.snapshot.topologyRevision, startedAt: event.now,
            warningDuration: duration, warningDeadline: null, warningVisible: false, warningRequested: true,
            observations: [], rollback: false };
        s.phase = "Warning";
        cmds.push(command(s, "showWarning", { transactionId: s.transaction.id, generation: s.generation,
            durationMs: duration, outputId: event.plan.outputIds[0], identity: s.transaction.move.identity }));
        return result(s, cmds);
    }
    var tx = s.transaction;
    if (!tx) return result(s, cmds);
    if (event.type === "warningShown" && s.phase === "Warning" && matches(s, event, "showWarning")) {
        if (event.shown !== true) return fail(s, "warning-show-failed", cmds);
        if (!validNow(event.now) || event.now < tx.startedAt || tx.warningVisible) return fail(s, "invalid-warning-acknowledgement", cmds);
        tx.warningVisible = true; tx.warningShownAt = event.now;
        tx.warningDeadline = event.now + tx.warningDuration;
        if (!validNow(tx.warningDeadline) || tx.warningDeadline <= event.now) return fail(s, "invalid-warning-deadline", cmds);
        return result(s, cmds);
    }
    if (event.type === "warningDeadline" && s.phase === "Warning" && matches(s, event, "showWarning")) {
        if (!validNow(event.now)) return result(s, cmds);
        if (!tx.warningVisible) return event.now >= tx.startedAt + tx.warningDuration ?
            fail(s, "warning-not-shown", cmds) : result(s, cmds);
        if (event.now < tx.warningDeadline) return result(s, cmds);
        s.pendingCommand = null;
        cmds.push(command(s, "hideWarning", { transactionId: tx.id, reason: "deadline" }));
        s.phase = "Revalidating"; return result(s, cmds);
    }
    if (event.type === "skip" && matches(s, event, "showWarning")) return cancel(s, "user-skip", cmds);
    if (event.type === "warningHidden" && s.phase === "Revalidating" && matches(s, event, "hideWarning")) {
        if (event.hidden !== true) return fail(s, "warning-hide-unconfirmed", cmds);
        tx.warningVisible = false; tx.warningRequested = false; s.pendingCommand = null;
        cmds.push(command(s, "requestSnapshot", { transactionId: tx.id, purpose: "pre-write" })); return result(s, cmds);
    }
    if (event.type === "snapshotReady" && s.phase === "Revalidating" && matches(s, event, "requestSnapshot")) {
        s.pendingCommand = null;
        var valid = validateCurrent(tx, event, event.now);
        if (!valid.valid) return fail(s, valid.reason, cmds);
        if (event.settlementMs !== undefined && !validDuration(event.settlementMs, 100, 2000))
            return fail(s, "invalid-settlement-duration", cmds);
        s.phase = "Applying"; tx.writeStartedAt = event.now;
        tx.writeDeadline = event.now + (event.settlementMs === undefined ? SETTLEMENT_MS_DEFAULT : event.settlementMs);
        if (!validNow(tx.writeDeadline) || tx.writeDeadline <= event.now) return fail(s, "invalid-settlement-deadline", cmds);
        tx.writeCommandId = "cmd-" + s.nextCommand++;
        s.pendingCommand = { id: tx.writeCommandId, generation: s.generation, type: "writeGeometry" };
        cmds.push({ id: tx.writeCommandId, type: "writeGeometry", generation: s.generation,
            transactionId: tx.id, identity: tx.move.identity, expectedFrom: copy(tx.move.fromRect), destination: copy(tx.move.toRect) });
        return result(s, cmds);
    }
    if (event.type === "snapshotReady" && s.phase === "RollingBack" && tx.rollbackPending && matches(s, event, "requestSnapshot")) {
        s.pendingCommand = null;
        if (!validNow(event.now) || !safetyOk(event.safety, event.now, event.safetyMaxAgeMs) || !safetyMatchesSnapshot(event.safety, event.snapshot) ||
                !safetyGenerationMatches(tx, event.safety) ||
                event.currentSettingsRevision !== tx.settingsRevision || !event.snapshot || !event.registry ||
                event.snapshot.topologyRevision !== tx.topologyRevision || !Model.validateSnapshot(event.snapshot).valid) {
            return finishFailure(s, "rollback-unsafe-or-stale", cmds);
        }
        var live = findWindow(event.snapshot, tx.move.identity), reverse = copy(tx.plan);
        // A verified readback may be quantized by the client. Reverse only the
        // exact last accepted frame; tolerance must never mask a later change.
        var reverseFrom = tx.lastObservedFrame || tx.move.toRect;
        if (!live || !sameRect(live.rect, reverseFrom)) return finishFailure(s, "external-frame-won", cmds);
        reverse.moves = [{ identity: tx.move.identity, fromRect: copy(live.rect), toRect: copy(tx.move.fromRect) }];
        if (!Planner.validateFinalPlan(event.snapshot, event.registry, reverse).valid)
            return finishFailure(s, "reverse-plan-unsafe", cmds);
        tx.rollbackPending = false; tx.rollbackPlan = reverse; tx.rollbackDeadline = event.now + SETTLEMENT_MS_DEFAULT;
        tx.rollbackCommandIssued = true;
        var reverseId = "cmd-" + s.nextCommand++;
        tx.rollbackCommandId = reverseId;
        s.pendingCommand = { id: reverseId, generation: s.generation, type: "writeGeometry" };
        cmds.push({ id: reverseId, type: "writeGeometry", generation: s.generation, transactionId: tx.id,
            identity: tx.move.identity, expectedFrom: copy(reverseFrom), destination: copy(tx.move.fromRect), rollback: true });
        return result(s, cmds);
    }
    if (event.type === "writeAck" && (s.phase === "Applying" || s.phase === "RollingBack") && matches(s, event, s.pendingCommand && s.pendingCommand.type)) {
        if (event.accepted !== true) return fail(s, "assignment-rejected", cmds);
        s.pendingCommand = null;
        if (s.phase === "RollingBack") { tx.rollbackAck = true; tx.rollbackObservations = []; s.phase = "RollingBack"; return result(s, cmds); }
        tx.assignmentAccepted = true; tx.observations = []; s.phase = "Settling"; return result(s, cmds);
    }
    if (event.type === "geometryObserved" && (s.phase === "Settling" || (s.phase === "RollingBack" && tx.rollbackAck))) {
        if (!event.observationId || !validNow(event.now) || !event.accepted || !event.geometry || !event.snapshot || !event.registry || event.currentSettingsRevision !== tx.settingsRevision ||
                !Model.validateSnapshot(event.snapshot).valid || !safetyOk(event.safety, event.now, event.safetyMaxAgeMs) || !safetyMatchesSnapshot(event.safety, event.snapshot) ||
                !safetyGenerationMatches(tx, event.safety))
            return fail(s, "unsafe-or-invalid-readback", cmds);
        if ((s.phase === "Settling" && (event.now < tx.writeStartedAt || event.now > tx.writeDeadline)) ||
                (s.phase === "RollingBack" && event.now > tx.rollbackDeadline))
            return fail(s, "readback-outside-settlement-window", cmds);
        if (s.phase === "RollingBack") return rollbackObservation(s, event, cmds);
        if (!Model.rectanglesClose(event.geometry, tx.move.toRect, Model.READBACK_TOLERANCE) || !sameRect(findWindow(event.snapshot, tx.move.identity) && findWindow(event.snapshot, tx.move.identity).rect, event.geometry) ||
                !postWriteSafe(tx, event.snapshot, event.registry, event.geometry)) return fail(s, "destination-not-accepted-or-unsafe", cmds);
        tx.lastObservedFrame = copy(event.geometry);
        if (tx.observations.indexOf(event.observationId) < 0 &&
                (tx.lastObservationAt === undefined || event.now - tx.lastObservationAt >= MIN_CONFIRM_DELAY_MS)) {
            tx.observations.push(event.observationId); tx.lastObservationAt = event.now;
        }
        if (tx.observations.length >= 2) {
            s.lastResult = { status: "committed", identity: tx.move.identity, frame: copy(event.geometry), generation: s.generation };
            s.transaction = null; s.phase = "Waiting"; s.pauseReason = null;
        }
        return result(s, cmds);
    }
    if (event.type === "settlementDeadline" && (s.phase === "Settling" || s.phase === "Applying" || s.phase === "RollingBack") && event.generation === s.generation) {
        if (s.phase === "RollingBack" && event.commandId === tx.rollbackCommandId && validNow(event.now) && event.now >= tx.rollbackDeadline)
            return finishFailure(s, "rollback-settlement-timeout", cmds);
        if (event.commandId === tx.writeCommandId && validNow(event.now) && event.now >= tx.writeDeadline) return fail(s, "settlement-timeout", cmds);
    }
    return result(s, cmds);
}

function validateBegin(e) {
    if (!e.plan || !e.snapshot || !e.registry || e.settingsRevision === undefined || e.currentSettingsRevision !== e.settingsRevision) return { valid: false, reason: "missing-or-stale-input" };
    if (Model.validateSnapshot(e.snapshot).valid !== true || !Array.isArray(e.plan.moves) || e.plan.moves.length !== 1) return { valid: false, reason: "plan-must-contain-one-move" };
    if (!Planner.validateFinalPlan(e.snapshot, e.registry, e.plan).valid) return { valid: false, reason: "plan-validation-failed" };
    if (e.plan.snapshotRevision !== e.snapshot.topologyRevision || e.registry.topologyRevision !== e.snapshot.topologyRevision) return { valid: false, reason: "stale-topology" };
    if (!safetyOk(e.safety, e.now, e.safetyMaxAgeMs) || !safetyMatchesSnapshot(e.safety, e.snapshot)) return { valid: false, reason: "unsafe-state" };
    return { valid: true };
}
function validateCurrent(tx, e, now) {
    if (!validNow(now) || !validRevision(e.currentSettingsRevision) || e.currentSettingsRevision !== tx.settingsRevision) return { valid: false, reason: "settings-changed" };
    if (now < tx.warningDeadline || now - tx.warningDeadline > 2000) return { valid: false, reason: "stale-warning-completion" };
    if (!e.snapshot || !e.registry || !Model.validateSnapshot(e.snapshot).valid) return { valid: false, reason: "invalid-fresh-snapshot" };
    if (e.snapshot.topologyRevision !== tx.topologyRevision || e.registry.topologyRevision !== tx.topologyRevision) return { valid: false, reason: "topology-changed" };
    if (Registry.snapshotSignature(e.snapshot) !== Registry.snapshotSignature(tx.snapshot)) return { valid: false, reason: "snapshot-changed" };
    if (!Planner.validateFinalPlan(e.snapshot, e.registry, tx.plan).valid) return { valid: false, reason: "plan-revalidation-failed" };
    if (!safetyOk(e.safety, now, e.safetyMaxAgeMs) || !safetyMatchesSnapshot(e.safety, e.snapshot) ||
            !safetyGenerationMatches(tx, e.safety)) return { valid: false, reason: "unsafe-state" };
    return { valid: true };
}
function safetyOk(safety, now, age) { return Safety.evaluate(safety, now, age === undefined ? SAFETY_MAX_AGE_MS_DEFAULT : age).safe; }
function safetyMatchesSnapshot(safety, snapshot) {
    if (!safety || !snapshot || !Array.isArray(snapshot.outputs) || !Array.isArray(safety.topologyOutputIds)) return false;
    var ids = snapshot.outputs.map(function(o) { return o.id; }).sort(), observed = safety.topologyOutputIds.slice().sort();
    return ids.length === observed.length && ids.every(function(id, i) { return id === observed[i]; });
}
function safetyGenerationMatches(tx, safety) {
    return !!safety && safety.topologyGeneration === tx.safetyTopologyGeneration;
}
function accepts(s, e) { return e.generation === s.generation; }
function matches(s, e, type) { return !!s.pendingCommand && e.commandId === s.pendingCommand.id && s.pendingCommand.generation === s.generation && s.pendingCommand.type === type; }
function validNow(v) { return typeof v === "number" && isFinite(v); }
function validRevision(v) { return (typeof v === "string" && v.length > 0) || validNow(v); }
function validDuration(v, min, max) { return validNow(v) && v >= min && v <= max; }
function cancel(s, reason, cmds) {
    var tx = s.transaction; bump(s); s.phase = "Paused"; s.pauseReason = reason;
    if (tx && tx.warningRequested) cmds.push(command(s, "hideWarning", { transactionId: tx.id, reason: reason }));
    s.lastResult = { status: tx && tx.writeCommandId ? "cancelled-write-outcome-unknown" : "cancelled",
        reason: reason, identity: tx && tx.move.identity,
        observedFrame: tx && tx.lastObservedFrame ? copy(tx.lastObservedFrame) : null }; return result(s, cmds);
}
function fail(s, reason, cmds) {
    var tx = s.transaction;
    if (tx && tx.assignmentAccepted && !tx.rollbackPending && !tx.rollbackCommandIssued) {
        s.generation++; tx.failureReason = reason; tx.rollbackPending = true;
        tx.warningVisible = false; tx.warningRequested = false; s.phase = "RollingBack"; s.pendingCommand = null;
        cmds.push(command(s, "requestSnapshot", { transactionId: tx.id, purpose: "rollback" }));
        return result(s, cmds);
    }
    return finishFailure(s, reason, cmds);
}
function finishFailure(s, reason, cmds) {
    var tx = s.transaction;
    bump(s); s.phase = "Paused"; s.pauseReason = reason;
    if (tx && tx.warningRequested) cmds.push(command(s, "hideWarning", { transactionId: tx.id, reason: reason }));
    s.lastResult = { status: "failed", reason: reason, identity: tx && tx.move.identity,
        observedFrame: tx && tx.lastObservedFrame ? copy(tx.lastObservedFrame) : null,
        writeOutcome: !tx || !tx.writeCommandId ? "not-issued" :
            (tx.lastObservedFrame ? "observed" : "unknown"),
        rollback: tx && tx.rollbackCommandIssued ? "failed-or-unverified" : "not-attempted" }; return result(s, cmds);
}
function rollbackObservation(s, e, cmds) {
    var tx = s.transaction, list = tx.rollbackObservations || (tx.rollbackObservations = []);
    var live = findWindow(e.snapshot, tx.move.identity);
    if (!Model.rectanglesClose(e.geometry, tx.move.fromRect, Model.READBACK_TOLERANCE) || !live || !sameRect(live.rect, e.geometry) ||
            !postRollbackSafe(tx, e.snapshot, e.geometry)) return fail(s, "rollback-not-confirmed-or-unsafe", cmds);
    if (list.indexOf(e.observationId) < 0 &&
            (tx.lastRollbackObservationAt === undefined || e.now - tx.lastRollbackObservationAt >= MIN_CONFIRM_DELAY_MS)) {
        list.push(e.observationId); tx.lastRollbackObservationAt = e.now;
    }
    if (list.length >= 2) {
        s.lastResult = { status: "failed-rolled-back", reason: tx.failureReason, identity: tx.move.identity, frame: copy(e.geometry) };
        s.transaction = null; s.pendingCommand = null; s.phase = "Paused"; s.pauseReason = tx.failureReason || "transaction-failed";
    }
    return result(s, cmds);
}
function findWindow(snapshot, identity) {
    for (var i = 0; i < snapshot.windows.length; i++) if (snapshot.windows[i].identity === identity) return snapshot.windows[i];
    return null;
}
function sameRect(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function postWriteSafe(tx, snapshot, registry, frame) {
    var checked = Model.validateSnapshot(snapshot); if (!checked.valid) return false;
    if (snapshot.topologyRevision !== tx.topologyRevision || registry.topologyRevision !== tx.topologyRevision) return false;
    var w = findWindow(snapshot, tx.move.identity), original = findWindow(tx.snapshot, tx.move.identity);
    if (!w || !original || !sameRect(original.rect, tx.move.fromRect) || !sameRect(w.rect, frame) ||
            w.visible !== true || w.onCurrentDesktop !== true || w.onCurrentActivity !== true || (w.focused === true && !tx.registry.windows.some(function(rw){return rw.identity===w.identity && rw.allowIdleFocused===true;})) ||
            w.movable !== true || w.maximized !== false || w.fullscreen !== false || w.tiled !== false) return false;
    var comparable = copy(snapshot), comparableWindow = findWindow(comparable, tx.move.identity);
    comparableWindow.rect = copy(tx.move.fromRect);
    if (Registry.snapshotSignature(comparable) !== Registry.snapshotSignature(tx.snapshot)) return false;
    var acceptedPlan = copy(tx.plan);
    acceptedPlan.moves[0].toRect = copy(frame);
    if (!Planner.validateFinalPlan(tx.snapshot, tx.registry, acceptedPlan).valid) return false;
    var output = null;
    snapshot.outputs.forEach(function(o) { if (o.id === tx.plan.outputIds[0]) output = o; });
    if (!output || !Model.rectangleContains(output.usableRect, frame)) return false;
    for (var i = 0; i < snapshot.windows.length; i++) {
        var other = snapshot.windows[i];
        if (other.identity === w.identity || other.visible === false || other.minimized === true || other.onCurrentDesktop === false || other.onCurrentActivity === false ||
                ["desktop", "dock", "panel", "guard", "lock", "notification", "osd"].indexOf(other.windowType) >= 0 || !Model.rectangle(other.rect)) continue;
        if (Model.movementCollision(tx.snapshot,tx.plan,tx.move.identity,frame,other.identity,other.rect)) return false;
    }
    return true;
}
function postRollbackSafe(tx, snapshot, frame) {
    if (snapshot.topologyRevision !== tx.topologyRevision || !Model.rectangle(frame)) return false;
    var output = null;
    snapshot.outputs.forEach(function(o) { if (o.id === tx.plan.outputIds[0]) output = o; });
    if (!output || !Model.rectangleContains(output.usableRect, frame)) return false;
    for (var i = 0; i < snapshot.windows.length; i++) {
        var other = snapshot.windows[i];
        if (other.identity === tx.move.identity || other.visible === false || other.minimized === true || other.onCurrentDesktop === false ||
                other.onCurrentActivity === false || ["desktop", "dock", "panel", "guard", "lock", "notification", "osd"].indexOf(other.windowType) >= 0) continue;
        if (Model.movementCollision(tx.snapshot,tx.plan,tx.move.identity,frame,other.identity,other.rect)) return false;
    }
    return true;
}

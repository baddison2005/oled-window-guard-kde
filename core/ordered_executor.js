.pragma library
.import "model.js" as Model
.import "registry.js" as Registry
.import "safety.js" as Safety
.import "swap_steps.js" as Steps
.import "restore.js" as Restore

// Event-driven ordered transaction. Commands are inert adapter intents; no
// KWin object, timer, warning surface or geometry writer lives here.
function orderedCopy(value) { return Model.copyPlain(value); }
function orderedSame(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function orderedWindow(snapshot, identity) {
    for (var i = 0; i < snapshot.windows.length; i++) if (snapshot.windows[i].identity === identity) return snapshot.windows[i];
    return null;
}
function createOrderedState() { return { phase:"Paused", generation:0, nextCommand:1, pending:null, tx:null,
    undoRecord:null, pendingRestore:null, lastResult:null }; }
function orderedCommand(state, type, fields) {
    var result = fields || {}; result.id = "ordered-" + state.nextCommand++; result.type = type;
    result.generation = state.generation;
    state.pending = { id:result.id, type:type, generation:state.generation };
    return result;
}
function orderedPending(state, event, type) {
    return !!state.pending && state.pending.type === type && state.pending.generation === state.generation &&
        event.generation === state.generation && event.commandId === state.pending.id;
}
function orderedSafe(tx, event) {
    if (!event || typeof event.now !== "number" || !isFinite(event.now) ||
            event.currentSettingsRevision !== tx.settingsRevision ||
            !event.safety || Safety.evaluate(event.safety, event.now, 1000).safe !== true ||
            event.safety.topologyGeneration !== tx.safetyTopologyGeneration ||
            !Array.isArray(event.safety.topologyOutputIds) ||
            event.safety.topologyOutputIds.length !== tx.initial.outputs.length) return false;
    if (!tx.initial.outputs.every(function(o) { return event.safety.topologyOutputIds.indexOf(o.id) >= 0; })) return false;
    if (tx.libraryRevision) {
        var library = event.currentLibrary;
        if (!library || library.status !== "available" || library.executionReady !== true ||
                library.revision !== tx.libraryRevision || event.libraryFresh !== true) return false;
    }
    return true;
}
function orderedSnapshotMatches(tx, snapshot, expected) {
    if (!Model.validateSnapshot(snapshot).valid || snapshot.topologyRevision !== tx.topologyRevision) return false;
    var comparable = orderedCopy(snapshot);
    for (var id in tx.original) if (Object.prototype.hasOwnProperty.call(tx.original, id)) {
        var live = orderedWindow(snapshot, id), clone = orderedWindow(comparable, id);
        if (!live || !clone || !orderedSame(live.rect, expected[id])) return false;
        clone.rect = orderedCopy(tx.original[id]);
    }
    return Registry.snapshotSignature(comparable) === tx.initialSignature;
}
function orderedCurrentStep(tx) { return tx.rollback ? tx.reverse[tx.index] : (tx.retryStep || tx.steps[tx.index]); }
function orderedPartialResize(tx, step, frame) {
    return !tx.rollback && !tx.retryStep && step.kind === "resize" && Model.rectangle(frame) &&
        frame.x === step.fromRect.x && frame.y === step.fromRect.y &&
        frame.width <= Math.max(step.fromRect.width,step.toRect.width) && frame.height <= Math.max(step.fromRect.height,step.toRect.height) &&
        frame.width >= Math.min(step.fromRect.width,step.toRect.width) && frame.height >= Math.min(step.fromRect.height,step.toRect.height) &&
        !orderedSame(frame, step.fromRect) && !orderedSame(frame, step.toRect);
}
function orderedFinish(state, status, reason) {
    var tx = state.tx, altered = [];
    if (tx) for (var id in tx.expected) if (Object.prototype.hasOwnProperty.call(tx.expected, id) && !orderedSame(tx.expected[id], tx.original[id]))
        altered.push({ identity:id, frame:orderedCopy(tx.expected[id]) });
    if (status === "committed" && tx && tx.plan.metadata.mode !== "restore") {
        state.undoRecord = Restore.captureUndo({ plan:tx.plan, steps:tx.committed,
            preSnapshot:tx.initial, postSnapshot:tx.finalSnapshot,
            settingsRevision:tx.settingsRevision, sessionEpoch:tx.sessionEpoch });
    } else if (tx && status !== "committed") state.undoRecord = null;
    var unverified = tx && tx.unverifiedFrame;
    var unverifiedChanged = !!(unverified && !orderedSame(unverified.frame,tx.original[unverified.identity]));
    var failureStep = tx && orderedCurrentStep(tx);
    var failureWindow = tx && unverified && orderedWindow(tx.initial,unverified.identity);
    state.lastResult = { status:status, reason:reason || null, alteredFrames:altered,
        committedWindows:altered.filter(function(w){return !tx.unverifiedFrame || tx.unverifiedFrame.identity!==w.identity;}).length,
        plannedWindows:tx?tx.plan.moves.length:0,failureBoundary:tx?tx.failureReason:null,
        unverifiedWindows:unverifiedChanged?1:0,
        geometryFailure:unverified && failureStep ? {appId:String(failureWindow && failureWindow.appId || "").slice(0,128),
            from:orderedCopy(tx.original[unverified.identity]),requested:orderedCopy(failureStep.toRect),
            observed:orderedCopy(unverified.frame)}:null,
        unverifiedFrames:tx && tx.unverifiedFrame ? [orderedCopy(tx.unverifiedFrame)] : [] };
    state.tx = null; state.pending = null; state.pendingRestore = null;
    state.phase = status === "committed" ? "Waiting" : "Paused";
    return { state:state, commands:[] };
}
function orderedFail(state, reason, uncertain) {
    var tx = state.tx;
    if (!tx) return orderedFinish(state, "failed", reason);
    tx.failureReason = reason;
    if (uncertain) {
        state.phase = "Reconciling";
        return { state:state, commands:[orderedCommand(state,"requestSnapshot",{ purpose:"reconcile" })] };
    }
    return orderedStartRollback(state);
}
function orderedStartRollback(state) {
    var tx = state.tx;
    tx.reverse = tx.committed.slice().reverse().map(function(step) { return {
        identity:step.identity, fromRect:orderedCopy(step.toRect), toRect:orderedCopy(step.fromRect), kind:"reverse"
    }; });
    tx.rollback = true; tx.index = 0;
    if (!tx.reverse.length) return orderedFinish(state,"failed",tx.failureReason);
    state.phase = "Revalidating";
    return { state:state, commands:[orderedCommand(state,"requestSnapshot",{ purpose:"rollback" })] };
}
function transitionOrdered(input, event) {
    var state = orderedCopy(input || createOrderedState()), tx = state.tx;
    if (!event || typeof event.type !== "string") return { state:state, commands:[] };
    if (event.type === "resume" && state.phase === "Paused") {
        state.generation++; state.phase = "Waiting"; return { state:state, commands:[] };
    }
    if (["pause","activity","session","topology","settingsChanged","close"].indexOf(event.type) >= 0) {
        state.generation++;
        state.pendingRestore = null;
        if (event.type === "session" || event.type === "close" || event.type === "topology") state.undoRecord = null;
        return orderedFinish(state, tx ? "cancelled-write-outcome-unknown" : "cancelled", event.type);
    }
    if (event.type === "queueRestore" && state.phase === "Waiting" && event.generation === state.generation) {
        var consumed = Restore.consumeRestore(state.undoRecord, { snapshot:event.snapshot,
            settingsRevision:event.settingsRevision, sessionEpoch:event.sessionEpoch,
            currentLibrary:event.currentLibrary, libraryFresh:event.libraryFresh,
            validateInverse:function(snapshot,plan,steps) {
                return Steps.validateSwapSteps(snapshot,event.registry,plan,steps);
            } });
        state.undoRecord = null;
        if (!consumed.plan || !event.safety || !Safety.evaluate(event.safety,event.now,1000).safe) {
            state.lastResult = { status:"restore-skipped", reason:consumed.reason || "unsafe-state", alteredFrames:[] };
            return { state:state, commands:[] };
        }
        state.pendingRestore = { plan:consumed.plan, steps:consumed.steps,
            snapshotSignature:Registry.snapshotSignature(event.snapshot) };
        return { state:state, commands:[{ type:"restoreQueued", generation:state.generation,
            plan:orderedCopy(consumed.plan), steps:orderedCopy(consumed.steps) }] };
    }
    if (event.type === "begin" && state.phase === "Waiting" && event.generation === state.generation) {
        var isRestore = event.plan && event.plan.metadata && event.plan.metadata.mode === "restore";
        if (isRestore && (!state.pendingRestore ||
                JSON.stringify(state.pendingRestore.plan) !== JSON.stringify(event.plan) ||
                JSON.stringify(state.pendingRestore.steps) !== JSON.stringify(event.steps) ||
                !event.snapshot || Registry.snapshotSignature(event.snapshot) !== state.pendingRestore.snapshotSignature))
            return orderedFinish(state,"failed","restore-not-queued-or-stale");
        if (event.warningCleared !== true || !event.snapshot || !event.registry || !event.plan ||
                !Steps.validateSwapSteps(event.snapshot,event.registry,event.plan,event.steps,
                    {group:event.group,library:event.library}).valid ||
                typeof event.settingsRevision !== "string" || !event.settingsRevision ||
                event.currentSettingsRevision !== event.settingsRevision ||
                (event.plan && event.plan.metadata && event.plan.metadata.libraryRevision &&
                    (!event.currentLibrary || event.currentLibrary.status !== "available" ||
                     event.currentLibrary.executionReady !== true ||
                     event.currentLibrary.revision !== event.plan.metadata.libraryRevision || event.libraryFresh !== true)) ||
                !event.safety || !Safety.evaluate(event.safety,event.now,1000).safe)
            return orderedFinish(state,"failed","invalid-begin");
        var original = {}, expected = {};
        event.plan.moves.forEach(function(move) { original[move.identity] = orderedCopy(move.fromRect); expected[move.identity] = orderedCopy(move.fromRect); });
        state.generation++;
        state.pendingRestore = null;
        state.tx = { initial:orderedCopy(event.snapshot), initialSignature:Registry.snapshotSignature(event.snapshot),
            topologyRevision:event.snapshot.topologyRevision, settingsRevision:event.settingsRevision,
            sessionEpoch:event.registry.sessionEpoch,
            safetyTopologyGeneration:event.safety.topologyGeneration, plan:orderedCopy(event.plan),
            steps:orderedCopy(event.steps), original:original, expected:expected, committed:[],
            group:event.group ? orderedCopy(event.group) : null,
            libraryRevision:event.plan.metadata.libraryRevision || null,
            index:0, rollback:false, reverse:[], retryStep:null, retryCount:0, failureReason:null };
        state.phase = "Revalidating";
        return { state:state, commands:[orderedCommand(state,"requestSnapshot",{ purpose:"step" })] };
    }
    tx = state.tx;
    if (!tx) return { state:state, commands:[] };
    if (event.type === "snapshotReady" && (state.phase === "Revalidating" || state.phase === "Reconciling") && orderedPending(state,event,"requestSnapshot")) {
        state.pending = null;
        if (state.phase === "Reconciling") {
            var failed = orderedCurrentStep(tx), live = event.snapshot && orderedWindow(event.snapshot,failed.identity);
            var accepted = orderedCopy(tx.expected); accepted[failed.identity] = failed.toRect;
            if (live && Model.rectangle(live.rect))
                tx.unverifiedFrame = { identity:failed.identity, frame:orderedCopy(live.rect) };
            if (!orderedSafe(tx,event) || !live ||
                    (!orderedSnapshotMatches(tx,event.snapshot,tx.expected) && !orderedSnapshotMatches(tx,event.snapshot,accepted)))
                return orderedFinish(state,"failed-uncertain","partial-or-external-geometry");
            if (orderedSame(live.rect,failed.toRect)) {
                tx.expected[failed.identity] = orderedCopy(failed.toRect);
                if (!tx.rollback) tx.committed.push(failed);
            }
            if (tx.rollback) return orderedFinish(state,"failed-uncertain","rollback-write-unconfirmed");
            tx.unverifiedFrame = null;
            return orderedStartRollback(state);
        }
        if (!orderedSafe(tx,event) || !orderedSnapshotMatches(tx,event.snapshot,tx.expected))
            return orderedFinish(state,"failed-uncertain",tx.failureReason || "stale-or-unsafe-snapshot");
        var step = orderedCurrentStep(tx);
        if (!step || !orderedSame(tx.expected[step.identity],step.fromRect)) return orderedFinish(state,"failed-uncertain","step-source-changed");
        tx.writeIssuedAt = event.now;
        tx.writeDeadline = event.now + 700;
        state.phase = "Applying";
        return { state:state, commands:[orderedCommand(state,"writeGeometry",{
            identity:step.identity, expectedFrom:orderedCopy(step.fromRect), destination:orderedCopy(step.toRect),
            rollback:tx.rollback, stepIndex:tx.index })] };
    }
    if (event.type === "writeResult" && state.phase === "Applying" && orderedPending(state,event,"writeGeometry")) {
        state.pending = null;
        var active = orderedCurrentStep(tx);
        if (event.accepted !== true || !Array.isArray(event.observations) || event.observations.length < 2)
            return orderedFail(state,event.reason==="client-size-constraint"?event.reason:"write-rejected-or-unconfirmed",true);
        var seen = Object.create(null), priorAt = null, acceptedFrame = null;
        for (var i = 0; i < event.observations.length; i++) {
            var observation = event.observations[i], expected = orderedCopy(tx.expected);
            var observedWindow = observation && observation.snapshot && orderedWindow(observation.snapshot,active.identity);
            var frame = observedWindow && observedWindow.rect;
            if (!acceptedFrame && frame) acceptedFrame = orderedCopy(frame);
            expected[active.identity] = acceptedFrame;
            var rejected = !observation || typeof observation.id !== "string" || !observation.id || seen[observation.id] ? "observation-id" :
                !orderedSafe(tx,observation) ? "safety" :
                !orderedSnapshotMatches(tx,observation.snapshot,expected) ? "desktop-changed" :
                !orderedSame(frame,acceptedFrame) ? "unstable-frame" :
                observation.now < tx.writeIssuedAt ? "before-write" :
                observation.now > tx.writeDeadline ? "deadline" :
                priorAt !== null && observation.now-priorAt < 16 ? "sample-spacing" : null;
            if (rejected)
                { if (frame && Model.rectangle(frame)) tx.unverifiedFrame = { identity:active.identity, frame:orderedCopy(frame) };
                  return orderedFail(state,"unsafe-or-invalid-readback/"+rejected,true); }
            seen[observation.id] = true; priorAt = observation.now;
        }
        if (!orderedSame(acceptedFrame,active.toRect)) {
            if (!orderedPartialResize(tx,active,acceptedFrame)) {
                if (acceptedFrame && Model.rectangle(acceptedFrame))
                    tx.unverifiedFrame = { identity:active.identity, frame:orderedCopy(acceptedFrame) };
                return orderedFail(state,"destination-not-accepted",true);
            }
            tx.expected[active.identity] = orderedCopy(acceptedFrame);
            tx.committed.push({ identity:active.identity, fromRect:orderedCopy(active.fromRect),
                toRect:orderedCopy(acceptedFrame), kind:"resize-partial" });
            tx.retryStep = { identity:active.identity, fromRect:orderedCopy(acceptedFrame),
                toRect:orderedCopy(active.toRect), kind:"resize" };
            tx.retryCount++;
            state.phase = "Revalidating";
            return { state:state, commands:[orderedCommand(state,"requestSnapshot",{ purpose:"partial-resize-retry" })] };
        }
        tx.expected[active.identity] = orderedCopy(active.toRect);
        tx.finalSnapshot = orderedCopy(event.observations[event.observations.length - 1].snapshot);
        if (tx.rollback) tx.index++;
        else { tx.committed.push(active); tx.retryStep = null; tx.index++; }
        if (tx.rollback && tx.index >= tx.reverse.length) {
            tx.unverifiedFrame = null;
            return orderedFinish(state,"failed-rolled-back",tx.failureReason);
        }
        if (!tx.rollback && tx.index >= tx.steps.length) return orderedFinish(state,"committed",null);
        state.phase = "Revalidating";
        return { state:state, commands:[orderedCommand(state,"requestSnapshot",{ purpose:tx.rollback ? "rollback" : "step" })] };
    }
    if (event.type === "stepTimeout" && state.phase === "Applying" && orderedPending(state,event,"writeGeometry") &&
            typeof event.now === "number" && isFinite(event.now) && event.now >= tx.writeDeadline)
        return orderedFail(state,"step-timeout",true);
    return { state:state, commands:[] };
}

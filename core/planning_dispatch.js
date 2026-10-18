.pragma library
.import "model.js" as Model

// Bounded request/reply gate. No timers, worker objects, compositor access or
// write intents. The host supplies fresh context and owns worker replacement.
function planningCopy(value) { return Model.copyPlain(value); }
function planningString(value) { return typeof value === "string" && value.length > 0; }
function validPlanningContext(context) {
    return context && planningString(context.sessionEpoch) && planningString(context.settingsRevision) &&
        planningString(context.snapshotSignature) && Model.validRevision(context.topologyRevision) &&
        (context.libraryRevision === null || planningString(context.libraryRevision));
}
function samePlanningContext(a,b) {
    return validPlanningContext(a) && validPlanningContext(b) && a.sessionEpoch === b.sessionEpoch &&
        a.settingsRevision === b.settingsRevision && a.snapshotSignature === b.snapshotSignature &&
        a.topologyRevision === b.topologyRevision && a.libraryRevision === b.libraryRevision;
}
function samePlanningToken(a,b) {
    return a && b && a.workerEpoch === b.workerEpoch && a.id === b.id && a.generation === b.generation &&
        samePlanningContext(a.context,b.context);
}
function createPlanningState() {
    return {phase:"Unavailable",generation:0,nextRequest:1,workerEpoch:null,retiredEpoch:null,
        pending:null,occupied:null,lastResult:null};
}
function validPlanningResult(result,context) {
    if (!result || !Array.isArray(result.steps) || result.steps.length>36) return false;
    if (!result.plan) return result.steps.length===0;
    var plan=result.plan,participants=Object.create(null),metadata=plan.metadata;
    if (!Model.validatePlan(plan).valid || !metadata || metadata.complete !== true || plan.moves.length>12 ||
        plan.snapshotRevision !== context.topologyRevision || plan.outputIds.length !== 1 ||
        (metadata.libraryRevision || null) !== context.libraryRevision ||
        !Number.isInteger(metadata.maximumWindows) || metadata.maximumWindows<1 || metadata.maximumWindows>12 ||
        plan.moves.length>metadata.maximumWindows) return false;
    plan.moves.forEach(function(move){participants[move.identity]=true});
    return result.steps.every(function(step){return step && participants[step.identity] &&
        Model.rectangle(step.fromRect) && Model.rectangle(step.toRect) &&
        ["stage","place","resize"].indexOf(step.kind)>=0;});
}
function planningInvalidate(state,reason,unavailable) {
    state.generation++; state.pending=null;
    state.lastResult={status:"discarded",reason:reason};
    // Cancellation does not enqueue more work behind an executing request.
    // Its transport slot remains occupied until a matching reply or timeout.
    if (unavailable) {
        state.retiredEpoch=state.workerEpoch;state.workerEpoch=null;state.occupied=null;
    }
    state.phase=unavailable?"Unavailable":state.occupied?"Draining":"Idle";
}
function transitionPlanning(input,event) {
    var state=planningCopy(input || createPlanningState()),commands=[];
    if (!event || typeof event.type !== "string" || state.phase === "Closed") return {state:state,commands:commands};
    if (event.type === "workerReady") {
        if (!planningString(event.workerEpoch) || event.workerEpoch === state.workerEpoch ||
            event.workerEpoch === state.retiredEpoch) return {state:state,commands:commands};
        planningInvalidate(state,"worker-replaced",true);
        state.workerEpoch=event.workerEpoch;state.phase="Idle";
    } else if (event.type === "close") {
        planningInvalidate(state,"closed",true);state.phase="Closed";
    } else if (event.type === "invalidate") {
        planningInvalidate(state,event.reason || "context-invalidated",false);
        if (!state.workerEpoch) state.phase="Unavailable";
    } else if (event.type === "workerFailed" && event.workerEpoch === state.workerEpoch) {
        planningInvalidate(state,"worker-failed",true);
    } else if (event.type === "timeout") {
        if (state.occupied && samePlanningToken(event.token,state.occupied.token) &&
            typeof event.now === "number" && isFinite(event.now) && event.now >= state.occupied.deadline)
            planningInvalidate(state,"worker-timeout",true);
    } else if (event.type === "request") {
        if (state.phase !== "Idle" || state.occupied || !validPlanningContext(event.context) ||
            typeof event.now !== "number" || !isFinite(event.now) ||
            typeof event.timeoutMs !== "number" || !isFinite(event.timeoutMs) || event.timeoutMs <= 0 || event.timeoutMs > 5000 ||
            !isFinite(event.now+event.timeoutMs)) return {state:state,commands:commands};
        if (!event.job || !event.job.snapshot || !Array.isArray(event.job.snapshot.windows) ||
            event.job.snapshot.windows.length>256 || !Array.isArray(event.job.snapshot.outputs) ||
            event.job.snapshot.outputs.length>16 || !event.job.registry ||
            !Array.isArray(event.job.registry.windows) || event.job.registry.windows.length>256 ||
            !Array.isArray(event.job.registry.outputs) || event.job.registry.outputs.length>16) {
            state.lastResult={status:"rejected",reason:"planning-input-limit"};
            return {state:state,commands:commands};
        }
        if (state.nextRequest >= 9007199254740991) {
            planningInvalidate(state,"request-id-exhausted",true);return {state:state,commands:commands};
        }
        var token={workerEpoch:state.workerEpoch,id:state.nextRequest++,generation:state.generation,
            context:planningCopy(event.context)};
        state.occupied={token:token,deadline:event.now+event.timeoutMs};
        state.pending=planningCopy(state.occupied);state.phase="Busy";
        // The job is not retained in reducer state. The host must immediately
        // hand this immutable reference to WorkerScript.sendMessage, whose
        // transport copies plain objects; avoid a second full snapshot clone
        // on the compositor thread. Tokens remain defensively copied.
        commands.push({type:"sendPlanning",token:planningCopy(token),job:event.job});
    } else if (event.type === "reply") {
        if (!state.occupied || !samePlanningToken(event.token,state.occupied.token))
            return {state:state,commands:commands};
        var pending=state.pending,deadline=state.occupied.deadline;
        state.occupied=null;state.pending=null;state.phase="Idle";
        if (!pending || event.token.generation !== state.generation ||
            !samePlanningContext(event.currentContext,event.token.context)) {
            state.lastResult={status:"discarded",reason:"stale-planning-result"};
        } else if (typeof event.now !== "number" || !isFinite(event.now) || event.now >= deadline) {
            planningInvalidate(state,"worker-timeout",true);
        } else if (event.ok !== true) {
            planningInvalidate(state,"worker-error",true);
        } else if (!validPlanningResult(event.result,event.token.context)) {
            planningInvalidate(state,"malformed-planning-result",true);
        } else {
            var ready;
            try { ready=planningCopy(event.result); }
            catch(error) { planningInvalidate(state,"malformed-planning-result",true); }
            if (ready) {
                state.lastResult={status:"ready",reason:null};
                commands.push({type:"planningReady",token:planningCopy(event.token),result:ready});
            }
        }
    }
    return {state:state,commands:commands};
}

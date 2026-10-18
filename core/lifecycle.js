.pragma library
.import "safety.js" as Safety

// Pure session gate shared by the scheduler and transaction controller. The
// adapter reports facts as events; this module performs no KWin or timer I/O.
function createLifecycleState() {
    return { phase:"Paused", generation:0, kwinEpoch:null, companionSessionEpoch:null,
        topologyGeneration:null, topologyOutputIds:[], activePlan:null, reason:"startup-paused" };
}

function lifecycleCopy(value) {
    if (value === null || typeof value !== "object") return value;
    if (Array.isArray(value)) return value.map(lifecycleCopy);
    var result = {};
    Object.keys(value).forEach(function(key) { result[key] = lifecycleCopy(value[key]); });
    return result;
}

function lifecycleSuspend(state, reason) {
    // Every boundary invalidates pending callbacks and plans, even if already
    // paused. A repeated signal therefore cannot revive an old generation.
    state.generation++;
    state.phase = "Suspended";
    state.activePlan = null;
    state.reason = reason;
    return { state:state, effects:[{ type:"cancelPending", generation:state.generation }] };
}

function lifecycleGate(event) {
    if (!event || !event.safety || !finite(event.now)) return { safe:false, reason:"unknown-safety" };
    if (!event.companion || event.companion.connected !== true ||
            typeof event.companion.sessionEpoch !== "string" || !event.companion.sessionEpoch ||
            event.safety.companionSessionEpoch !== event.companion.sessionEpoch)
        return { safe:false, reason:"companion-session-unavailable" };
    if (typeof event.kwinEpoch !== "string" || !event.kwinEpoch)
        return { safe:false, reason:"kwin-epoch-unknown" };
    var result = Safety.evaluateReadiness(event.safety, event.now,
        finite(event.maxSafetyAgeMs) ? event.maxSafetyAgeMs : 1000);
    return result.safe ? { safe:true, reason:"safe" } : { safe:false, reason:result.reason };
}

function transitionLifecycle(input, event) {
    var state = lifecycleCopy(input || createLifecycleState());
    if (!event || typeof event.type !== "string") return { state:state, effects:[] };

    if (event.type === "resume") {
        var gate = lifecycleGate(event);
        if (state.phase === "Running") {
            if (!gate.safe) return lifecycleSuspend(state, gate.reason);
            if (event.kwinEpoch !== state.kwinEpoch || event.companion.sessionEpoch !== state.companionSessionEpoch)
                return lifecycleSuspend(state, "session-epoch-changed");
            if (event.safety.topologyGeneration !== state.topologyGeneration ||
                    !sameStrings(event.safety.topologyOutputIds, state.topologyOutputIds))
                return lifecycleSuspend(state, "topology-changed");
            return { state:state, effects:[] };
        }
        if (!gate.safe) {
            state.phase = "Paused";
            state.activePlan = null;
            state.reason = gate.reason;
            return { state:state, effects:[] };
        }
        // A changed epoch can only be adopted by a fresh explicit Resume.
        state.generation++;
        state.kwinEpoch = event.kwinEpoch;
        state.companionSessionEpoch = event.companion.sessionEpoch;
        state.topologyGeneration = event.safety.topologyGeneration;
        state.topologyOutputIds = event.safety.topologyOutputIds.slice();
        state.phase = "Running";
        state.activePlan = null;
        state.reason = null;
        return { state:state, effects:[{ type:"resetIntervals", generation:state.generation }] };
    }

    if (event.type === "pause") return lifecycleSuspend(state, "user-paused");
    if (event.type === "locked") return lifecycleSuspend(state, "session-locked");
    if (event.type === "companionLost") return lifecycleSuspend(state, "companion-lost");
    if (event.type === "kwinEpochChanged") {
        var changed = event.kwinEpoch !== state.kwinEpoch;
        if (changed) {
            var suspended = lifecycleSuspend(state, "kwin-epoch-changed");
            suspended.state.kwinEpoch = null;
            suspended.state.companionSessionEpoch = null;
            return suspended;
        }
        return { state:state, effects:[] };
    }
    if (["topologyChanged", "settingsChanged", "wake"].indexOf(event.type) >= 0) {
        return lifecycleSuspend(state, event.type);
    }
    if (event.type === "safetyUpdate") {
        if (state.phase !== "Running") return { state:state, effects:[] };
        var current = lifecycleGate(event);
        if (!current.safe) return lifecycleSuspend(state, current.reason);
        if (event.kwinEpoch !== state.kwinEpoch || event.companion.sessionEpoch !== state.companionSessionEpoch)
            return lifecycleSuspend(state, "session-epoch-changed");
        if (event.safety.topologyGeneration !== state.topologyGeneration ||
                !sameStrings(event.safety.topologyOutputIds, state.topologyOutputIds)) {
            return lifecycleSuspend(state, "topology-changed");
        }
        return { state:state, effects:[] };
    }
    return { state:state, effects:[] };
}

function finite(value) { return typeof value === "number" && isFinite(value); }
function sameStrings(a,b) {
    if (!Array.isArray(a) || !Array.isArray(b) || a.length !== b.length) return false;
    for (var i=0;i<a.length;i++) if (a[i] !== b[i]) return false;
    return true;
}

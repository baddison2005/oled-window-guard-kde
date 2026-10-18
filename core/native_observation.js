.pragma library

// Diagnostic consumer only. Polling cannot authorize geometry writes.
function createObservationState() {
    return {available:false, sample:null, observedAt:null, revision:0, reason:"native-unavailable"};
}
function counter(value) { return Number.isSafeInteger(value) && value >= 0; }
function validObservation(sample) {
    if (!sample || sample.schema !== 2 || typeof sample.epoch !== "string" ||
            !sample.epoch || sample.epoch.length > 128 || sample.outputIdentityComplete !== true ||
            !Array.isArray(sample.outputs) || !sample.outputs.length || sample.outputs.length > 16 ||
            !["unknown","locking","locked","unlocked"].includes(sample.lockState) ||
            !["unknown","sleeping","awake"].includes(sample.sleepState) ||
            !["unknown","active","inactive"].includes(sample.sessionActivity) ||
            typeof sample.buttonKnown !== "boolean" || typeof sample.buttonsDown !== "boolean" ||
            typeof sample.focusKnown !== "boolean" ||
            !Number.isSafeInteger(sample.ageMs) || sample.ageMs < -1 ||
            (!sample.buttonKnown && sample.buttonsDown)) return false;
    if (sample.graceProtocol!==undefined && (sample.graceProtocol!==1 || !counter(sample.monotonicMs) ||
            sample.outputs.some(function(o){return !Number.isSafeInteger(o.lastInteractionMs) || o.lastInteractionMs< -1 || o.lastInteractionMs>sample.monotonicMs || typeof o.keysDown!=="boolean";}))) return false;
    const keys=["sequence","topologyGeneration","sessionGeneration","focusGeneration",
        "pointer","button","axis","keyboard","unknownFocusKeyboard"];
    if (keys.some(function(key) { return !counter(sample[key]); }) ||
            sample.unknownFocusKeyboard > sample.keyboard ||
            sample.sequence !== sample.pointer + sample.button + sample.axis + sample.keyboard) return false;
    const ids=Object.create(null);
    return sample.outputs.every(function(output) {
        if (!output || typeof output.id !== "string" || !output.id || output.id.length > 64 ||
                ids[output.id] || !counter(output.events) || output.events > sample.sequence) return false;
        if (sample.interactionProtocol !== undefined && (sample.interactionProtocol!==1 ||
                !counter(output.interactionEvents) || output.interactionEvents>sample.sequence ||
                !counter(output.focusEvents) || output.focusEvents>sample.focusGeneration ||
                !counter(output.interactionAgeMs) || typeof output.buttonsDown!=="boolean")) return false;
        ids[output.id]=true;return true;
    });
}
function unavailable(state, reason) {
    const changed=state.available;
    return {state:{available:false,sample:state.sample,observedAt:null,
        revision:state.revision+(changed?1:0),reason:reason},cancel:changed?reason:null};
}
function consumeObservation(state, sample, now) {
    state=state || createObservationState();
    if (!Number.isFinite(now) || !validObservation(sample)) return unavailable(state,"native-invalid");
    const old=state.sample;
    let reason=null;
    if (old && old.epoch === sample.epoch) {
        if (old.graceProtocol===1 && (sample.graceProtocol!==1 || sample.monotonicMs<old.monotonicMs)) return unavailable(state,"native-clock-regressed");
        const keys=["sequence","topologyGeneration","sessionGeneration","focusGeneration",
            "pointer","button","axis","keyboard","unknownFocusKeyboard"];
        if (keys.some(function(key) { return sample[key] < old[key]; })) return unavailable(state,"native-regressed");
        const sameBoundary=old.topologyGeneration===sample.topologyGeneration && old.sessionGeneration===sample.sessionGeneration;
        const ids=function(s) { return s.outputs.map(function(o){return o.id}).sort().join("\n"); };
        if ((sameBoundary && (ids(old)!==ids(sample) || old.lockState!==sample.lockState ||
                old.sleepState!==sample.sleepState || old.sessionActivity!==sample.sessionActivity)) ||
                (sameBoundary && sample.outputs.some(function(o) {
                    const prior=old.outputs.filter(function(p){return p.id===o.id})[0];
                    return prior && (o.events < prior.events || (old.interactionProtocol===1 &&
                        (sample.interactionProtocol!==1 || o.interactionEvents<prior.interactionEvents || o.focusEvents<prior.focusEvents)));
                }))) return unavailable(state,"native-incoherent");
        if (!sameBoundary) reason="native-session-or-topology";
        else if (old.focusGeneration!==sample.focusGeneration || old.focusKnown!==sample.focusKnown) reason="native-focus";
        else if (old.sequence!==sample.sequence || old.buttonKnown!==sample.buttonKnown || old.buttonsDown!==sample.buttonsDown)
            reason="native-input";
    } else reason="native-replaced";
    if (!state.available) reason="native-recovered";
    return {state:{available:true,sample:JSON.parse(JSON.stringify(sample)),observedAt:now,
        revision:state.revision+(reason?1:0),reason:"native-diagnostic"},cancel:reason};
}
function expireObservation(state, now, maxAgeMs) {
    if (!state.available) return {state:state,cancel:null};
    if (!Number.isFinite(now) || !Number.isFinite(maxAgeMs) || maxAgeMs < 0 ||
            now < state.observedAt || now-state.observedAt >= maxAgeMs)
        return unavailable(state,"native-stale");
    return {state:state,cancel:null};
}

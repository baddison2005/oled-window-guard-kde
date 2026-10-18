.pragma library
.import "native_observation.js" as Native

// Read-only previews have no interaction scope. Write tokens cover precisely
// their target connectors plus global session/topology/coverage boundaries.
function valid(sample) {
    return Native.validObservation(sample) && sample.interactionProtocol===1 && sample.immediateCancellation===true &&
        typeof sample.otherInputObserved==="boolean" && sample.outputs.every(function(o){return Native.counter(o.interactionEvents) &&
            Native.counter(o.focusEvents) && Native.counter(o.interactionAgeMs) && typeof o.buttonsDown==="boolean";});
}
function token(sample,connectors,options) {
    options=options || {};
    if (!valid(sample) || !Array.isArray(connectors) || connectors.length>16 ||
            new Set(connectors).size!==connectors.length) return null;
    if (connectors.length && sample.otherInputObserved) return null;
    const rows=connectors.map(function(id){return sample.outputs.filter(function(o){return o.id===id})[0];});
    if (rows.some(function(o){return !o;})) return null;
    const grace=options.graceUntilMs || 0;
    if (grace && (sample.graceProtocol!==1 || !Native.counter(grace) || !Native.counter(sample.monotonicMs) ||
            rows.some(function(o){return !Number.isSafeInteger(o.lastInteractionMs) || o.lastInteractionMs < -1 || o.lastInteractionMs>sample.monotonicMs;}))) return null;
    return {ignoreFocus:options.ignoreFocus===true,graceUntilMs:grace,epoch:sample.epoch,otherInput:sample.otherInputObserved,topology:sample.topologyGeneration,session:sample.sessionGeneration,
        lock:sample.lockState,sleep:sample.sleepState,activity:sample.sessionActivity,
        connectors:sample.outputs.map(function(o){return o.id;}).sort(),
        outputs:rows.map(function(o){return {id:o.id,interaction:o.interactionEvents,focus:options.ignoreFocus===true?null:o.focusEvents};})};
}
function current(expected,sample) {
    if (!expected || !valid(sample)) return false;
    const fresh=token(sample,expected.outputs.map(function(o){return o.id;}),expected);
    if (!fresh) return false;
    for (let i=0;i<expected.outputs.length;i++) {
        const before=expected.outputs[i], after=fresh.outputs[i];
        if (before.interaction===after.interaction) continue;
        const row=sample.outputs.filter(function(o){return o.id===before.id;})[0];
        if (expected.graceUntilMs && row.lastInteractionMs>=0 && row.lastInteractionMs<expected.graceUntilMs && after.interaction>before.interaction)
            after.interaction=before.interaction;
    }
    return JSON.stringify(expected)===JSON.stringify(fresh);
}
function shouldCancel(old,fresh,connectors,reason,options) {
    if (reason!=="native-input" && reason!=="native-focus") return true;
    return !current(token(old,connectors,options),fresh);
}

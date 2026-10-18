.pragma library
.import "native_observation.js" as Native
.import "model.js" as Model

// Pure normalization. A diagnostic poll can never acquire write authority.
function normalize(sample, snapshot, now, options) {
    function no(reason) { return {safe:false, reason:reason, snapshot:null}; }
    if (!options || options.immediateCancellation!==true || !Native.validObservation(sample) ||
            sample.immediateCancellation!==true || sample.otherInputObserved!==false ||
            !Native.counter(sample.cancellationGeneration)) return no("native-write-coverage-unavailable");
    if (!Number.isFinite(now) || !Model.validateSnapshot(snapshot).valid ||
            !Number.isFinite(options.quietMs) || options.quietMs<0 || options.quietMs>60000 ||
            options.companionConnected!==true || options.topologyStable!==true) return no("invalid-write-context");
    if (sample.lockState!=="unlocked" || sample.sleepState!=="awake" || sample.sessionActivity!=="active")
        return no("session-not-authoritatively-ready/"+sample.lockState+"/"+sample.sleepState+"/"+sample.sessionActivity);
    const scoped=options.interactionOnly===true;
    if (scoped && (sample.interactionProtocol!==1 || sample.outputs.some(function(o){return !Native.counter(o.interactionEvents) ||
            !Native.counter(o.focusEvents) || !Native.counter(o.interactionAgeMs) || typeof o.buttonsDown!=="boolean";}))) return no("interaction-coverage-unavailable");
    if (!sample.buttonKnown || (!scoped && sample.buttonsDown) || (!sample.focusKnown && options.allowUnfocusedDesktop!==true)) return no("input-state-unknown-or-held");
    const connectors=Object.create(null), native=Object.create(null);
    for (const output of sample.outputs) {
        if ((!scoped && (!Number.isSafeInteger(output.ageMs) || output.ageMs<0)) || native[output.id]) return no("output-activity-unknown");
        native[output.id]=output;
    }
    if (snapshot.outputs.length!==sample.outputs.length) return no("output-set-mismatch");
    const outputs=[];
    for (const output of snapshot.outputs) {
        if (!output.connector || connectors[output.connector] || !native[output.connector]) return no("output-set-mismatch");
        connectors[output.connector]=true;
        if (scoped && Array.isArray(options.outputIds) && options.outputIds.length && options.outputIds.indexOf(output.id)<0) continue;
        const row=native[output.connector],age=scoped?row.interactionAgeMs:row.ageMs;
        const held=scoped && (row.buttonsDown || row.keysDown===true) && options.ignoreKnownHolds!==true;
        outputs.push({id:output.id,known:true,quiet:age>=options.quietMs && !held,quietSince:now-age,buttonsDown:held});
    }
    if (!outputs.length || (scoped && Array.isArray(options.outputIds) && options.outputIds.length && outputs.length!==options.outputIds.length)) return no("output-set-mismatch");
    const quiet=outputs.every(function(o){return o.quiet;});
    const safety={observedAt:now,companion:"connected",locked:false,suspended:false,
        topologyStable:true,topologyGeneration:sample.topologyGeneration,
        topologyOutputIds:outputs.map(function(o){return o.id;}),outputs:outputs,
        allOutputsQuiet:quiet,heldButtons:outputs.some(function(o){return o.buttonsDown;}),capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},
        quietSince:Math.max.apply(null,outputs.map(function(o){return o.quietSince;})),quietMs:options.quietMs};
    return {safe:quiet,reason:quiet?"safe":"quiet-period-incomplete",snapshot:safety};
}

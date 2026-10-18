.pragma library

// Session-local intent. Persisted settings never arm either guard at startup.
function createGuardingState() {return {movement:false,brightness:false,unlock:null,movementOutputs:[],brightnessOutputs:[]};}
function copy(s) {return JSON.parse(JSON.stringify(s || createGuardingState()));}
function start(s,scope,movementReady,brightnessReady,movementOutputs,brightnessOutputs) {
    var next=copy(s);
    if(scope==="all")next.unlock=null;
    else if(next.unlock) {
        next.unlock[scope]=false;
        if(!next.unlock.movement && !next.unlock.brightness)next.unlock=null;
    }
    if(scope!=="brightness" && movementReady) {next.movement=true;next.movementOutputs=copy(movementOutputs || []);}
    if(scope!=="movement" && brightnessReady) {next.brightness=true;next.brightnessOutputs=copy(brightnessOutputs || []);}
    return next;
}
function pause(s,scope) {
    var next=copy(s);
    if(scope!=="brightness") {next.movement=false;if(next.unlock)next.unlock.movement=false;}
    if(scope!=="movement") {next.brightness=false;if(next.unlock)next.unlock.brightness=false;}
    if(next.unlock && !next.unlock.movement && !next.unlock.brightness)next.unlock=null;
    return next;
}
function sameSession(ticket,sample) {
    return !!ticket && !!sample && typeof sample.epoch==="string" && ticket.epoch===sample.epoch;
}
function dormant(sample) {
    return !!sample && (["locking","locked"].indexOf(sample.lockState)>=0 ||
        sample.sleepState==="sleeping" || sample.sessionActivity==="inactive");
}
function mergeOutputs(before,after) {
    var result=copy(before || []);
    (after || []).forEach(function(o){if(!result.some(function(r){return r.id===o.id}))result.push(copy(o));});
    return result;
}
function boundary(s,reason,sample,optedIn) {
    var next=copy(s),ticket=next.unlock;
    var sessionReasons=["native-lock","native-session","native-sleep","native-wake","native-input-blocked","native-unlock-input"];
    var missingOutputs=sameSession(ticket,sample) && sample.otherInputObserved===false &&
        reason==="native-invalid" && sample.outputIdentityComplete===false && Array.isArray(sample.outputs) && sample.outputs.length===0;
    var recoveredOutputs=sameSession(ticket,sample) && sample.otherInputObserved===false &&
        reason==="native-recovered" && ticket.awaitingOutputs===true;
    var knownBoundary=sameSession(ticket,sample) && sample.otherInputObserved===false && reason==="native-session-or-topology";
    var allowed=sessionReasons.indexOf(reason)>=0 || missingOutputs || recoveredOutputs || knownBoundary;
    if(optedIn && allowed && dormant(sample) && sample &&
            typeof sample.epoch==="string" && (next.movement || next.brightness)) {
        if(!sameSession(ticket,sample))ticket={movement:false,brightness:false,movementOutputs:[],brightnessOutputs:[],epoch:sample.epoch};
        ["movement","brightness"].forEach(function(kind){
            if(next[kind]) {ticket[kind]=true;ticket[kind+"Outputs"]=mergeOutputs(ticket[kind+"Outputs"],next[kind+"Outputs"]);}
        });
    }
    if(missingOutputs)ticket.awaitingOutputs=true;
    if(recoveredOutputs)ticket.awaitingOutputs=false;
    next.movement=false;next.brightness=false;
    next.unlock=optedIn && allowed && sameSession(ticket,sample)?ticket:null;
    return next;
}
function afterUnlock(s,sample,movementReady,brightnessReady,optedIn,scopes) {
    var next=copy(s),ticket=next.unlock;
    if(!optedIn || !sameSession(ticket,sample)){next.unlock=null;return next;}
    if(sample.lockState!=="unlocked" || sample.sleepState!=="awake" || sample.sessionActivity!=="active")return next;
    ["movement","brightness"].forEach(function(kind){
        if(!ticket[kind] || !(kind==="movement"?movementReady:brightnessReady))return;
        var outputs=scopes?scopes[kind]:ticket[kind+"Outputs"] || [];
        next[kind]=true;next[kind+"Outputs"]=mergeOutputs(next[kind+"Outputs"],outputs);
        ticket[kind+"Outputs"]=(ticket[kind+"Outputs"] || []).filter(function(o){return !outputs.some(function(r){return r.id===o.id})});
        ticket[kind]=ticket[kind+"Outputs"].length>0;
    });
    next.unlock=ticket.movement || ticket.brightness?ticket:null;
    return next;
}

// A topology boundary still retires every movement token. Only already armed,
// still-connected output identities retain ongoing guarding; additions do not arm.
function outputKey(o) {
    return JSON.stringify([o.id,o.connector]);
}
function topology(s,outputs) {
    var next=copy(s),current={};
    outputs.forEach(function(o){current[o.id]=outputKey(o);});
    ["movement","brightness"].forEach(function(kind){
        var key=kind+"Outputs";
        next[key]=(next[key] || []).filter(function(o){return current[o.id]===outputKey(o);});
        next[kind]=next[kind] && next[key].length>0;
    });
    return next;
}

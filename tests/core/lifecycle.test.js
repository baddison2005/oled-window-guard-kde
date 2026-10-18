"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
function source(file) { return fs.readFileSync("core/" + file, "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/gm, ""); }
const Safety = (() => { const c = {}; vm.runInNewContext(source("safety.js") + "\nthis.api={evaluate,evaluateReadiness};", c); return c.api; })();
const Lifecycle = (() => { const c = { Safety }; vm.runInNewContext(source("lifecycle.js") + "\nthis.api={createLifecycleState,transitionLifecycle};", c); return c.api; })();
function safe(now, overrides = {}) {
    return Object.assign({ observedAt:now, companion:"connected", companionSessionEpoch:"companion-1",
        locked:false, suspended:false, topologyStable:true, topologyGeneration:1,
        topologyOutputIds:["DP-1"], allOutputsQuiet:true, heldButtons:false,
        outputs:[{id:"DP-1",known:true,quiet:true,buttonsDown:false}],
        capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true}, quietSince:now-5000, quietMs:1000 }, overrides);
}
function resume(now=10000, overrides={}) {
    return { type:"resume", now, kwinEpoch:"kwin-1", companion:{connected:true,sessionEpoch:"companion-1"},
        safety:safe(now), ...overrides };
}
function send(state, event) { return Lifecycle.transitionLifecycle(state,event); }

// Login/restart always begins paused. Missing, stale, unknown, or incomplete
// capabilities prevent explicit Resume from opening the gate.
{
    let state=Lifecycle.createLifecycleState();
    assert.equal(state.phase,"Paused");
    for (const event of [resume(10000,{safety:null}), resume(10000,{safety:safe(0)}),
        resume(10000,{safety:safe(10000,{locked:null})}),
        resume(10000,{safety:safe(10000,{capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:false}})}),
        resume(10000,{companion:{connected:true,sessionEpoch:"companion-2"}})]) {
        const result=send(state,event); state=result.state;
        assert.equal(state.phase,"Paused");
        assert.equal(result.effects.length,0);
    }
    const started=send(state,resume()); state=started.state;
    assert.equal(state.phase,"Running"); assert.equal(state.generation,1);
    assert.deepEqual(JSON.parse(JSON.stringify(started.effects)),[{type:"resetIntervals",generation:1}]);
}

// Lock, unknown safety, companion loss, and KWin reload invalidate the current
// generation and plan. Reconnection is inert until a fresh explicit Resume.
{
    for (const trigger of [
        {type:"locked"},
        {type:"safetyUpdate",now:11000,kwinEpoch:"kwin-1",companion:{connected:true,sessionEpoch:"companion-1"},safety:safe(11000,{locked:null})},
        {type:"companionLost"},
        {type:"kwinEpochChanged",kwinEpoch:"kwin-2"}
    ]) {
        let state=send(Lifecycle.createLifecycleState(),resume()).state;
        state.activePlan={id:"pending-plan"};
        const prior=state.generation;
        const suspended=send(state,trigger); state=suspended.state;
        assert.equal(state.phase,"Suspended"); assert.equal(state.generation,prior+1);
        assert.equal(state.activePlan,null);
        assert.equal(suspended.effects[0].type,"cancelPending");
        // A heartbeat/reconnect cannot resume automatically.
        const reconnect=send(state,{...resume(12000),type:"safetyUpdate"});
        assert.equal(reconnect.state.phase,"Suspended");
        const resumed=send(state,resume(12000,{kwinEpoch:trigger.type === "kwinEpochChanged" ? "kwin-2" : "kwin-1"}));
        assert.equal(resumed.state.phase,"Running");
        assert.equal(resumed.state.generation,state.generation+1);
    }
}

// Repeated Resume is idempotent while running; explicit Pause and subsequent
// Resume reset intervals and produce a new generation each time.
{
    let state=send(Lifecycle.createLifecycleState(),resume()).state;
    const repeated=send(state,resume(11000));
    assert.equal(repeated.state.generation,state.generation); assert.equal(repeated.effects.length,0);
    const unsafeRepeat=send(state,resume(11000,{safety:safe(11000,{locked:true})}));
    assert.equal(unsafeRepeat.state.phase,"Suspended");
    assert.equal(unsafeRepeat.state.generation,state.generation+1);
    state=send(state,{type:"pause"}).state;
    const restarted=send(state,resume(12000));
    assert.equal(restarted.state.generation,3);
    assert.equal(restarted.effects[0].type,"resetIntervals");
}

// A long event-driven session has bounded state: heartbeats never add timer or
// plan records, and an old callback generation cannot match after suspension.
{
    let state=send(Lifecycle.createLifecycleState(),resume()).state;
    const oldGeneration=state.generation;
    for(let i=1;i<=20000;i++) {
        const now=10000+i*1000;
        state=send(state,{type:"safetyUpdate",now,kwinEpoch:"kwin-1",
            companion:{connected:true,sessionEpoch:"companion-1"},safety:safe(now)}).state;
        assert.equal(state.generation,oldGeneration);
    }
    state=send(state,{type:"companionLost"}).state;
    assert.notEqual(state.generation,oldGeneration);
    assert.equal(state.activePlan,null);
}
// Configuration, wake, and output topology boundaries suspend, discard pending
// work, and require explicit Resume to establish fresh intervals.
{
    for (const event of [{type:"settingsChanged"},{type:"wake"},{type:"topologyChanged"}]) {
        let state=send(Lifecycle.createLifecycleState(),resume()).state;
        state.activePlan={id:"stale"};
        const result=send(state,event);
        assert.equal(result.state.phase,"Suspended");
        assert.equal(result.state.activePlan,null);
        assert.equal(result.state.generation,state.generation+1);
        assert.deepEqual(Array.from(result.effects,effect=>effect.type),["cancelPending"]);
        assert.equal(send(result.state,resume()).state.phase,"Running");
    }
    let state=send(Lifecycle.createLifecycleState(),resume()).state;
    const event={type:"safetyUpdate",now:11000,kwinEpoch:"kwin-1",
        companion:{connected:true,sessionEpoch:"companion-1"},
        safety:safe(11000,{topologyGeneration:2})};
    const changed=send(state,event);
    assert.equal(changed.state.generation,state.generation+1);
    assert.equal(changed.state.phase,"Suspended");
    assert.equal(changed.state.activePlan,null);
    assert.deepEqual(Array.from(changed.effects,effect=>effect.type),["cancelPending"]);
}
console.log("core lifecycle: capability and session transitions passed");

"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function load(file,imports,names) {
    const source=fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");
    const context=Object.assign({},imports);
    vm.runInNewContext(source+"\nthis.api={"+names.join(",")+"};",context);
    return context.api;
}
const Safety=load("safety.js",{},["evaluateReadiness","evaluateForOutput"]);
const Scheduler=load("scheduler.js",{},["createSchedulerState","transitionScheduler"]);
const Lifecycle=load("lifecycle.js",{Safety},["createLifecycleState","transitionLifecycle"]);
const Automatic=load("automatic_controller.js",{Scheduler,Lifecycle,Safety},["createAutomaticState","automaticTransition"]);
const copy=value=>JSON.parse(JSON.stringify(value));
const ids=["B","A"];
function safety(now,outputIds=ids) { return {observedAt:now,companion:"connected",companionSessionEpoch:"comp-1",
    locked:false,suspended:false,topologyStable:true,topologyGeneration:1,topologyOutputIds:outputIds.slice(),
    allOutputsQuiet:true,heldButtons:false,outputs:outputIds.map(id=>({id,known:true,quiet:true,buttonsDown:false,quietSince:now-5000})),
    capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},quietSince:now-5000,quietMs:1000}; }
function live(type,now,overrides={}) { return Object.assign({type,now,safety:safety(now),kwinEpoch:"kwin-1",
    companion:{connected:true,sessionEpoch:"comp-1"}},overrides); }
let state=Automatic.createAutomaticState();
function send(event) { const result=Automatic.automaticTransition(state,event);state=copy(result.state);return copy(result.commands); }
function deadline(command,now,overrides={}) { return send(Object.assign(live("warningDeadline",now),{
    outputId:command.outputId,schedulerGeneration:command.schedulerGeneration,
    lifecycleGeneration:command.lifecycleGeneration},overrides)); }
assert.equal(state.lifecycle.phase,"Paused");
send({type:"configure",now:0,outputIds:ids,intervalMs:1000});
assert.deepEqual(send(live("tick",2000)),[]);
send(live("resume",0));
assert.equal(state.lifecycle.phase,"Running");
assert.deepEqual(send(live("tick",999)),[]);
let commands=send(live("tick",1000));
assert.equal(commands.length,1);
assert.equal(commands[0].outputId,"A");
assert.deepEqual(send(live("tick",1000)),[]); // one warning/apply at a time
const a=copy(commands[0]);
send(Object.assign(live("skip",1050),{outputId:"A",schedulerGeneration:a.schedulerGeneration,
    lifecycleGeneration:a.lifecycleGeneration}));
assert.equal(state.scheduler.deadlines.A,2050);
commands=send(live("tick",1050));
assert.equal(commands[0].outputId,"B");
const b=copy(commands[0]);
assert.equal(deadline(b,1060)[0].type,"revalidateTransaction");
send(Object.assign(live("finished",1100),{outputId:"B",schedulerGeneration:b.schedulerGeneration,
    lifecycleGeneration:b.lifecycleGeneration,status:"committed"}));
assert.equal(state.scheduler.deadlines.B,2100);
commands=send(live("tick",9999));
assert.equal(commands.length,1); // delayed timer never creates a catch-up burst
assert.equal(commands[0].outputId,"A");
const late=copy(commands[0]);
commands=send({type:"locked"});
assert.equal(commands[0].type,"cancelTransaction");
assert.equal(state.lifecycle.phase,"Suspended");
assert.deepEqual(send(Object.assign(live("finished",10000),{outputId:"A",schedulerGeneration:late.schedulerGeneration,
    lifecycleGeneration:late.lifecycleGeneration,status:"committed"})),[]);
assert.equal(state.scheduler.inFlight,null);
send(live("resume",10001));
assert.equal(state.scheduler.deadlines.A,11001);
send(live("resume",10002));
assert.equal(state.scheduler.deadlines.A,11001); // repeated Resume preserves interval
const unknown=live("tick",11001);unknown.safety.capabilities.keyboard=null;
assert.deepEqual(send(unknown),[]);
assert.equal(state.lifecycle.phase,"Suspended");
assert.deepEqual(send(live("tick",20000)),[]);
send(live("resume",20000));
commands=send(live("tick",21000));
assert.equal(commands[0].type,"beginTransaction");
commands=send({type:"companionLost"});
assert.equal(commands[0].type,"cancelTransaction");
const changed=live("resume",22000,{companion:{connected:true,sessionEpoch:"comp-2"}});
assert.equal(send(changed).length,0); // stale safety session cannot resume
assert.equal(state.lifecycle.phase,"Paused");
send({type:"wake",now:23000,outputIds:ids,intervalMs:1000});
assert.equal(state.scheduler.phase,"Suspended");
assert.deepEqual(send(live("tick",24000)),[]);
send(live("resume",25000));
assert.equal(state.scheduler.deadlines.A,26000);
send({type:"topologyChanged",now:25500,outputIds:["A"],intervalMs:500});
assert.equal(state.lifecycle.phase,"Suspended");
assert.deepEqual(send(live("tick",26000,{safety:safety(26000,["A"])})),[]);
send(live("resume",27000,{safety:safety(27000,["A"])}));
assert.equal(state.scheduler.deadlines.A,27500);
assert.equal(Object.keys(state.scheduler.deadlines).length,1);
// Activity on B does not hold back A. B is skipped at its own deadline only.
send({type:"configure",now:27010,outputIds:ids,intervalMs:100});
send(live("resume",27020));
const onlyA=live("tick",27120);onlyA.safety.outputs[0].quiet=false;
onlyA.safety.allOutputsQuiet=false;
commands=send(onlyA);
assert.equal(commands[0].type,"beginTransaction");
assert.equal(commands[0].outputId,"A");
assert.equal(deadline(commands[0],27120,{safety:onlyA.safety})[0].type,"revalidateTransaction");
send(Object.assign(live("finished",27121),{outputId:"A",schedulerGeneration:commands[0].schedulerGeneration,
    lifecycleGeneration:commands[0].lifecycleGeneration,status:"committed",safety:onlyA.safety}));
commands=send(live("tick",27121,{safety:onlyA.safety}));
assert.equal(commands[0].type,"beginTransaction");
commands=deadline(commands[0],27121,{safety:onlyA.safety});
assert.equal(commands[0].type,"outputSkipped");
assert.equal(commands[0].outputId,"B");
assert.equal(state.scheduler.deadlines.B,27221);
// Once apply may have started, activity requires reconciliation before any
// other output starts; it is no longer a harmless pre-write Skip.
commands=send(live("tick",27231));
assert.equal(commands[0].type,"beginTransaction");
const duringApply=copy(commands[0]);
assert.equal(deadline(duringApply,27231)[0].type,"revalidateTransaction");
const activeFinish=live("finished",27232);activeFinish.safety.outputs.find(o=>o.id===duringApply.outputId).quiet=false;
activeFinish.safety.allOutputsQuiet=false;
send(Object.assign(activeFinish,{outputId:duringApply.outputId,
    schedulerGeneration:duringApply.schedulerGeneration,lifecycleGeneration:duringApply.lifecycleGeneration,
    status:"committed"}));
assert.equal(state.lifecycle.phase,"Suspended");
assert.equal(state.scheduler.phase,"Paused");
// A malformed configuration cannot reuse previous deadlines; a regressing
// monotonic clock suspends both reducers instead of creating a stale intent.
send({type:"settingsChanged",now:27240,outputIds:["A"],intervalMs:0});
assert.equal(Object.keys(state.scheduler.intervals).length,0);
send(live("resume",27300,{safety:safety(27300,["A"])}));
assert.notEqual(state.lifecycle.phase,"Running");
send({type:"configure",now:27400,outputIds:["A"],intervalMs:500});
send(live("resume",27500,{safety:safety(27500,["A"])}));
assert.equal(state.lifecycle.phase,"Running");
send(live("tick",27499,{safety:safety(27499,["A"])}));
assert.equal(state.lifecycle.phase,"Suspended");
{
    let long=Automatic.createAutomaticState();
    function step(event) { const result=Automatic.automaticTransition(long,event);long=copy(result.state);return copy(result.commands); }
    step({type:"configure",now:0,outputIds:["A","B"],intervalMs:10});
    step(live("resume",0));
    for (let i=1;i<=1000;i++) {
        const now=i*11,command=step(live("tick",now))[0];
        assert.equal(command.type,"beginTransaction");
        const checked=step(Object.assign(live("warningDeadline",now),{outputId:command.outputId,
            schedulerGeneration:command.schedulerGeneration,lifecycleGeneration:command.lifecycleGeneration}));
        assert.equal(checked[0].type,"revalidateTransaction");
        step(Object.assign(live("finished",now+1),{outputId:command.outputId,
            schedulerGeneration:command.schedulerGeneration,lifecycleGeneration:command.lifecycleGeneration,
            status:"committed"}));
        assert.equal(long.scheduler.inFlight,null);
        assert.equal(Object.keys(long.scheduler.deadlines).length,2);
    }
}
console.log("core automatic controller: tests passed");

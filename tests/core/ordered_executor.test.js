"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
function source(file){return fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");}
function load(file,imports,names){const c=Object.assign({},imports);vm.runInNewContext(source(file)+"\nthis.api={"+names.join(",")+"};",c);return c.api;}
const Model=load("model.js",{},["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain","rectangle","rectangleContains","rectangleOverlaps","createPlan","validateSnapshot","validatePlan","validRevision"]);
const Registry=load("registry.js",{Model},["createRegistryState","reconcile","snapshotSignature"]);
const Planner=load("planner.js",{Model,Registry},["rawObstacle","validateSwapFinalPlan","validateFinalPlan"]);
const Safety=load("safety.js",{},["evaluate"]);
const Steps=load("swap_steps.js",{Model,Planner},["orderSwapSteps","validateSwapSteps","stepObstacle"]);
const Restore=load("restore.js",{Model,Registry},["captureUndo","consumeRestore"]);
const Executor=load("ordered_executor.js",{Model,Registry,Safety,Steps,Restore},["createOrderedState","transitionOrdered","orderedSafe","orderedSnapshotMatches","orderedCurrentStep","orderedSame","orderedWindow"]);
const rect=(x,y,width,height)=>({x,y,width,height});
const copy=x=>JSON.parse(JSON.stringify(x));
const win=(identity,frame)=>({identity,appId:"app",outputIds:["DP-1"],rect:frame,visible:true,onCurrentDesktop:true,
    onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,
    minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]});
const safety=(now)=>({observedAt:now,companion:"connected",locked:false,suspended:false,topologyStable:true,
    topologyGeneration:1,topologyOutputIds:["DP-1"],allOutputsQuiet:true,heldButtons:false,
    outputs:[{id:"DP-1",known:true,quiet:true,buttonsDown:false}],capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},
    quietSince:now-5000,quietMs:1000});
function fixture(ordinary=false){
    const area=rect(0,0,300,200),snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"e",name:"d",geometry:area,usableRect:area,scale:1}],
        windows:[win("a",rect(0,0,100,100)),win("b",rect(100,0,100,100))],focusedIdentity:null,topologyRevision:"topo"};
    const registry=Registry.reconcile(snapshot,Registry.createRegistryState("ordered"),{sessionEpoch:"ordered",selectedOutputIds:["DP-1"]});
    const plan=Model.createPlan({snapshotRevision:"topo",outputIds:["DP-1"],moves:[
        {identity:"a",fromRect:rect(0,0,100,100),toRect:rect(100,0,100,100)},
        {identity:"b",fromRect:rect(100,0,100,100),toRect:rect(0,0,100,100)}],
        metadata:{mode:"swap",maximumWindows:2,complete:true,allowTransientOverlap:false}});
    if (ordinary) {
        plan.metadata.mode="ordinary_shift";plan.metadata.shiftRangePercent=100;
        plan.moves.forEach(m=>{m.toRect={...m.fromRect,y:100};});
    }
    const ordered=Steps.orderSwapSteps(snapshot,registry,plan,{now:()=>0,deadlineMs:1000});
    assert.ok(ordered.steps.length>=(ordinary?2:3));
    return {snapshot,registry,plan,steps:ordered.steps};
}
function run(failAt=-1,failMode="reject",ordinary=false){
    const f=fixture(ordinary),live=copy(f.snapshot);let state=Executor.createOrderedState(),now=10000,index=0;
    function send(event){const result=Executor.transitionOrdered(state,event);state=result.state;return result.commands;}
    send({type:"resume"});
    let commands=send({type:"begin",generation:state.generation,warningCleared:true,now,
        snapshot:f.snapshot,registry:f.registry,plan:f.plan,steps:f.steps,settingsRevision:"s1",currentSettingsRevision:"s1",safety:safety(now)});
    assert.equal(commands[0].type,"requestSnapshot");
    let failed=false,guard=0;
    while(commands.length&&guard++<30){
        const command=commands[0]; now+=50;
        if(command.type==="requestSnapshot"){
            commands=send({type:"snapshotReady",generation:state.generation,commandId:command.id,now,
                snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now)});
        }else if(command.type==="writeGeometry"){
            if(command.rollback&&failMode==="rollback-uncertain"){
                commands=send({type:"writeResult",generation:state.generation,
                    commandId:command.id,accepted:false,now,currentSettingsRevision:"s1"});
                failMode="rollback-uncertain-done";
                continue;
            }
            if(index===failAt&&!failed){failed=true;commands=send({type:"writeResult",generation:state.generation,
                commandId:command.id,accepted:false,now,currentSettingsRevision:"s1"});
                if(failMode==="landed") live.windows.find(w=>w.identity===command.identity).rect=copy(command.destination);
                if(failMode==="partial") live.windows.find(w=>w.identity===command.identity).rect.x+=1;
            }
            else{
                live.windows.find(w=>w.identity===command.identity).rect=copy(command.destination);
                const observations=[20,40].map((offset,k)=>({id:"obs-"+guard+"-"+k,now:now+offset,
                    snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now+offset)}));
                commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                    accepted:true,observations,now:now+40,currentSettingsRevision:"s1"});
            }
            if(!command.rollback)index++;
        }else throw Error("unexpected command " + command.type);
    }
    assert.ok(guard<30,"ordered execution must terminate");
    return {state,live,original:f.snapshot};
}
{
    const done=run();assert.equal(done.state.lastResult.status,"committed");
    assert.equal(done.live.windows[0].rect.x,100);assert.equal(done.live.windows[1].rect.x,0);
}
for(let failAt=0;failAt<fixture().steps.length;failAt++){
    const failed=run(failAt);assert.equal(failed.state.lastResult.status,failAt===0?"failed":"failed-rolled-back");
    assert.deepEqual(failed.live.windows.map(w=>w.rect),failed.original.windows.map(w=>w.rect));
    assert.equal(failed.state.lastResult.alteredFrames.length,0);
}
{
    const landed=run(1,"landed");
    assert.equal(landed.state.lastResult.status,"failed-rolled-back");
    assert.deepEqual(landed.live.windows.map(w=>w.rect),landed.original.windows.map(w=>w.rect));
    const partial=run(1,"partial");
    assert.equal(partial.state.lastResult.status,"failed-uncertain");
    assert.notDeepEqual(partial.live.windows.map(w=>w.rect),partial.original.windows.map(w=>w.rect));
    assert.equal(partial.state.lastResult.unverifiedWindows,1);
    const diagnostic=partial.state.lastResult.geometryFailure;
    assert.equal(diagnostic.appId,"app");
    assert.deepEqual(copy(diagnostic.observed),partial.live.windows.find(w=>w.rect.x===diagnostic.observed.x && w.rect.y===diagnostic.observed.y).rect);
    assert.notDeepEqual(diagnostic.from,diagnostic.observed);
    const rollback=run(1,"rollback-uncertain");
    assert.equal(rollback.state.lastResult.status,"failed-uncertain");
    assert.ok(rollback.state.lastResult.alteredFrames.length>0);
    assert.equal(rollback.state.lastResult.unverifiedFrames.length,1);
}
console.log("core ordered executor: step-failure fixtures passed");

// Delayed callbacks must remain inert across every suspension boundary, even
// after an explicit Resume starts a fresh transaction with a new command ID.
function boundaryDriver(phase) {
    const f=fixture();
    let live=copy(f.snapshot),state=Executor.createOrderedState(),pending=null,now=10000;
    function send(event) {
        const r=Executor.transitionOrdered(state,event);state=r.state;
        pending=r.commands[0]||null;return r.commands;
    }
    function begin() {
        send({type:"resume"});
        return send({type:"begin",generation:state.generation,warningCleared:true,now,
            snapshot:f.snapshot,registry:f.registry,plan:f.plan,steps:f.steps,
            settingsRevision:"s1",currentSettingsRevision:"s1",safety:safety(now)});
    }
    function snapshot(extras={}) {
        now+=50;
        return Object.assign({type:"snapshotReady",generation:state.generation,commandId:pending.id,
            now,snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now)},extras);
    }
    function result(accepted=true) {
        const command=pending;now+=50;
        if (accepted) live.windows.find(w=>w.identity===command.identity).rect=copy(command.destination);
        return {type:"writeResult",generation:state.generation,commandId:command.id,accepted,now:now+40,
            currentSettingsRevision:"s1",observations:accepted?[20,40].map((offset,i)=>({
                id:"boundary-"+now+"-"+i,now:now+offset,snapshot:copy(live),
                currentSettingsRevision:"s1",safety:safety(now+offset)})):[]};
    }
    begin();
    if (phase!=="revalidating") send(snapshot());
    if (phase==="after_commit" || phase.startsWith("rollback")) send(result());
    if (phase==="reconciling") send(result(false));
    if (phase.startsWith("rollback")) {
        send(snapshot());send(result(false));send(snapshot());
        if (phase==="rollback_applying") send(snapshot());
    }
    return {f,send,begin,snapshot,result,
        get state(){return state},get pending(){return pending},get live(){return live}};
}
let boundaryCases=0;
for (const phase of ["revalidating","applying","after_commit","reconciling","rollback_ready","rollback_applying"]) {
    for (const boundary of ["pause","activity","session","topology","settingsChanged","close"]) {
        const d=boundaryDriver(phase),oldGeneration=d.state.generation,oldCommand=copy(d.pending);
        assert.ok(oldCommand,phase);
        d.send({type:boundary});
        assert.ok(d.state.generation>oldGeneration);
        assert.equal(d.state.phase,"Paused");assert.equal(d.state.tx,null);assert.equal(d.state.pending,null);
        const paused=copy(d.state);
        for (const type of ["snapshotReady","writeResult","stepTimeout"]) {
            assert.equal(d.send({type,generation:oldGeneration,commandId:oldCommand.id,now:20000,
                snapshot:d.f.snapshot,currentSettingsRevision:"s1",safety:safety(20000),accepted:true,observations:[]}).length,0);
            assert.deepEqual(copy(d.state),paused);
        }
        d.begin();
        const fresh=copy(d.state);
        assert.notEqual(d.pending.id,oldCommand.id);
        for (const generation of [oldGeneration,d.state.generation]) {
            for (const type of ["snapshotReady","writeResult","stepTimeout"]) {
                assert.equal(d.send({type,generation,commandId:oldCommand.id,now:20000,
                    snapshot:d.f.snapshot,currentSettingsRevision:"s1",safety:safety(20000),accepted:true,observations:[]}).length,0);
                assert.deepEqual(copy(d.state),fresh);
            }
        }
        boundaryCases++;
    }
}

const unsafeBoundaryFacts=[
    ["lock",event=>{event.safety.locked=true}],
    ["sleep",event=>{event.safety.suspended=true}],
    ["companion loss",event=>{event.safety.companion="disconnected"}],
    ["held button",event=>{event.safety.heldButtons=true;event.safety.outputs[0].buttonsDown=true}],
    ["activity",event=>{event.safety.allOutputsQuiet=false;event.safety.outputs[0].quiet=false}],
    ["unknown input",event=>{event.safety.capabilities.keyboard=false}],
    ["stale input",event=>{event.safety.observedAt=event.now-1001}],
    ["topology generation",event=>{event.safety.topologyGeneration++}],
    ["settings revision",event=>{event.currentSettingsRevision="s2"}],
    ["external geometry",event=>{event.snapshot.windows[0].rect.x+=1}],
    ["focus change",event=>{event.snapshot.focusedIdentity="a";event.snapshot.windows[0].focused=true}],
    ["output scale",event=>{event.snapshot.outputs[0].scale=1.5}],
];
for (const phase of ["revalidating","rollback_ready"]) for (const [label,mutate] of unsafeBoundaryFacts) {
    const d=boundaryDriver(phase),event=d.snapshot();mutate(event);
    assert.equal(d.send(event).length,0,phase+": "+label);
    assert.equal(d.state.phase,"Paused");assert.equal(d.state.lastResult.status,"failed-uncertain");
    boundaryCases++;
}
for (const phase of ["applying","rollback_applying"]) for (const [label,mutate] of unsafeBoundaryFacts) {
    const d=boundaryDriver(phase),event=d.result();mutate(event.observations[1]);
    const commands=d.send(event);
    assert.equal(commands.length,1,phase+": "+label);
    assert.equal(commands[0].type,"requestSnapshot");assert.equal(commands[0].purpose,"reconcile");
    assert.equal(d.state.phase,"Reconciling");
    // The unsafe facts still apply at reconciliation: no inverse write is safe.
    const follow=d.snapshot();mutate(follow);
    assert.equal(d.send(follow).length,0,phase+": unsafe reconciliation "+label);
    assert.equal(d.state.phase,"Paused");assert.equal(d.state.lastResult.status,"failed-uncertain");
    assert.ok(d.state.lastResult.unverifiedFrames.length>0);
    boundaryCases++;
}
console.log("ordered transaction boundaries: "+boundaryCases+" matrix cases passed");

// The adapter must wait for separated samples; the executor continues to
// reject a forged pair that is only 15 ms apart and reconcile before rollback.
{
    const d=boundaryDriver("applying"),event=d.result();
    event.observations[1].now=event.observations[0].now+15;
    event.observations[1].safety=safety(event.observations[1].now);
    const commands=d.send(event);
    assert.equal(commands[0].purpose,"reconcile");
    assert.equal(d.state.tx.failureReason,"unsafe-or-invalid-readback/sample-spacing");
}

// The assignment boundary runs independently of the earlier step snapshot.
const WriteGate=load("ordered_write.js",{Model,Registry,Safety,Ordered:Executor,Steps},["validate"]);
let gateCases=0;
for (const phase of ["applying","rollback_applying"]) {
    const d=boundaryDriver(phase), context={now:d.state.tx.writeIssuedAt,snapshot:copy(d.live),registry:d.f.registry,
        settingsRevision:"s1",currentSettingsRevision:"s1",enabled:true,paused:false,nativeTokenCurrent:true,safety:safety(d.state.tx.writeIssuedAt)};
    assert.equal(WriteGate.validate(d.pending,d.state,context).valid,true,phase);gateCases++;
    const changes=[
        c=>{c.nativeTokenCurrent=false},c=>{c.paused=true},c=>{c.settingsRevision="s2"},
        c=>{c.safety.observedAt=c.now-501},c=>{c.safety.heldButtons=true},
        c=>{c.snapshot.windows[0].rect.x+=.1},c=>{c.snapshot.windows[1].focused=true},
        c=>{c.snapshot.outputs[0].scale=1.5},c=>{c.registry.sessionEpoch="replacement"},
        c=>{c.now=d.state.tx.writeDeadline+1;c.safety=safety(c.now)},
        c=>{c.now=d.state.tx.writeIssuedAt-1;c.safety=safety(c.now)},
    ];
    for (const change of changes){const c=copy(context);change(c);assert.equal(WriteGate.validate(d.pending,d.state,c).valid,false,phase);gateCases++}
    for (const change of [c=>{c.id="stale"},c=>{c.generation++},c=>{c.rollback=!c.rollback},
            c=>{c.destination.x++},c=>{c.expectedFrom.x++},c=>{c.stepIndex++},c=>{c.identity="other"}]) {
        const c=copy(d.pending);change(c);assert.equal(WriteGate.validate(c,d.state,context).valid,false,phase);gateCases++;
    }
}
console.log("ordered assignment gate: "+gateCases+" cases passed");

assert.equal(run(-1,'reject',true).state.lastResult.status,'committed');
for (let i=0;i<2;i++) {
 const failed=run(i,'reject',true);
 assert.equal(failed.state.lastResult.status,i===0?'failed':'failed-rolled-back');
 assert.deepEqual(failed.live.windows.map(w=>w.rect),failed.original.windows.map(w=>w.rect));
}
console.log('PASS ordered ordinary drift and rejection rollback at each step');

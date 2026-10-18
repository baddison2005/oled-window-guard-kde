"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function source(file){return fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");}
function load(file,imports,names){const c=Object.assign({},imports);vm.runInNewContext(source(file)+"\nthis.api={"+names.join(",")+"};",c);return c.api;}
const Model=load("model.js",{},["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain","rectangle","rectangleContains","rectangleOverlaps","createPlan","validateSnapshot","validatePlan","validRevision"]);
const Registry=load("registry.js",{Model},["createRegistryState","reconcile","snapshotSignature"]);
const Planner=load("planner.js",{Model,Registry},["registryMatches","focusResolved","eligible","rawObstacle","validateGroupCommonFinalPlan","validateRestoreFinalPlan","validateSwapFinalPlan"]);
const Groups=load("groups.js",{Model,Registry},["builtinGroups","groupFrames","groupsFromLibrary","matchingZones","allowedShrink"]);
const GroupSafety=load("group_safety.js",{Model,Planner,Groups},["validateGroupFinalPlan"]);
const Steps=load("swap_steps.js",{Model,Planner,Groups,GroupSafety},["orderSwapSteps","validateSwapSteps"]);
const Safety=load("safety.js",{},["evaluate"]);
const Restore=load("restore.js",{Model,Registry},["captureUndo","consumeRestore"]);
const Executor=load("ordered_executor.js",{Model,Registry,Safety,Steps,Restore},["createOrderedState","transitionOrdered","orderedSafe"]);
const copy=x=>JSON.parse(JSON.stringify(x)),rect=(x,y,width,height)=>({x,y,width,height});
const safety=now=>({observedAt:now,companion:"connected",locked:false,suspended:false,topologyStable:true,
    topologyGeneration:1,topologyOutputIds:["DP-1"],allOutputsQuiet:true,heldButtons:false,
    outputs:[{id:"DP-1",known:true,quiet:true,buttonsDown:false}],capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},
    quietSince:now-5000,quietMs:1000});
function fixture(){
    const area=rect(0,0,400,200),group=Groups.builtinGroups(2)[5];
    const start=Groups.groupFrames(group,area)[0],destination=Groups.groupFrames(group,area)[1];
    const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"e",name:"d",geometry:area,usableRect:area,scale:1}],
        windows:[{identity:"a",appId:"app",outputIds:["DP-1"],rect:start,visible:true,onCurrentDesktop:true,onCurrentActivity:true,
            focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,maximized:false,
            fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]}],focusedIdentity:null,topologyRevision:"topo"};
    const registry=Registry.reconcile(snapshot,Registry.createRegistryState("group-tx"),{sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
    const move={identity:"a",fromRect:start,toRect:destination,permitsRoundingResize:true,roundingResizeLimit:6};
    const plan=Model.createPlan({snapshotRevision:"topo",outputIds:["DP-1"],moves:[move],metadata:{mode:"group-rotation",complete:true,
        maximumWindows:1,groupId:group.id,source:"guard",padding:2,libraryRevision:null,allowTransientOverlap:true}});
    const ordered=Steps.orderSwapSteps(snapshot,registry,plan,{group,now:()=>0,deadlineMs:1000});
    assert.equal(ordered.steps.length,2,JSON.stringify(ordered));
    return {snapshot,registry,group,plan,steps:ordered.steps};
}
function run(mode){
    const f=fixture(),live=copy(f.snapshot);let state=Executor.createOrderedState(),now=10000,command,commands,resizeAttempts=0;
    function send(e){const out=Executor.transitionOrdered(state,e);state=out.state;return out.commands;}
    function observe(destination){live.windows[0].rect=copy(destination);return [20,40].map((offset,i)=>({id:"o"+now+"-"+i,
        now:now+offset,snapshot:copy(live),safety:safety(now+offset),currentSettingsRevision:"s1"}));}
    send({type:"resume"});commands=send({type:"begin",generation:state.generation,warningCleared:true,now,
        snapshot:f.snapshot,registry:f.registry,plan:f.plan,steps:f.steps,group:f.group,
        settingsRevision:"s1",currentSettingsRevision:"s1",safety:safety(now)});
    for(let guard=0;commands.length&&guard<20;guard++){
        command=commands[0];now+=50;
        if(command.type==="requestSnapshot")commands=send({type:"snapshotReady",generation:state.generation,
            commandId:command.id,now,snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now)});
        else if(command.type==="writeGeometry"){
            if(command.destination.x===f.snapshot.windows[0].rect.x&&command.destination.width<f.snapshot.windows[0].rect.width){
                resizeAttempts++;
                if(mode==="resize-refused") commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                    accepted:false,now,currentSettingsRevision:"s1"});
                else if(mode==="partial-retry-refused"&&resizeAttempts===2)
                    commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                        accepted:false,now,currentSettingsRevision:"s1"});
                else if(((mode==="partial-retry"||mode==="partial-retry-refused")&&resizeAttempts===1)||mode==="partial-twice"){
                    const partial=copy(command.destination);partial.width+=mode==="partial-twice"&&resizeAttempts===2?1:2;
                    commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                        accepted:true,observations:observe(partial),now:now+40,currentSettingsRevision:"s1"});
                }else commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                    accepted:true,observations:observe(command.destination),now:now+40,currentSettingsRevision:"s1"});
            }else if(mode==="position-refused"&&!command.rollback)commands=send({type:"writeResult",generation:state.generation,
                commandId:command.id,accepted:false,now,currentSettingsRevision:"s1"});
            else commands=send({type:"writeResult",generation:state.generation,commandId:command.id,
                accepted:true,observations:observe(command.destination),now:now+40,currentSettingsRevision:"s1"});
        }else throw Error("unexpected command");
    }
    return {f,state,live,resizeAttempts,send,getState:()=>state,safety,now};
}
{
    const r=run("partial-retry");assert.equal(r.state.lastResult.status,"committed");
    assert.equal(r.resizeAttempts,2);assert.ok(r.state.undoRecord);
    assert.deepEqual(copy(r.live.windows[0].rect),copy(r.f.plan.moves[0].toRect));
    const postRegistry=Registry.reconcile(r.live,Registry.createRegistryState("group-tx"),{sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
    const queued=r.send({type:"queueRestore",generation:r.getState().generation,now:r.now+100,
        snapshot:copy(r.live),registry:postRegistry,settingsRevision:"s1",sessionEpoch:"group-tx",safety:safety(r.now+100)});
    assert.equal(queued[0].type,"restoreQueued");assert.equal(r.getState().undoRecord,null);
    r.send({type:"pause"});r.send({type:"resume"});
    assert.equal(r.send({type:"queueRestore",generation:r.getState().generation,now:r.now+200,
        snapshot:copy(r.live),registry:postRegistry,settingsRevision:"s1",sessionEpoch:"group-tx",safety:safety(r.now+200)}).length,0);
    assert.equal(r.getState().lastResult.reason,"undo-unavailable");
}
{
    const refused=run("resize-refused");assert.equal(refused.state.lastResult.status,"failed");
    assert.deepEqual(copy(refused.live.windows[0].rect),copy(refused.f.snapshot.windows[0].rect));
    const position=run("position-refused");assert.equal(position.state.lastResult.status,"failed-rolled-back");
    assert.deepEqual(copy(position.live.windows[0].rect),copy(position.f.snapshot.windows[0].rect));
    const repeated=run("partial-twice");assert.equal(repeated.resizeAttempts,2);
    assert.equal(repeated.state.lastResult.status,"failed-uncertain");
    assert.equal(repeated.state.lastResult.unverifiedFrames[0].frame.width,repeated.live.windows[0].rect.width);
    const retryRefused=run("partial-retry-refused");assert.equal(retryRefused.resizeAttempts,2);
    assert.equal(retryRefused.state.lastResult.status,"failed-rolled-back");
    assert.deepEqual(copy(retryRefused.live.windows[0].rect),copy(retryRefused.f.snapshot.windows[0].rect));
}
{
    const f=fixture(),now=10000,tx={initial:f.snapshot,settingsRevision:"s1",safetyTopologyGeneration:1,
        libraryRevision:"revision-1"};
    const current={sourceId:"kde:window-layouts",status:"available",executionReady:true,revision:"revision-1"};
    assert.equal(Executor.orderedSafe(tx,{now,currentSettingsRevision:"s1",safety:safety(now),
        currentLibrary:current,libraryFresh:true}),true);
    assert.equal(Executor.orderedSafe(tx,{now,currentSettingsRevision:"s1",safety:safety(now),
        currentLibrary:Object.assign({},current,{revision:"revision-2"}),libraryFresh:true}),false);
    assert.equal(Executor.orderedSafe(tx,{now,currentSettingsRevision:"s1",safety:safety(now),
        currentLibrary:current,libraryFresh:false}),false);
}
function restoreOnce(rejectExpansion){
    const r=run("normal"),postRegistry=Registry.reconcile(r.live,Registry.createRegistryState("group-tx"),
        {sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
    const postMove=copy(r.live.windows[0].rect);
    let now=r.now+100,commands=r.send({type:"queueRestore",generation:r.getState().generation,now,
        snapshot:copy(r.live),registry:postRegistry,settingsRevision:"s1",sessionEpoch:"group-tx",safety:safety(now)});
    assert.equal(commands[0].type,"restoreQueued");
    commands=r.send({type:"begin",generation:r.getState().generation,warningCleared:true,now:now+10,
        snapshot:copy(r.live),registry:postRegistry,plan:commands[0].plan,steps:commands[0].steps,
        settingsRevision:"s1",currentSettingsRevision:"s1",safety:safety(now+10)});
    assert.equal(commands[0].type,"requestSnapshot");
    for(let guard=0;commands.length&&guard<20;guard++){
        const cmd=commands[0];now+=50;
        if(cmd.type==="requestSnapshot")commands=r.send({type:"snapshotReady",generation:r.getState().generation,
            commandId:cmd.id,now,snapshot:copy(r.live),currentSettingsRevision:"s1",safety:safety(now)});
        else if(cmd.type==="writeGeometry"){
            if(rejectExpansion&&cmd.destination.width>cmd.expectedFrom.width){
                commands=r.send({type:"writeResult",generation:r.getState().generation,commandId:cmd.id,
                    accepted:false,now,currentSettingsRevision:"s1"});
                continue;
            }
            r.live.windows[0].rect=copy(cmd.destination);
            const observations=[20,40].map((offset,i)=>({id:"restore-"+guard+"-"+i,now:now+offset,
                snapshot:copy(r.live),currentSettingsRevision:"s1",safety:safety(now+offset)}));
            commands=r.send({type:"writeResult",generation:r.getState().generation,commandId:cmd.id,
                accepted:true,observations,now:now+40,currentSettingsRevision:"s1"});
        }
    }
    assert.equal(r.getState().lastResult.status,rejectExpansion?"failed-rolled-back":"committed");
    assert.equal(r.getState().undoRecord,null);
    assert.deepEqual(copy(r.live.windows[0].rect),rejectExpansion?postMove:copy(r.f.snapshot.windows[0].rect));
}
restoreOnce(false);
restoreOnce(true);
console.log("core group transaction: partial resize and restore fixtures passed");

// Imported-layout freshness must hold across the entire transaction, not just
// at Begin. Exercise actual reducer transitions and one-shot Restore tokens.
function importedFixture() {
    const f=fixture();
    const library={sourceId:"kde:window-layouts",status:"available",executionReady:true,
        revision:"library-1",padding:2,
        layouts:[{id:"left",name:"Left",x:0,y:0,width:2/3,height:1,groupId:"pair"},
            {id:"middle",name:"Middle",x:1/6,y:0,width:2/3,height:1,groupId:"pair"}],
        groups:[{id:"pair",name:"Pair",layoutIds:["left","middle"]}]};
    const group=Groups.groupsFromLibrary(library)[0],frames=Groups.groupFrames(group,f.snapshot.outputs[0].usableRect);
    f.snapshot.windows[0].rect=copy(frames[0]);
    f.registry=Registry.reconcile(f.snapshot,Registry.createRegistryState("group-tx"),
        {sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
    const plan=Model.createPlan({snapshotRevision:"topo",outputIds:["DP-1"],moves:[{
        identity:"a",fromRect:frames[0],toRect:frames[1],permitsRoundingResize:true,roundingResizeLimit:6}],
        metadata:{mode:"group-rotation",complete:true,maximumWindows:1,groupId:group.id,
            source:group.source,padding:2,libraryRevision:library.revision,allowTransientOverlap:true}});
    const ordered=Steps.orderSwapSteps(f.snapshot,f.registry,plan,{group,library,now:()=>0,deadlineMs:1000});
    assert.equal(ordered.steps.length,2);
    return Object.assign(f,{library,group,plan,steps:ordered.steps});
}
function importedDriver(phase="begin") {
    const f=importedFixture(),live=copy(f.snapshot);
    let state=Executor.createOrderedState(),pending=null,now=10000;
    function send(event) {
        const result=Executor.transitionOrdered(state,event);state=result.state;
        pending=result.commands[0]||null;return result.commands;
    }
    const fresh=()=>({currentLibrary:copy(f.library),libraryFresh:true});
    function begin(plan=f.plan,steps=f.steps,snapshot=f.snapshot,registry=f.registry) {
        return Object.assign({type:"begin",generation:state.generation,warningCleared:true,now,
            snapshot:copy(snapshot),registry,plan,steps,group:f.group,library:f.library,
            settingsRevision:"s1",currentSettingsRevision:"s1",safety:safety(now)},fresh());
    }
    function snapshot() {
        now+=50;
        return Object.assign({type:"snapshotReady",generation:state.generation,commandId:pending.id,
            now,snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now)},fresh());
    }
    function result(accepted=true,partial=false) {
        const command=pending;now+=50;
        if (accepted) live.windows[0].rect=copy(command.destination);
        if (accepted && partial) live.windows[0].rect.width+=1;
        return {type:"writeResult",generation:state.generation,commandId:command.id,accepted,now:now+40,
            observations:accepted?[20,40].map((offset,i)=>Object.assign({id:"imported-"+now+"-"+i,
                now:now+offset,snapshot:copy(live),currentSettingsRevision:"s1",safety:safety(now+offset)},fresh())):[]};
    }
    function queue() {
        now+=50;
        const registry=Registry.reconcile(live,Registry.createRegistryState("group-tx"),
            {sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
        return Object.assign({type:"queueRestore",generation:state.generation,now,snapshot:copy(live),registry,
            settingsRevision:"s1",sessionEpoch:"group-tx",safety:safety(now)},fresh());
    }
    send({type:"resume"});
    if (phase!=="begin") send(begin());
    if (["resize_ack","retry_snapshot","retry_ack","place_snapshot","place_ack","reconcile","rollback_snapshot","rollback_ack",
         "restore_queue","restore_begin","restore_snapshot","restore_ack"].includes(phase)) send(snapshot());
    if (phase.startsWith("retry")) {
        send(result(true,true));
        assert.equal(state.tx.retryCount,1);
        if (phase==="retry_ack") send(snapshot());
    }
    if (["place_snapshot","place_ack","reconcile","rollback_snapshot","rollback_ack",
         "restore_queue","restore_begin","restore_snapshot","restore_ack"].includes(phase)) send(result());
    if (["place_ack","reconcile","rollback_snapshot","rollback_ack",
         "restore_queue","restore_begin","restore_snapshot","restore_ack"].includes(phase)) send(snapshot());
    if (["reconcile","rollback_snapshot","rollback_ack"].includes(phase)) send(result(false));
    if (["rollback_snapshot","rollback_ack"].includes(phase)) send(snapshot());
    if (phase==="rollback_ack") send(snapshot());
    if (phase.startsWith("restore")) {
        send(result());assert.equal(state.lastResult.status,"committed");assert.ok(state.undoRecord);
        if (phase!=="restore_queue") {
            const queued=send(queue());assert.equal(queued[0].type,"restoreQueued");
            const registry=Registry.reconcile(live,Registry.createRegistryState("group-tx"),
                {sessionEpoch:"group-tx",selectedOutputIds:["DP-1"]});
            if (phase!=="restore_begin") send(begin(queued[0].plan,queued[0].steps,live,registry));
            else pending=Object.assign(queued[0],{registry});
            if (phase==="restore_ack") send(snapshot());
        }
    }
    return {f,live,send,begin,snapshot,result,queue,
        get state(){return state},get pending(){return pending}};
}
const libraryFaults=[
    ["changed revision",event=>{event.currentLibrary.revision="library-2"}],
    ["partial save",event=>{event.currentLibrary.status="unstable";event.currentLibrary.executionReady=false}],
    ["missing provider",event=>{event.currentLibrary.status="unavailable"}],
    ["execution disabled",event=>{event.currentLibrary.executionReady=false}],
    ["cached read",event=>{event.libraryFresh=false}],
    ["unknown freshness",event=>{delete event.libraryFresh}],
    ["missing library",event=>{delete event.currentLibrary}],
];
let importedCases=0;
for (const phase of ["begin","resize_snapshot","resize_ack","retry_snapshot","retry_ack","place_snapshot","place_ack",
    "reconcile","rollback_snapshot","rollback_ack","restore_queue","restore_begin","restore_snapshot","restore_ack"]) {
    for (const [label,mutate] of libraryFaults) {
        const d=importedDriver(phase),sourceLibrary=JSON.stringify(d.f.library);
        let event;
        if (phase==="begin") event=d.begin();
        else if (phase==="restore_queue") event=d.queue();
        else if (phase==="restore_begin") event=d.begin(d.pending.plan,d.pending.steps,d.live,d.pending.registry);
        else if (phase.endsWith("ack")) event=d.result();
        else event=d.snapshot();
        if (phase.endsWith("ack")) mutate(event.observations[1]);
        else mutate(event);
        const commands=d.send(event);
        assert.ok(commands.every(c=>c.type!=="writeGeometry"),phase+": "+label);
        if (phase.endsWith("ack")) {
            assert.equal(commands.length,1);assert.equal(commands[0].purpose,"reconcile");
            const follow=d.snapshot();mutate(follow);
            assert.equal(d.send(follow).length,0);
            assert.equal(d.state.lastResult.status,"failed-uncertain");
            assert.ok(d.state.lastResult.unverifiedFrames.length>0);
        } else {
            assert.equal(commands.length,0);
            if (phase==="restore_queue") {
                assert.equal(d.state.lastResult.status,"restore-skipped");
                assert.equal(d.state.lastResult.reason,"library-changed");
                assert.equal(d.state.undoRecord,null);
                assert.equal(d.send(d.queue()).length,0);
                assert.equal(d.state.lastResult.reason,"undo-unavailable");
            } else assert.equal(d.state.phase,"Paused");
        }
        assert.equal(JSON.stringify(d.f.library),sourceLibrary,"provider fixture must remain unchanged");
        importedCases++;
    }
}
// Fresh imported data permits a full move and one full Restore, with no second
// Restore token or implicit reuse of the original library/undo record.
{
    const d=importedDriver("restore_snapshot");
    let commands=d.send(d.snapshot()),writes=0;
    for (let guard=0;commands.length&&guard<20;guard++) {
        if (commands[0].type==="writeGeometry") {writes++;commands=d.send(d.result());}
        else {assert.equal(commands[0].type,"requestSnapshot");commands=d.send(d.snapshot());}
    }
    assert.equal(writes,2);assert.equal(d.state.lastResult.status,"committed");
    assert.deepEqual(d.live.windows[0].rect,copy(d.f.snapshot.windows[0].rect));
    assert.equal(d.state.undoRecord,null);assert.equal(d.send(d.queue()).length,0);
    assert.equal(d.state.lastResult.reason,"undo-unavailable");
    importedCases++;
}
for (const boundary of ["pause","activity","session","topology","settingsChanged","close"]) {
    const d=importedDriver("restore_begin"),queued=copy(d.pending);
    assert.equal(d.state.undoRecord,null);
    d.send({type:boundary});d.send({type:"resume"});
    assert.equal(d.send(d.begin(queued.plan,queued.steps,d.live,queued.registry)).length,0);
    assert.equal(d.state.lastResult.reason,"restore-not-queued-or-stale");
    assert.equal(d.state.pendingRestore,null);assert.equal(d.state.undoRecord,null);
    importedCases++;
}
console.log("imported group freshness: "+importedCases+" transaction matrix cases passed");

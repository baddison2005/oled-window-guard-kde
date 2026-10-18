"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function load(name,deps={}) {
 const c={...deps};vm.runInNewContext(fs.readFileSync("core/"+name+".js","utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*$/gm,""),c);return c;
}
const Model=load("model"),Registry=load("registry",{Model}),Planner=load("planner",{Model,Registry}),Safety=load("safety");
const Native=load("native_observation"),NS=load("native_safety",{Native,Model});
const Tx=load("transaction",{Model,Registry,Planner,Safety}),Gate=load("guarded_write",{Model,Registry,Planner,Safety});
const clone=v=>JSON.parse(JSON.stringify(v));
const rect=(x,y,width,height)=>({x,y,width,height});
const output={id:"panel-id",connector:"DP-1",edid:"panel",geometry:rect(0,0,1000,800),usableRect:rect(0,0,1000,800),scale:1};
const w={identity:"window-1",appId:"app",outputIds:[output.id],rect:rect(100,100,100,100),visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]};
const snapshot={outputs:[output],windows:[w],focusedIdentity:null,topologyRevision:"topology-1"};
const sample={schema:2,epoch:"native-1",sequence:4,topologyGeneration:1,sessionGeneration:1,focusGeneration:1,
 lockState:"unlocked",sleepState:"awake",sessionActivity:"active",pointer:1,button:2,axis:1,keyboard:0,unknownFocusKeyboard:0,focusKnown:true,
 ageMs:3000,buttonKnown:true,buttonsDown:false,outputIdentityComplete:true,outputs:[{id:"DP-1",events:4,ageMs:3000}],
 immediateCancellation:true,otherInputObserved:false,cancellationGeneration:5};
const options={immediateCancellation:true,companionConnected:true,topologyStable:true,quietMs:1000};
let count=0;
const noisy=Model.normalizeReadbackRectangle(rect(69.85833252075342,165.78169627619351,220,128.00000000000003));
assert.equal(noisy.height,128);assert.equal(noisy.x,69.85833252075342);count++;
assert.equal(Model.normalizeReadbackRectangle(rect(1,2,220.5,128.25)).height,128.25);count++;
assert.equal(Model.normalizeReadbackRectangle(rect(1,2,220,128.00001)).height,128.00001);count++;
assert.equal(Model.normalizeReadbackRectangle(rect(1,2,220,0)),null);count++;
assert.equal(NS.normalize(sample,snapshot,10000,options).safe,true);count++;
assert.equal(NS.normalize(sample,snapshot,10000,{...options,quietMs:0}).safe,true);count++;
for (const changes of [{lockState:"unknown"},{sleepState:"unknown"},{sessionActivity:"inactive"},{buttonKnown:false},{buttonsDown:true},{focusKnown:false},{otherInputObserved:true},{immediateCancellation:false},{cancellationGeneration:NaN},{outputs:[{id:"DP-1",events:4,ageMs:-1}]},{outputs:[{id:"HDMI",events:4,ageMs:3000}]},{outputs:[{id:"DP-1",events:4,ageMs:500}]}]) {
 assert.equal(NS.normalize({...sample,...changes},snapshot,10000,options).safe,false);count++;
}
for (const changes of [{immediateCancellation:false},{companionConnected:false},{topologyStable:false},{quietMs:NaN}]) {
 assert.equal(NS.normalize(sample,snapshot,10000,{...options,...changes}).safe,false);count++;
}
const registry=Registry.reconcile(snapshot,Registry.createRegistryState("session-1"),{sessionEpoch:"session-1",selectedOutputIds:[output.id]});
const plan=Planner.planOrdinaryShift(snapshot,registry,{outputId:output.id,shiftRangePercent:100,maximumWindows:1,random:()=>.5,maxEvaluations:5000,now:()=>10}).plan;
assert.ok(plan && plan.moves.length===1);
let state=Tx.createTransactionState();
function feed(event) {const result=Tx.transition(state,event);state=result.state;return result.commands;}
function context(now) {return {now,snapshot,registry,safety:NS.normalize({...sample,outputs:[{id:"DP-1",events:4,ageMs:now-7000}]},snapshot,now,options).snapshot,safetyMaxAgeMs:500,currentSettingsRevision:"settings-1",settingsRevision:"settings-1",enabled:true,paused:false,nativeTokenCurrent:true};}
feed({type:"resume"});
let cmds=feed({...context(10000),type:"begin",generation:state.generation,plan,settingsRevision:"settings-1",warningAvailable:true,warningMs:3000});
feed({type:"warningShown",generation:state.generation,commandId:cmds[0].id,shown:true,now:10000});
cmds=feed({type:"warningDeadline",generation:state.generation,commandId:cmds[0].id,now:13000});
cmds=feed({type:"warningHidden",generation:state.generation,commandId:cmds[0].id,hidden:true,now:13000});
cmds=feed({...context(13000),type:"snapshotReady",generation:state.generation,commandId:cmds[0].id});
assert.equal(cmds.length,1);assert.equal(cmds[0].type,"writeGeometry");
const cmd=cmds[0],ctx=context(13000);
assert.equal(Gate.validate(cmd,state,ctx).valid,true);count++;
for (const changes of [{enabled:false},{paused:true},{nativeTokenCurrent:false},{settingsRevision:"changed"},{now:14000},{safety:{...ctx.safety,heldButtons:true}},{safety:{...ctx.safety,topologyGeneration:2}},{snapshot:{...snapshot,focusedIdentity:w.identity,windows:[{...w,focused:true}]}},{snapshot:{...snapshot,windows:[{...w,rect:rect(101,100,100,100)}]}}]) {
 assert.equal(Gate.validate(cmd,state,{...ctx,...changes}).valid,false);count++;
}
for(const changes of [{id:"old"},{generation:state.generation-1},{transactionId:"wrong"},{identity:"other"},{destination:rect(400,400,100,100)},{rollback:true}]) {
 assert.equal(Gate.validate({...cmd,...changes},state,ctx).valid,false);count++;
}
const cancelled=Tx.transition(state,{type:"invalidate",reason:"native-input"}).state;
assert.equal(Gate.validate(cmd,cancelled,ctx).valid,false);count++;
// A quantized frame is safe to reverse only while it exactly matches the
// previously validated readback, even when a later nudge fits readback tolerance.
feed({type:"writeAck",generation:state.generation,commandId:cmd.id,accepted:true});
const quantized=clone(cmd.destination);quantized.x+=.2;
const acceptedSnapshot=clone(snapshot);acceptedSnapshot.windows[0].rect=quantized;
const acceptedRegistry=Registry.reconcile(acceptedSnapshot,registry,{sessionEpoch:"session-1",selectedOutputIds:[output.id]});
const acceptedContext={...context(13025),snapshot:acceptedSnapshot,registry:acceptedRegistry};
feed({...acceptedContext,type:"geometryObserved",generation:state.generation,accepted:true,geometry:quantized,observationId:"one"});
assert.equal(state.transaction.observations.length,1);count++;
let rollback=feed({type:"settlementDeadline",generation:state.generation,commandId:cmd.id,now:13700});
const rollbackState=clone(state);
const lateContext={...context(13700),snapshot:acceptedSnapshot,registry:acceptedRegistry};
rollback=feed({...lateContext,type:"snapshotReady",generation:state.generation,commandId:rollback[0].id});
assert.equal(rollback[0].rollback,true);count++;
assert.equal(rollback[0].expectedFrom.x,quantized.x);count++;
assert.equal(Gate.validate(rollback[0],state,lateContext).valid,true);count++;
assert.equal(Gate.validate({...rollback[0],expectedFrom:cmd.destination},state,lateContext).valid,false);count++;
const nudged=clone(acceptedSnapshot);nudged.windows[0].rect.x+=.1;
const nudgedRegistry=Registry.reconcile(nudged,acceptedRegistry,{sessionEpoch:"session-1",selectedOutputIds:[output.id]});
const pending=rollbackState.pendingCommand;
const refused=Tx.transition(rollbackState,{...lateContext,snapshot:nudged,registry:nudgedRegistry,type:"snapshotReady",generation:rollbackState.generation,commandId:pending.id});
assert.equal(refused.state.lastResult.reason,"external-frame-won");assert.equal(refused.commands.length,0);count++;
console.log("guarded writer: "+count+" normalization and assignment-boundary checks passed");

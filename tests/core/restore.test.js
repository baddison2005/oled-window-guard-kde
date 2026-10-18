"use strict";
const assert=require("node:assert/strict");
const fs=require("node:fs");
const vm=require("node:vm");
function source(file){return fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");}
function load(file,imports,names){const c=Object.assign({},imports);vm.runInNewContext(source(file)+"\nthis.api={"+names.join(",")+"};",c);return c.api;}
const Model=load("model.js",{},["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain","createPlan","validatePlan","validateSnapshot","rectangle","validRevision"]);
const Registry=load("registry.js",{Model},["snapshotSignature"]);
const Restore=load("restore.js",{Model,Registry},["captureUndo","consumeRestore","captureSingleUndo"]);
const rect=(x,y,w,h)=>({x,y,width:w,height:h});
const copy=x=>JSON.parse(JSON.stringify(x));
function fixture(){
 const r0=rect(0,0,100,100),r1=rect(110,0,100,100);
 const snapshot={outputs:[{id:"DP-1",geometry:rect(0,0,500,400),usableRect:rect(0,0,500,400),scale:1}],
  windows:[{identity:"opaque-1",appId:"app",outputIds:["DP-1"],rect:r1,visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]}],
  focusedIdentity:null,topologyRevision:"topo-1"};
 const plan=Model.createPlan({snapshotRevision:"topo-1",outputIds:["DP-1"],moves:[{identity:"opaque-1",fromRect:r0,toRect:r1}],metadata:{mode:"zone_drift",allowTransientOverlap:false}});
 const steps=[{identity:"opaque-1",fromRect:r0,toRect:r1,kind:"place"}];
 return {snapshot,plan,steps,record:Restore.captureUndo({plan,steps,postSnapshot:snapshot,settingsRevision:"settings-1",sessionEpoch:"session-1"})};
}
function validate(){return {valid:true};}
function consume(record,snapshot,extras={}){return Restore.consumeRestore(record,Object.assign({snapshot,settingsRevision:"settings-1",sessionEpoch:"session-1",validateInverse:validate},extras));}
{
 const f=fixture(); assert.ok(f.record);
 const result=consume(f.record,f.snapshot);
 assert.equal(result.reason,null);assert.equal(result.record,null);
 assert.deepEqual(JSON.parse(JSON.stringify(result.plan.moves)),[{identity:"opaque-1",fromRect:rect(110,0,100,100),toRect:rect(0,0,100,100)}]);
 assert.deepEqual(JSON.parse(JSON.stringify(result.steps)),[{identity:"opaque-1",fromRect:rect(110,0,100,100),toRect:rect(0,0,100,100),kind:"reverse"}]);
}
{
 const f=fixture(),changed=copy(f.snapshot);changed.windows[0].rect.x+=1;
 assert.equal(consume(f.record,changed).reason,"desktop-changed");
}
{
 const f=fixture();
 assert.equal(consume(f.record,f.snapshot,{validateInverse:undefined}).reason,"inverse-validation-required");
 assert.equal(consume(f.record,f.snapshot,{validateInverse:()=>false}).reason,"inverse-validation-failed");
 assert.equal(consume(f.record,f.snapshot,{validateInverse:()=>{throw Error("validator");}}).reason,"inverse-validation-failed");
}
{
 // Consumption occurs before warning queueing. Skipping or cancelling that
 // queued operation cannot restore the already-consumed token.
 for(const disposition of ["queued","skipped","cancelled"]){
  const f=fixture(),attempt=consume(f.record,f.snapshot);
  assert.equal(attempt.record,null,disposition);
  assert.equal(consume(attempt.record,f.snapshot).reason,"undo-unavailable",disposition);
 }
}
{
 const f=fixture();
 assert.equal(consume(f.record,f.snapshot,{sessionEpoch:"session-after-kwin-reload"}).reason,"session-changed");
 assert.equal(consume(f.record,f.snapshot,{settingsRevision:"settings-2"}).reason,"settings-changed");
}
{
 const f=fixture(),changed=copy(f.snapshot);changed.topologyRevision="topo-2";
 assert.equal(consume(f.record,changed).reason,"desktop-changed");
}
{
 const f=fixture(),malformed=copy(f.record);delete malformed.postSnapshot;
 assert.equal(consume(malformed,f.snapshot).reason,"invalid-snapshot-or-record");
}
console.log("guarded restore core tests passed");

{
 const f=fixture(),pre=copy(f.snapshot);pre.windows[0].rect=copy(f.plan.moves[0].fromRect);
 const tx={plan:copy(f.plan),snapshot:pre,settingsRevision:"settings-1"};tx.plan.metadata.mode="ordinary_shift";delete tx.plan.metadata.allowTransientOverlap;
 const actual=copy(f.snapshot);actual.windows[0].rect.x=110.25;
 const args={transaction:tx,result:{status:"committed",identity:"opaque-1",frame:actual.windows[0].rect},snapshot:actual,sessionEpoch:"session-1"};
 const record=Restore.captureSingleUndo(args);assert.ok(record);
 assert.equal(record.plan.metadata.allowTransientOverlap,false);
 assert.equal(Restore.captureSingleUndo({...args,result:{...args.result,identity:"replacement"}}),null);
 assert.equal(record.steps[0].fromRect.x,110.25);assert.equal(record.steps[0].toRect.x,0);
 assert.equal(consume(record,actual).reason,null);
 const changed=copy(actual);changed.windows[0].rect.x+=.1;
 assert.equal(consume(record,changed).reason,"desktop-changed");
 assert.equal(Restore.captureSingleUndo({...args,result:{status:"cancelled"}}),null);
 assert.equal(Restore.captureSingleUndo({...args,snapshot:changed}),null);
 const drifted=copy(pre);drifted.windows[0].resizable=false;
 assert.equal(Restore.captureSingleUndo({...args,transaction:{...tx,snapshot:drifted}}),null);
 console.log("single commit undo: quantized receipt and external-change rejection passed");
}

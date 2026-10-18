"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
function source(file) { return fs.readFileSync("core/" + file, "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/gm, ""); }
const context = {};
vm.runInNewContext(source("model.js") + "\nthis.Model={applicationExcluded,roundingResizeAllowed,roundingContactAllowed,movementCollision,copyPlain,rectangle,rectangleContains,rectangleOverlaps,createPlan,validateSnapshot,validatePlan};\n" +
    source("registry.js") + "\nthis.Registry={createRegistryState,reconcile,snapshotSignature};\n" +
    source("planner.js") + "\nthis.Planner={rawObstacle,registryMatches,eligible,validateSwapFinalPlan};\n" +
    source("swap_steps.js") + "\nthis.api={orderSwapSteps,validateSwapSteps};", context);
const api = context.api;
const rect = (x,y,width,height)=>({x,y,width,height});
const windowRecord = (identity, frame, extras={})=>Object.assign({identity,appId:"app",outputIds:["DP-1"],rect:frame,
    visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,
    movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]},extras);
function fixture(area, windows, allowTransientOverlap=false) {
    const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"edid",name:"Display",geometry:area,usableRect:area,scale:1}],
        windows,focusedIdentity:null,topologyRevision:"rev"};
    const registry=context.Registry.reconcile(snapshot,context.Registry.createRegistryState("steps"),
        {sessionEpoch:"steps",selectedOutputIds:["DP-1"]});
    const moves=windows.slice(0,2).map((w,i)=>({identity:w.identity,fromRect:w.rect,toRect:windows[1-i].rect}));
    const plan=context.Model.createPlan({snapshotRevision:"rev",outputIds:["DP-1"],moves,
        metadata:{mode:"swap",maximumWindows:2,complete:true,allowTransientOverlap}});
    return {snapshot,registry,plan};
}
{
    const f=fixture(rect(0,0,300,200),[windowRecord("a",rect(0,0,100,100)),windowRecord("b",rect(100,0,100,100))]);
    const ordered=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{now:()=>0,deadlineMs:1000});
    assert.equal(ordered.reason,null);
    assert.ok(ordered.steps.some(s=>s.kind==="stage"));
    assert.equal(api.validateSwapSteps(f.snapshot,f.registry,f.plan,ordered.steps).valid,true);
    for (let i=0;i<ordered.steps.length;i++) {
        const tampered=JSON.parse(JSON.stringify(ordered.steps));
        tampered[i].fromRect.x += 1;
        assert.equal(api.validateSwapSteps(f.snapshot,f.registry,f.plan,tampered).valid,false);
    }
}
{
    const f=fixture(rect(0,0,200,100),[windowRecord("a",rect(0,0,100,100)),windowRecord("b",rect(100,0,100,100))]);
    const noStage=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{now:()=>0,deadlineMs:1000});
    assert.equal(noStage.reason,"no-safe-step-order");
    assert.equal(noStage.steps.length,0);
    f.plan.metadata.allowTransientOverlap=true;
    const explicit=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{now:()=>0,deadlineMs:1000});
    assert.equal(explicit.reason,null);
    assert.equal(explicit.steps.length,2);
    assert.equal(api.validateSwapSteps(f.snapshot,f.registry,f.plan,explicit.steps).valid,true);
}
{
    const f=fixture(rect(0,0,300,200),[windowRecord("a",rect(0,0,100,100)),windowRecord("b",rect(100,0,100,100))]);
    const exhausted=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{maxEvaluations:1,now:()=>0,deadlineMs:1000});
    assert.equal(exhausted.steps.length,0);
    assert.equal(exhausted.reason,"evaluation_budget_exhausted");
    const cancelled=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{shouldCancel:()=>true,now:()=>0,deadlineMs:1000});
    assert.equal(cancelled.steps.length,0);
    assert.equal(cancelled.reason,"cancelled");
}
// A saturated grid has no staging rectangle. Cancellation must be observed
// during candidate generation, even before any search evaluation is counted.
for (const origin of [0,-100.25]) for (const penetration of [-1,0,.005,.01,.011,1]) {
    const a=rect(origin,-50.5,100,60),b=rect(origin+100-penetration,-50.5,100,60);
    assert.equal(context.stepOverlapsNearby(a,b),context.Model.rectangleOverlaps(a,b));
    assert.equal(context.stepOverlapsNearby(b,a),context.Model.rectangleOverlaps(b,a));
}
{
    const windows = Array.from({length:255}, (_, i) => windowRecord(String(i),
        rect((i % 17)*110, Math.floor(i / 17)*65, 100, 60), {movable:i<2}));
    const f=fixture(rect(0,0,1920,975),windows);
    let polls=0;
    const canceled=api.orderSwapSteps(f.snapshot,f.registry,f.plan,
        {now:()=>0,deadlineMs:1000,shouldCancel:()=>++polls>=5});
    assert.equal(canceled.reason,"cancelled");
    assert.equal(canceled.steps.length,0);
    assert.equal(canceled.stats.evaluated,0);
    assert.equal(polls,5);
    const noSpace=api.orderSwapSteps(f.snapshot,f.registry,f.plan,{now:()=>0,deadlineMs:1000});
    assert.equal(noSpace.reason,"no-safe-step-order");
    assert.equal(noSpace.steps.length,0);
}
// The budget includes initial validation, and both overlap policies discard
// generated steps when cancellation arrives during final replay validation.
for (const overlap of [false,true]) {
    const f=fixture(rect(0,0,300,200),[windowRecord("a",rect(0,0,100,100)),
        windowRecord("b",rect(100,0,100,100))],overlap);
    let clockReads=0;
    const expired=api.orderSwapSteps(f.snapshot,f.registry,f.plan,
        {now:()=>++clockReads<3?0:100,deadlineMs:30});
    assert.equal(expired.reason,"deadline_exceeded");
    assert.equal(expired.steps.length,0);
    let canceled=false;
    const original=context.Planner.validateSwapFinalPlan;
    let validations=0;
    context.Planner.validateSwapFinalPlan=function(...args) {
        const result=original(...args);
        if (++validations===2) canceled=true;
        return result;
    };
    try {
        const result=api.orderSwapSteps(f.snapshot,f.registry,f.plan,
            {now:()=>0,deadlineMs:1000,shouldCancel:()=>canceled});
        assert.equal(result.reason,"cancelled");
        assert.equal(result.steps.length,0);
        assert.equal(validations,2);
    } finally { context.Planner.validateSwapFinalPlan=original; }
}
console.log("core swap steps: tests passed");

"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
function source(file) { return fs.readFileSync("core/" + file, "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/gm, ""); }
function load(file, imports, names) {
    const context=Object.assign({},imports);
    vm.runInNewContext(source(file)+"\nthis.api={"+names.join(",")+"};",context);
    return context.api;
}
const Model=load("model.js",{},["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain","rectangle","rectangleContains","rectangleOverlaps","createPlan","validateSnapshot","validatePlan"]);
const Registry=load("registry.js",{Model},["createRegistryState","reconcile","snapshotSignature"]);
const Planner=load("planner.js",{Model,Registry},["registryMatches","focusResolved","eligible","rawObstacle","planOrdinaryShift","validateSwapFinalPlan","validateCoordinatedFinalPlan"]);
const Cycles=require("../../core/swap_cycles.js");
const FreeSwap=load("free_swap.js",{},["proposeFreeSwap","proposeCoordinatedShift"]);
const Steps=load("swap_steps.js",{Model,Planner},["orderSwapSteps","validateSwapSteps"]);
const Swap=load("swap.js",{Model,Planner,Cycles,FreeSwap,Steps},["planSwap","planCoordinatedShift"]);
const rect=(x,y,width,height)=>({x,y,width,height});
const win=(identity,frame,extra={})=>Object.assign({identity,appId:"app",outputIds:["DP-1"],rect:frame,
    visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,
    movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]},extra);
function prepared(area,windows) {
    const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"edid",name:"display",geometry:area,usableRect:area,scale:1}],
        windows,focusedIdentity:null,topologyRevision:"topo"};
    const registry=Registry.reconcile(snapshot,Registry.createRegistryState("swap"),{sessionEpoch:"swap",selectedOutputIds:["DP-1"]});
    return {snapshot,registry};
}
function options(extra={}) { return Object.assign({outputId:"DP-1",maximumWindows:2,sizeTolerance:.1,
    allowTransientOverlap:false,keepSimilarWindowsTogether:false,random:()=>.2,now:()=>0,deadlineMs:1000},extra); }
{
    const f=prepared(rect(0,0,300,200),[win("a",rect(0,0,100,100)),win("b",rect(100,0,100,100))]);
    const result=Swap.planSwap(f.snapshot,f.registry,options());
    assert.ok(result.plan && result.plan.moves.length===2);
    assert.ok(result.steps.length>=3);
    assert.equal(Planner.validateSwapFinalPlan(f.snapshot,f.registry,result.plan).valid,true);
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps).valid,true);
}
{
    const f=prepared(rect(0,0,200,100),[win("a",rect(0,0,100,100)),win("b",rect(100,0,100,100))]);
    assert.equal(Swap.planSwap(f.snapshot,f.registry,options()).plan,null);
    const explicit=Swap.planSwap(f.snapshot,f.registry,options({allowTransientOverlap:true}));
    assert.ok(explicit.plan);
    assert.equal(explicit.steps.length,2);
}
{
    const f=prepared(rect(0,0,400,200),[win("a",rect(0,0,100,100)),win("b",rect(100,0,100,100)),
        win("c",rect(300,0,100,100),{movable:false})]);
    const result=Swap.planSwap(f.snapshot,f.registry,options());
    assert.ok(result.plan);
    assert.equal(Planner.validateSwapFinalPlan(f.snapshot,f.registry,result.plan).valid,true);
    assert.ok(result.plan.moves.every(m=>m.identity!=="c"));
}
{
    const f=prepared(rect(0,0,400,200),[win("a",rect(0,0,100,100)),win("b",rect(108,0,100,100),{movable:false}),
        win("c",rect(300,0,100,100))]);
    const result=Swap.planSwap(f.snapshot,f.registry,options({keepSimilarWindowsTogether:true}));
    assert.ok(result.trace.some(item=>item.identity==="a"&&item.reason==="ineligible_neighbour"));
    assert.equal(result.plan,null);
}
{
    const f=prepared(rect(0,0,600,200),[win("a",rect(0,0,100,100)),win("b",rect(108,0,100,100)),
        win("blocked",rect(216,0,100,100),{movable:false}),win("remote",rect(450,0,100,100))]);
    const result=Swap.planSwap(f.snapshot,f.registry,options({keepSimilarWindowsTogether:true,maximumWindows:3}));
    assert.ok(result.trace.some(item=>item.identity==="a"&&item.reason==="ineligible_neighbour"));
    assert.ok(result.trace.some(item=>item.identity==="b"&&item.reason==="ineligible_neighbour"));
    assert.equal(result.plan,null);
}
{
    const f=prepared(rect(0,0,800,300),[win("a",rect(0,0,100,100)),win("b",rect(108,0,100,100)),
        win("c",rect(400,0,100,100)),win("d",rect(508,0,100,100))]);
    const result=Swap.planSwap(f.snapshot,f.registry,options({keepSimilarWindowsTogether:true,maximumWindows:4}));
    assert.ok(result.plan);
    assert.equal(result.plan.metadata.strategy,"grouped-cycle");
    assert.equal(result.plan.moves.length,4);
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps).valid,true);
}
{
    const f=prepared(rect(0,0,1000,800),[win("large",rect(20,20,420,260)),
        win("small",rect(500,30,150,110)),win("medium",rect(230,400,260,170))]);
    const result=Swap.planSwap(f.snapshot,f.registry,options({maximumWindows:3,allowTransientOverlap:true,
        random:()=>.37,maxEvaluations:5000}));
    assert.ok(result.plan);
    assert.equal(result.plan.metadata.strategy,"free-swap");
    assert.equal(Planner.validateSwapFinalPlan(f.snapshot,f.registry,result.plan).valid,true);
}
{
    const f=prepared(rect(0,0,1000,800),[win("a",rect(100,300,180,180)),win("b",rect(650,300,180,180))]);
    const random=(()=>{let n=0;return()=>n++===0?.2:n%2===0?.5:.25;})();
    const result=Swap.planCoordinatedShift(f.snapshot,f.registry,{outputId:"DP-1",maximumWindows:2,
        shiftRangePercent:90,shiftHorizontalOrder:true,shiftVerticalOrder:false,random,now:()=>0,deadlineMs:1000});
    assert.equal(result.reason,"coordinated_shift");
    assert.equal(Planner.validateCoordinatedFinalPlan(f.snapshot,f.registry,result.plan).valid,true);
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps).valid,true);
    const disabled=Swap.planCoordinatedShift(f.snapshot,f.registry,{outputId:"DP-1",maximumWindows:2,
        shiftRangePercent:90,shiftHorizontalOrder:false,shiftVerticalOrder:false,random:()=>.5,now:()=>0,deadlineMs:1000});
    assert.equal(disabled.reason,"ordinary_shift_fallback");
}
console.log("core swap orchestration: tests passed");

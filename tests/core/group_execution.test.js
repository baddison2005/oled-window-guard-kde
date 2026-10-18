"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function source(file){return fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");}
function load(file,imports,names){const c=Object.assign({},imports);vm.runInNewContext(source(file)+"\nthis.api={"+names.join(",")+"};",c);return c.api;}
const Model=load("model.js",{},["applicationExcluded","roundingResizeAllowed","roundingContactAllowed","movementCollision","copyPlain","rectangle","rectangleContains","rectangleOverlaps","createPlan","validateSnapshot","validatePlan"]);
const Registry=load("registry.js",{Model},["createRegistryState","reconcile","snapshotSignature"]);
const Planner=load("planner.js",{Model,Registry},["registryMatches","focusResolved","eligible","rawObstacle","validateGroupCommonFinalPlan"]);
const Groups=load("groups.js",{Model,Registry},["builtinGroups","groupFrames","groupsFromLibrary","matchingZones","allowedShrink","planZoneDrift"]);
const Rotation=require("../../core/group_rotation.js");
const GroupSafety=load("group_safety.js",{Model,Planner,Groups},["validateGroupFinalPlan"]);
const Steps=load("swap_steps.js",{Model,Planner,Groups,GroupSafety},["orderSwapSteps","validateSwapSteps"]);
const GroupExecution=load("group_execution.js",{Model,Planner,Groups,Rotation,GroupSafety,Steps},["planGroupMovement"]);
const rect=(x,y,width,height)=>({x,y,width,height});
const win=(identity,frame,resizable=true)=>({identity,appId:"app",outputIds:["DP-1"],rect:frame,
    visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,
    movable:true,resizable,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]});
function fixture(windows,area=rect(0,0,1000,800)){
    const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"e",name:"d",geometry:area,usableRect:area,scale:1}],
        windows,focusedIdentity:null,topologyRevision:"topo"};
    const registry=Registry.reconcile(snapshot,Registry.createRegistryState("group-exec"),{sessionEpoch:"group-exec",selectedOutputIds:["DP-1"]});
    return {snapshot,registry};
}
{
    const group=Groups.builtinGroups()[1],f=fixture([win("a",rect(100,100,100,100))]);
    const result=GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group,rotateGroup:false,
        shiftRangePercent:20,maximumWindows:1,random:()=>.25,now:()=>0,deadlineMs:1000});
    assert.ok(result.plan && result.steps.length);
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,result.plan,group).valid,true);
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps,{group}).valid,true);
    const forged=JSON.parse(JSON.stringify(result.plan));forged.moves[0].toRect.x=600;
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,forged,group).valid,false);
}
{
    const group=Groups.builtinGroups()[1],f=fixture([win("a",rect(50,100,200,200)),win("b",rect(600,100,200,200))]);
    const result=GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group,rotateGroup:true,
        sizeTolerance:.1,maximumWindows:2,random:()=>.25,now:()=>0,deadlineMs:1000});
    assert.ok(result.plan && result.steps.length);
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,result.plan,group).valid,true);
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps,{group}).valid,true);
}
{
    const area=rect(0,0,400,200),group=Groups.builtinGroups(2)[5];
    const sourceFrame=Groups.groupFrames(group,area)[0],f=fixture([win("a",sourceFrame,true)],area);
    const result=GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group,rotateGroup:true,
        sizeTolerance:.1,maximumWindows:1,random:()=>.75,now:()=>0,deadlineMs:1000});
    assert.ok(result.plan && result.plan.moves.length===1,JSON.stringify(result));
    assert.equal(result.steps[0].kind,"resize");
    assert.equal(result.steps[1].kind,"place");
    assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps,{group}).valid,true);
    const fixed=fixture([win("a",sourceFrame,false)],area);
    assert.equal(GroupSafety.validateGroupFinalPlan(fixed.snapshot,fixed.registry,result.plan,group).valid,false);
}
{
    const library={sourceId:"kde:window-layouts",status:"available",executionReady:true,revision:"revision-1",padding:4,
        layouts:[{id:"left",name:"Left",x:0,y:0,width:.5,height:1,groupId:"pair"},
            {id:"right",name:"Right",x:.5,y:0,width:.5,height:1,groupId:"pair"}],
        groups:[{id:"pair",name:"Pair",layoutIds:["left","right"]}]};
    const group=Groups.groupsFromLibrary(library)[0],area=rect(0,0,1000,800);
    const f=fixture([win("a",rect(100,100,100,100))],area);
    const result=GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group,library,
        rotateGroup:false,shiftRangePercent:20,maximumWindows:1,random:()=>.25,now:()=>0,deadlineMs:1000});
    assert.ok(result.plan);
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,result.plan,group,library).valid,true);
    const forgedPadding=Object.assign({},group,{padding:200});
    assert.equal(GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group:forgedPadding,library,
        rotateGroup:false,shiftRangePercent:20,maximumWindows:1,random:()=>.25,now:()=>0,deadlineMs:1000}).plan,null);
    const forgedPlan=JSON.parse(JSON.stringify(result.plan));forgedPlan.metadata.padding=200;
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,forgedPlan,forgedPadding,library).valid,false);
    const unstable=Object.assign({},library,{status:"unstable",executionReady:false});
    assert.equal(GroupExecution.planGroupMovement(f.snapshot,f.registry,{outputId:"DP-1",group,library:unstable,
        rotateGroup:false,shiftRangePercent:20,maximumWindows:1,random:()=>.25,now:()=>0,deadlineMs:1000}).plan,null);
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,result.plan,group,unstable).valid,false);
    const outside=JSON.parse(JSON.stringify(result.plan));outside.moves[0].toRect.x=500;
    assert.equal(GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,outside,group,library).valid,false);
}
console.log("core group execution: tests passed");

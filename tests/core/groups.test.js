"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const model = fs.readFileSync("core/model.js", "utf8").replace(/^\.pragma library\s*/m, "");
const registry = fs.readFileSync("core/registry.js", "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/m, "");
const groups = fs.readFileSync("core/groups.js", "utf8").replace(/^\.pragma library\s*/m, "").replace(/^\.import .*\n/gm, "");
const context = {};
vm.runInNewContext(model + "\nthis.Model = { applicationExcluded, roundingResizeAllowed,roundingContactAllowed,movementCollision, copyPlain, rectangle, rectangleContains, rectangleOverlaps, createPlan, validateSnapshot };\n" +
    registry + "\nthis.Registry = { createRegistryState, reconcile, snapshotSignature };\n" + groups +
    "\nthis.api = { builtinGroups, groupsFromLibrary, geometryForZone, groupFrames, matchingZones, sourceAllowance, resizeLimit, allowedShrink, planZoneDrift };", context);
const api = context.api;
const strictDeepEqual = assert.deepStrictEqual.bind(assert);
assert.deepEqual = (actual, expected, message) => strictDeepEqual(JSON.parse(JSON.stringify(actual)), JSON.parse(JSON.stringify(expected)), message);
const rect = (x,y,width,height) => ({x,y,width,height});
const output = (id="DP-1", bounds=rect(0,0,1000,800)) => ({id,connector:id,edid:"edid-"+id,name:id,geometry:bounds,usableRect:bounds,scale:1});
const windowRecord = (identity, frame, overrides={}) => Object.assign({
    identity, appId:"org.example.app", outputIds:["DP-1"], rect:frame,
    visible:true, onCurrentDesktop:true, onCurrentActivity:true, focused:false,
    managed:true, modal:false, transient:false, movable:true, resizable:true,
    minimized:false, maximized:false, fullscreen:false, tiled:false, windowType:"normal", skipReasons:[]
}, overrides);
function setup(windows, outputs=[output()]) {
    const snapshot = {outputs,windows,focusedIdentity:null,topologyRevision:"topo-1"};
    const initial = context.Registry.createRegistryState("group-tests");
    const state = context.Registry.reconcile(snapshot, initial, {sessionEpoch:"group-tests",selectedOutputIds:outputs.map(o=>o.id)});
    return {snapshot,state};
}
function deepEqual(a,b) { assert.deepEqual(JSON.parse(JSON.stringify(a)),JSON.parse(JSON.stringify(b))); }

// The six behavior-level macOS groups remain independent from custom catalogs.
{
    const builtins = api.builtinGroups();
    assert.deepEqual(builtins.map(g=>[g.id,g.name]), [
        ["builtin.halves","Halves"],["builtin.horizontal-halves","Horizontal Halves"],
        ["builtin.vertical-halves","Vertical Halves"],["builtin.quarters","Quarters"],
        ["builtin.thirds","Thirds"],["builtin.two-thirds","Two Thirds"]
    ]);
    assert.deepEqual(builtins[0].zones.map(z=>[z.x,z.y,z.width,z.height]), [[0,0,.5,1],[.5,0,.5,1],[0,0,1,.5],[0,.5,1,.5]]);
    assert.deepEqual(builtins[5].zones.map(z=>[z.x,z.width]), [[0,2/3],[1/6,2/3],[1/3,2/3]]);
    assert.ok(builtins.every(g=>g.source === "guard"));
}

// macOS Swift rounding is half away from zero; the KDE JS contract rounds a
// negative half toward positive infinity. Edges are rounded independently.
{
    const zone = {id:"z",name:"Zone",x:.25,y:0,width:.75,height:1,groupId:"g"};
    const area = rect(-1,0,2,100);
    assert.equal(api.geometryForZone(zone,area,0,"guard").x,-1);
    assert.equal(api.geometryForZone(zone,area,0,"window-layouts-kde").x,0);
    const fractional = api.geometryForZone({id:"f",name:"F",x:0,y:0,width:.5,height:1,groupId:null},rect(-20.25,0,101,100),0,"window-layouts-kde");
    assert.equal(fractional.x,-20);
    assert.equal(fractional.width,50);
}

// KDE padding affects internal boundaries only and stays at least one unit wide
// even when the configured padding is much larger than a narrow zone.
{
    const area=rect(0,0,100,40);
    const left={id:"left",name:"left",x:0,y:0,width:.5,height:1,groupId:"g"};
    const right={id:"right",name:"right",x:.5,y:0,width:.5,height:1,groupId:"g"};
    deepEqual(api.geometryForZone(left,area,4,"window-layouts-kde"),rect(0,0,46,40));
    deepEqual(api.geometryForZone(right,area,4,"window-layouts-kde"),rect(54,0,46,40));
    const tiny={id:"tiny",name:"tiny",x:.49,y:0,width:.02,height:1,groupId:"g"};
    const constrained=api.geometryForZone(tiny,area,200,"window-layouts-kde");
    assert.ok(constrained && constrained.width===1 && constrained.height===40);
    const outer=api.geometryForZone({id:"outer",name:"outer",x:0,y:0,width:1,height:1,groupId:null},area,200,"window-layouts-kde");
    deepEqual(outer,area);
    const subpixel={id:"subpixel",name:"subpixel",x:.501,y:0,width:.001,height:1,groupId:"g"};
    deepEqual(api.geometryForZone(subpixel,area,0,"window-layouts-kde"),rect(50,0,1,40));
    assert.equal(api.geometryForZone(subpixel,area,0,"guard"),null);
}

// The KDE adapter's records are accepted only as a complete stable group set;
// IDs are namespaced and source IDs/layout IDs are preserved.
{
    const library={sourceId:"kde:window-layouts",status:"available",revision:"sha256:test",executionReady:true,padding:4,
        layouts:[{id:"layout-1",name:"Left",x:0,y:0,width:.5,height:1,groupId:"group-1"}],
        groups:[{id:"group-1",name:"My group",layoutIds:["layout-1"]}]};
    const imported=api.groupsFromLibrary(library);
    assert.equal(imported.length,1); assert.equal(imported[0].id,"kde:group-1");
    assert.equal(imported[0].sourceId,"group-1"); assert.equal(imported[0].zones[0].id,"layout-1");
    assert.equal(imported[0].revision,"sha256:test");
    assert.deepEqual(api.groupsFromLibrary(Object.assign({},library,{executionReady:false})),[]);
    assert.deepEqual(api.groupsFromLibrary(Object.assign({},library,{status:"unstable"})),[]);
    assert.deepEqual(api.groupsFromLibrary(Object.assign({},library,{groups:[{id:"group-1",name:"Broken",layoutIds:["missing"]}]})),[]);
    assert.deepEqual(api.groupsFromLibrary(Object.assign({},library,{groups:[library.groups[0],library.groups[0]]})),[]);
    assert.deepEqual(api.groupsFromLibrary(Object.assign({},library,{layouts:[library.layouts[0],library.layouts[0]]})),[]);
    assert.equal(api.groupsFromLibrary(Object.assign({},library,{groups:[...library.groups,{id:"empty",name:"Empty",layoutIds:[]}]})).length,1);
}

// Source membership tolerance is capped; ambiguous overlapping alternatives do
// not establish a source zone. This is geometric inference, not saved assignment.
{
    assert.equal(api.sourceAllowance(0),2); assert.equal(api.sourceAllowance(200),32);
    const zones=[rect(0,0,100,100),rect(100,0,100,100)];
    assert.equal(api.matchingZones(rect(101,20,100,50),zones,0).length,1);
    assert.equal(api.matchingZones(rect(2,20,96,50),zones,0).length,1);
    assert.equal(api.matchingZones(rect(33,20,99,50),[rect(0,0,100,100)],0).length,0);
}

// Only explicitly resizable windows receive bounded fitting adjustments.
{
    assert.equal(api.resizeLimit(0),2); assert.equal(api.resizeLimit(4),10); assert.equal(api.resizeLimit(200),32);
    assert.equal(api.allowedShrink(rect(0,0,200,200),rect(0,0,190,194),4,true),true);
    assert.equal(api.allowedShrink(rect(0,0,200,200),rect(0,0,189,194),4,true),false);
    assert.equal(api.allowedShrink(rect(0,0,200,200),rect(0,0,190,194),4,false),false);
    assert.equal(api.allowedShrink(rect(0,0,200,200),rect(0,0,201,194),4,true),true);
}

// Zone drift derives exactly one current zone, preserves size, obeys usable and
// anchor-range bounds, and emits only a dry-run model plan.
{
    const original=windowRecord("a",rect(150,150,100,100));
    const prepared=setup([original]);
    const group={id:"builtin.halves",name:"Halves",padding:0,source:"guard",zones:[
        {id:"l",name:"left",x:0,y:0,width:.5,height:1,groupId:null},
        {id:"r",name:"right",x:.5,y:0,width:.5,height:1,groupId:null}
    ]};
    const result=api.planZoneDrift(prepared.snapshot,prepared.state,{outputId:"DP-1",group,shiftRangePercent:10,maximumWindows:1,
        random:(()=>{const values=[.25,.8];return ()=>values.shift()||.4;})(),now:()=>10,deadlineMs:100});
    assert.equal(result.plan.moves.length,1);
    const move=result.plan.moves[0];
    assert.equal(move.identity,"a"); assert.deepEqual(move.fromRect,original.rect);
    assert.equal(move.toRect.width,original.rect.width); assert.equal(move.toRect.height,original.rect.height);
    assert.ok(context.Model.rectangleContains(prepared.snapshot.outputs[0].usableRect,move.toRect));
    const anchor=prepared.state.windows[0].originFrame;
    assert.ok(Math.abs(move.toRect.x-anchor.x)<=100 && Math.abs(move.toRect.y-anchor.y)<=80);
    assert.equal(result.plan.metadata.mode,"group-drift");
    assert.equal(result.stats.stopped,null);

    const ambiguous=Object.assign({},group,{zones:[group.zones[0],{id:"overlap",name:"overlap",x:.1,y:0,width:.5,height:1,groupId:null}]});
    const skipped=api.planZoneDrift(prepared.snapshot,prepared.state,{outputId:"DP-1",group:ambiguous,shiftRangePercent:10,maximumWindows:1,random:()=>.5,now:()=>10,deadlineMs:100});
    assert.equal(skipped.plan.moves.length,0);
    assert.equal(skipped.trace[0].reason,"ambiguous_zone");

    const nearEdge=setup([windowRecord("near",rect(499,150,100,100))]);
    const noStrictSource=api.planZoneDrift(nearEdge.snapshot,nearEdge.state,{outputId:"DP-1",group,shiftRangePercent:10,maximumWindows:1,
        random:()=>.5,now:()=>10,deadlineMs:100});
    assert.equal(noStrictSource.plan.moves.length,0);
    assert.equal(noStrictSource.trace[0].reason,"no_zone");

    const tampered=JSON.parse(JSON.stringify(prepared.state));
    tampered.windows[0].lastFrame.x += 1;
    assert.throws(()=>api.planZoneDrift(prepared.snapshot,tampered,{outputId:"DP-1",group,shiftRangePercent:10,maximumWindows:1}),
        /registry window mismatch/);
}

console.log("core groups: tests passed");

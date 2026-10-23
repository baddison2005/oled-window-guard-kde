"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function load(file,imports,names) {
    const source=fs.readFileSync("core/"+file,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import .*\n/gm,"");
    const context=Object.assign({},imports);
    vm.runInNewContext(source+"\nthis.api={"+names.join(",")+"};",context);
    return context.api;
}
const Plan=load("dimming_plan.js",{},["overlaps","regions"]);
const Timing=load("dimming_timing.js",{},["createDimmingScopeState","dimmingScopeEvent","createBrightnessRestoreState",
    "restoreDimmingBrightness","isBrightnessRestoreActive","dimmingMustBeBright","updateDimmingDelay","updateDimmingFade",
    "resolveDimmingScope"]);
const Engine=load("dimming_engine.js",{Plan,Timing},["createDimmingState","updateDimming"]);
const copy=value=>JSON.parse(JSON.stringify(value));
const rect=(x,y,width,height)=>({x,y,width,height});
const options=()=>({windows:true,display:true,windowAmount:.3,displayAmount:.4,
    windowDelayMs:1000,displayDelayMs:1000,windowFadeMs:1000,displayFadeMs:1000});
const status=()=>({available:true,rendererProven:true,paused:false,locked:false,inactive:false,sleeping:false,rendererError:false});
const base=()=>({nowMs:0,status:status(),displays:[{id:"A",frame:rect(0,0,500,400),options:options()},
    {id:"B",frame:rect(500,0,500,400),options:options()}],selectedOutputIds:["A","B"],
    windows:[{id:"w",frame:rect(0,0,500,400),dimmable:true}],
    focusedFrame:null,focusedWindowId:null,focusedDisplayId:null});
let state=Engine.createDimmingState();
function step(event){const result=Engine.updateDimming(state,event);state=copy(result.state);return copy(result);}
let event=base();
assert.equal(step(event).bright,true);
event.nowMs=1000;
assert.equal(step(event).bright,true); // fade begins at zero progress
event.nowMs=1500;
assert.ok(step(event).masks.some(m=>m.outputId==="A" && m.amount>0));
event.nowMs=3000;
let result=step(event);
assert.ok(result.masks.some(m=>m.outputId==="A" && m.amount===.4));
assert.ok(result.masks.some(m=>m.outputId==="B" && m.amount===.4));
assert.ok(result.masks.every(m=>m.amount<=.4)); // display mode takes precedence
event.focusedFrame=rect(450,20,100,100);
event.focusedWindowId="focused";event.focusedDisplayId="A";
result=step(event);
assert.ok(result.masks.every(m=>m.amount<=.3)); // spanning focus suppresses both display masks
event.scopeEvent={type:"desktopClick",displayId:"A"};event.nowMs=3100;
result=step(event);
assert.ok(result.masks.every(m=>m.outputId!=="A"));
event.scopeEvent={type:"windowClick"};event.nowMs=3200;
step(event);
event.scopeEvent={type:"activitySwitch",pointerDisplayId:"B"};event.nowMs=3250;
result=step(event);
assert.ok(result.masks.every(m=>m.outputId!=="B")); // stale A focus cannot dim pointer display
event.scopeEvent=null;event.focusedFrame=null;event.focusedWindowId=null;event.focusedDisplayId=null;event.nowMs=3300;
assert.equal(step(event).bright,true); // desktop scope and focus change restart clocks
event.restoreBrightness=true;event.nowMs=4000;
assert.equal(step(event).reason,"restore-brightness");
event.restoreBrightness=true;event.nowMs=8000;
assert.equal(step(event).reason,"restore-brightness");
event.restoreBrightness=false;event.nowMs=8999;
assert.equal(step(event).reason,"restore-hold");
event.nowMs=12999;
assert.equal(step(event).reason,"restore-hold");
event.nowMs=13000;
assert.equal(step(event).bright,true); // fresh eligibility after restore
event.status.rendererProven=false;event.nowMs=15000;
assert.equal(step(event).reason,"unavailable-or-paused");
event.status=status();event.nowMs=15100;
assert.equal(step(event).bright,true);
event.nowMs=17000;step(event);
event.nowMs=17100;result=step(event);
assert.ok(result.masks.length>0);
event.windows[0].frame=rect(40.5,20.25,300,250);event.nowMs=17200;
result=step(event);
assert.ok(result.masks.every(m=>m.frame.x>=0 && m.frame.y>=0));
event.status.locked=true;event.nowMs=17300;
assert.equal(step(event).bright,true);
event.status=status();event.nowMs=17400;
assert.equal(step(event).bright,true); // lock cleared the continuous-eligibility clocks
event.focusedFrame=undefined;event.nowMs=17500;
assert.equal(step(event).reason,"invalid-input");
{
    let focusState=Engine.createDimmingState();
    function run(e) { const r=Engine.updateDimming(focusState,e);focusState=copy(r.state);return copy(r); }
    const sample=base();sample.focusedFrame=rect(0,0,100,100);
    sample.focusedWindowId="first";sample.focusedDisplayId="A";
    run(sample);sample.nowMs=1000;run(sample);sample.nowMs=2500;
    assert.ok(run(sample).masks.some(m=>m.outputId==="B" && m.amount>0));
    sample.focusedWindowId="second";sample.nowMs=2600;
    assert.ok(run(sample).masks.some(m=>m.outputId==="B" && m.amount===.4)); // unrelated display retains timing
}
console.log("core dimming engine: tests passed");
// Desktop clicks can either hold brightness or restart independent delays/fades.
for (const behavior of ['stay-bright','restart-timers']) {
 state=Engine.createDimmingState();event=base();
 event.displays[0].options.desktopClickBehavior=behavior;
 event.scopeEvent={type:'desktopClick',displayId:'A'};
 event.nowMs=0;assert.equal(step(event).masks.filter(m=>m.outputId==='A').length,0);
 event.scopeEvent=null;event.nowMs=1000;step(event);event.nowMs=3000;result=step(event);
 assert.equal(result.masks.some(m=>m.outputId==='A'),behavior==='restart-timers');
 // Repeated clicks reset rather than inheriting elapsed delay/fade progress.
 event.scopeEvent={type:'desktopClick',displayId:'A'};event.nowMs=3100;
 assert.equal(step(event).masks.filter(m=>m.outputId==='A').length,0);
 event.scopeEvent=null;event.nowMs=4000;
 assert.equal(step(event).masks.filter(m=>m.outputId==='A').length,0);
 event.nowMs=4100;step(event);event.nowMs=5200;result=step(event);
 assert.equal(result.masks.some(m=>m.outputId==='A'),behavior==='restart-timers');
}
console.log('PASS desktop-click bright hold and timed redimming, including repeated-click reset');

// Focus/typing/clicks on either display preserve inactive windows' clocks.
state=Engine.createDimmingState();event=base();
event.displays.forEach(d=>{d.options.display=false;d.options.windowFadeMs=0;});
event.windows=[{id:'inactive',frame:rect(0,0,150,150),dimmable:true},
 {id:'active',frame:rect(200,0,150,150),dimmable:true}];
event.focusedWindowId='active';event.focusedDisplayId='A';event.focusedFrame=event.windows[1].frame;
step(event);event.nowMs=1500;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A' && m.frame.x===0));
const clocks=copy(state.outputs.A.windowDelay.since);
event.nowMs=1600;event.scopeEvent={type:'windowKey',displayId:'A'};
assert.ok(step(event).masks.some(m=>m.outputId==='A'));
assert.deepEqual(state.outputs.A.windowDelay.since,clocks);
event.nowMs=1700;event.scopeEvent={type:'desktopClick',displayId:'B'};
assert.ok(step(event).masks.some(m=>m.outputId==='A'));
event.nowMs=1800;event.scopeEvent={type:'windowInteraction',displayId:'B'};
assert.ok(step(event).masks.some(m=>m.outputId==='A'));
// Focus loss starts the previously active window's own inactivity delay.
event.scopeEvent=null;event.focusedWindowId='other';event.focusedDisplayId='B';event.focusedFrame=rect(600,0,100,100);
event.nowMs=1900;result=step(event);
assert.ok(result.masks.some(m=>m.frame.x===0));
assert.ok(result.masks.every(m=>m.frame.x!==200));
event.nowMs=3000;assert.ok(step(event).masks.some(m=>m.frame.x===200));
// Desktop bright holds remain local until an app on that display is used.
event.scopeEvent={type:'desktopClick',displayId:'A'};event.nowMs=3100;assert.equal(step(event).masks.length,0);
event.scopeEvent={type:'windowKey',displayId:'B'};event.nowMs=5000;assert.equal(step(event).masks.length,0);
event.scopeEvent={type:'windowKey',displayId:'A'};event.nowMs=5100;step(event);
event.scopeEvent=null;event.nowMs=6200;assert.ok(step(event).masks.some(m=>m.outputId==='A'));
console.log('PASS independent output/window clocks and local desktop holds');

state=Engine.createDimmingState();event=base();event.displays.forEach(d=>{d.options.display=false;d.options.windowFadeMs=0;});
step(event);event.nowMs=1500;assert.ok(step(event).masks.length);
const beforeMove=copy(state.outputs.A.windowDelay.since);
event.windows[0].frame.x+=10;event.nowMs=1600;
assert.ok(step(event).masks.some(m=>m.frame.x===10));
assert.deepEqual(state.outputs.A.windowDelay.since,beforeMove);
console.log('PASS mask follows geometry without restarting dimming delay');

// Manual dim skips the activation delay and focus exemption only for its
// selected output. Other outputs retain their clocks and restore holds.
state=Engine.createDimmingState();event=base();event.focusedFrame=rect(0,0,500,400);event.focusedWindowId='w';event.focusedDisplayId='A';
event.dimNowOutputIds=['A'];step(event);
event.dimNowOutputIds=[];event.nowMs=500;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A' && m.wholeDisplay && m.amount>0));
assert.ok(result.masks.every(m=>m.outputId!=='B'));
event.scopeEvent={type:'windowKey',displayId:'B'};event.nowMs=600;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A'));
event.scopeEvent={type:'windowKey',displayId:'A'};event.nowMs=650;result=step(event);
assert.ok(result.masks.every(m=>m.outputId!=='A'));
state=Engine.createDimmingState();event=base();event.restoreBrightness=true;step(event);
event.restoreBrightness=false;event.dimNowOutputIds=['A'];event.nowMs=100;step(event);
event.dimNowOutputIds=[];event.nowMs=1200;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A' && m.wholeDisplay));
assert.ok(result.masks.every(m=>m.outputId!=='B'));
event.restoreBrightness=true;event.nowMs=1300;result=step(event);
assert.equal(result.masks.length,0);assert.equal(Object.keys(state.forcedDisplays).length,0);
event.restoreBrightness=false;event.dimNowOutputIds=['A','B'];event.nowMs=1400;step(event);
event.dimNowOutputIds=[];event.nowMs=2500;result=step(event);
assert.ok(['A','B'].every(id=>result.masks.some(m=>m.outputId===id && m.wholeDisplay)));
event.status.paused=true;result=step(event);
assert.equal(result.masks.length,0);assert.equal(Object.keys(state.forcedDisplays).length,0);
// Starting manual monitor dimming must not flash already-dimmed windows bright.
state=Engine.createDimmingState();event=base();event.selectedOutputIds=['A'];
event.focusedFrame=rect(400,0,100,400);event.focusedWindowId='focus';event.focusedDisplayId='A';
step(event);event.nowMs=2500;step(event);event.nowMs=3600;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A' && m.amount===.3));
event.dimNowOutputIds=['A'];event.nowMs=3700;result=step(event);
assert.ok(result.masks.some(m=>m.outputId==='A' && m.amount>=.3));

// A monitor-specific restore retains every other monitor's masks and clocks.
state=Engine.createDimmingState();event=base();event.dimNowOutputIds=['A','B'];step(event);
event.dimNowOutputIds=[];event.nowMs=1500;result=step(event);
const bClock=copy(state.outputs.B),bMasks=copy(result.masks.filter(m=>m.outputId==='B'));
event.restoreOutputIds=['A'];result=step(event);
assert.ok(result.masks.every(m=>m.outputId!=='A'));
assert.deepEqual(state.outputs.B,bClock);
assert.deepEqual(result.masks.filter(m=>m.outputId==='B'),bMasks);
event.restoreOutputIds=[];event.nowMs=6000;result=step(event);
assert.ok(result.masks.every(m=>m.outputId!=='A')); // 5-second minimum hold
assert.ok(result.masks.some(m=>m.outputId==='B'));
event.dimNowOutputIds=['A'];event.nowMs=6100;step(event);
event.dimNowOutputIds=[];event.nowMs=7200;result=step(event);
assert.ok(['A','B'].every(id=>result.masks.some(m=>m.outputId===id)));
event.scopeEvent={type:'shellInteraction',displayId:'A'};result=step(event);
assert.ok(['A','B'].every(id=>result.masks.some(m=>m.outputId===id && m.wholeDisplay)));
event.scopeEvent={type:'desktopClick',displayId:'A'};result=step(event);
assert.ok(!Object.hasOwn(state.forcedDisplays,'A'));
assert.ok(result.masks.some(m=>m.outputId==='B'));
event.restoreBrightness=true;result=step(event);assert.equal(result.masks.length,0);
assert.equal(Object.keys(state.restoreOutputs).length,0);
console.log('PASS scoped restore and shell clicks preserve other displays');

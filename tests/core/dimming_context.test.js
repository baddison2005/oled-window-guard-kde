"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
function load(name,imports) {
 const c={...imports};vm.runInNewContext(fs.readFileSync("core/"+name,"utf8").replace(/^\.pragma library\s*/m,"").replace(/^\.import.*$/gm,"")+"\nthis.api={"+(name==="dimming_options.js"?"resolveDimmingOptions":"context,shellWindow,pointerInteraction")+"};",c);return c.api;
}
const Model={};vm.runInNewContext(fs.readFileSync('core/model.js','utf8').replace(/^\.pragma library\s*/,''),Model);
const Context=load("dimming_context.js",{Model,Options:load("dimming_options.js",{})});
const schema=JSON.parse(fs.readFileSync("schema/settings.schema.json"));
const settings=Object.fromEntries(Object.entries(schema.properties).filter(([,p])=>Object.hasOwn(p,"default")).map(([k,p])=>[k,JSON.parse(JSON.stringify(p.default))]));
const rect={x:0,y:0,width:500,height:300};
const w=id=>({identity:id,appId:"test",rect,outputIds:["A"],visible:true,minimized:false,onCurrentDesktop:true,onCurrentActivity:true,managed:true,modal:false,transient:false,windowType:"normal"});
let snapshot={outputs:[{id:"A",geometry:rect}],windows:[w("back"),w("front")],focusedIdentity:"front"};
settings.dimmingDisplays=["A","disconnected"];settings.dimWindows=true;
let event=Context.context(snapshot,settings,100,{});
assert.deepEqual(JSON.parse(JSON.stringify(event.windows.map(w=>w.id))),["front","back"]);
assert.equal(event.focusedWindowId,"front");assert.equal(event.focusedDisplayId,"A");
assert.equal(event.selectedOutputIds.length,1);
settings.dimmingExcludedApps=["test"];
assert.equal(Context.context(snapshot,settings,100,{}).windows[0].dimmable,false);
snapshot.windows[0].visible=null;assert.throws(()=>Context.context(snapshot,settings,100,{}),/unknown/);
snapshot.windows[0].visible=false;assert.equal(Context.context(snapshot,settings,100,{}).windows.length,1);
snapshot.windows[1].appId="oled-window-guard";assert.equal(Context.context(snapshot,settings,100,{}).windows[0].dimmable,false);
snapshot.windows[1].windowType="desktop";event=Context.context(snapshot,settings,100,{});
assert.equal(event.windows.length,0);assert.equal(event.focusedFrame,null);
console.log("dimming context: tests passed");

snapshot.windows=[{...w('dock'),windowType:'dock'}];snapshot.focusedIdentity=null;
event=Context.context(snapshot,settings,100,{});
assert.equal(event.windows[0].dimmable,false);assert.equal(event.windows[0].displayDimmable,true);

snapshot.windows=[{...w('auto-hide-strip'),windowType:'dock',appId:'cairo-dock'}];
assert.equal(Context.context(snapshot,settings,100,{}).windows.length,0);

// Cairo's full, transparent auto-hide surface must not punch a bright strip
// out of an application mask. Its visible pixels are protected by stacking.
const paint={};vm.runInNewContext(fs.readFileSync('core/dimming_plan.js','utf8').replace(/^\.pragma library\s*/m,'')+'\nthis.paint=regions;',paint);
snapshot.windows=[w('behind-dock'),{...w('cairo-region'),windowType:'dock',appId:'cairo-dock',rect:{x:0,y:250,width:500,height:50}}];
event=Context.context(snapshot,settings,100,{});event.windows[0].dimmable=true;
const masks=paint.paint({display:rect,windows:event.windows,windowAmount:.4});
assert.equal(masks.reduce((sum,m)=>sum+m.frame.width*m.frame.height,0),500*300);
console.log('PASS Cairo transparent dock region does not clip application dimming');
snapshot.windows=[{...w('steam'),appId:'steam'}];snapshot.focusedIdentity=null;
settings.dimmingExcludedApps=['com.valvesoftware.Steam'];
assert.equal(Context.context(snapshot,settings,100,{}, {'com.valvesoftware.Steam':['Steam']}).windows[0].dimmable,false);

// Shell menus preserve prior application focus and never become desktop clicks.
settings.dimmingExcludedApps=[];
const app=w('app'),panel={...w('panel'),appId:'org.kde.plasmashell',windowType:'dock'};
const cairo={...w('cairo'),appId:'cairo-dock',windowType:'dock'};
for(const shell of [panel,cairo,{...panel,windowType:'special'},{...panel,appId:'oled-window-guard',windowType:'special'}]) {
 snapshot.windows=[app,shell];snapshot.focusedIdentity=shell.identity;
 event=Context.context(snapshot,settings,100,{},null,'app');
 assert.equal(event.focusedWindowId,'app');
 assert.equal(Context.pointerInteraction(snapshot,{x:100,y:100},shell.identity).type,'shellInteraction');
 // The real pointer recipient beats a transparent auto-hide dock's rectangle.
 assert.equal(Context.pointerInteraction(snapshot,{x:100,y:100},'app').windowId,'app');
}
snapshot.windows=[app,{...panel,windowType:'desktop'}];snapshot.focusedIdentity='panel';
assert.equal(Context.context(snapshot,settings,100,{},null,'app').focusedWindowId,null);
assert.equal(Context.pointerInteraction(snapshot,{x:100,y:100},'panel').type,'desktopClick');
console.log('PASS shell input/focus stays separate from desktop and app interaction');

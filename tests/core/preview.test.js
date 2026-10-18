"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function load(file,imports) {const c={...imports};vm.createContext(c);vm.runInContext(fs.readFileSync('core/'+file,'utf8').replace(/^\.(pragma|import).*$/mg,''),c);return c;}
const Model=load('model.js',{}),Registry=load('registry.js',{Model}),Preview=load('preview.js',{Model,Registry});
const rect=(x=0)=>({x,y:10,width:100,height:100});
const win=(id,extra={})=>({identity:id,appId:'org.example.Editor',rect:rect(),outputIds:['A'],visible:true,
 onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,
 resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:'normal',skipReasons:[],...extra});
const output={id:'A',geometry:{x:0,y:0,width:1000,height:700},usableRect:{x:0,y:0,width:1000,height:700},scale:1};
const snapshot={outputs:[output],windows:[win('editor'),win('steam',{appId:'steam',rect:rect(200)}),win('large',{maximized:true,rect:rect(400)})],focusedIdentity:'editor',topologyRevision:'topology',capturedAt:0};
const settings={movementDisplays:['A'],excludedApps:['com.valvesoftware.Steam']};
const plan={moves:[{identity:'editor',fromRect:rect(),toRect:rect(110)}]};
const plain=o=>JSON.parse(JSON.stringify(o));
const d=plain(Preview.drawing(snapshot,plan,'A',settings,{'com.valvesoftware.Steam':['Steam']}));
assert.deepEqual(d.windowInfo.map(w=>w.eligibility),['eligible','excluded','unavailable']);
assert.deepEqual(d.windowInfo.map(w=>w.moveNumber),[1,0,0]);
assert.equal(d.windowInfo[1].reason,'excluded_app');assert.equal(d.windowInfo[2].reason,'maximized');
assert(!JSON.stringify(d).includes('identity'));assert(!JSON.stringify(d).includes('focusedIdentity'));
assert.deepEqual(d.destinations,[{from:rect(),to:rect(110)}]);
assert.equal(Preview.drawing(snapshot,null,'missing',settings,{}),null);
const overlap={...snapshot,windows:[win('a'),win('b',{rect:rect(50)})]};
assert(Preview.drawing(overlap,null,'A',settings,{}).windowInfo.every(w=>w.eligibility==='unavailable' && w.reason==='overlapping_window'));
for (const type of ['notification','osd']) {
 const transient={...snapshot,windows:[...snapshot.windows,win('toast',{windowType:type})]};
 assert.equal(Preview.drawing(transient,null,'A',settings,{}).windows.length,3);
}
const many={...snapshot,windows:Array.from({length:80},(_,i)=>win('secret-'+i,{appId:'应用'.repeat(80)}))};
const large=Preview.drawing(many,plan,'A',settings,{});
const payload={livePreview:large,report:{id:'report',moves:1,preview:large},runtime:{restorePreview:large,cancellationTrace:'x'.repeat(6000)},optional:undefined};
const original=JSON.stringify(payload),encoded=Preview.boundedPayload(payload,16384),wire=JSON.parse(encoded);
assert(Buffer.byteLength(encoded,'utf8')<=16384);assert.equal(JSON.stringify(payload),original);
for(const row of [wire.livePreview,wire.report.preview,wire.runtime.restorePreview]) {
 assert.equal(row.windows.length,row.windowInfo.length);assert.equal(row.totalWindows,80);
 assert.deepEqual(row.destinations,plain(large.destinations));
}
assert(wire.livePreview.windows.length<32);assert(!('optional' in wire));
console.log('preview metadata, eligibility, inverse drawing and bounded Unicode payload: PASS');

'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const modules={};
function load(file){if(modules[file])return modules[file];const source=fs.readFileSync('core/'+file,'utf8'),deps={};for(const m of source.matchAll(/^\.import "([^"]+)" as (\w+)$/gm))deps[m[2]]=load(m[1]);const c=Object.assign({},deps);modules[file]=c;vm.runInNewContext(source.replace(/^\.pragma library\s*/m,'').replace(/^\.import .*$/gm,''),c);return c;}
const M=load('model.js'),R=load('registry.js'),Swap=load('swap.js'),Steps=load('swap_steps.js'),E=load('ordered_executor.js'),Write=load('ordered_write.js');
const copy=x=>JSON.parse(JSON.stringify(x)),rect=(x,y,width,height)=>({x,y,width,height});
function reg(snapshot,excludedApps=[]){return R.reconcile(snapshot,R.createRegistryState('groups'),{sessionEpoch:'groups',selectedOutputIds:['lg'],allowIdleFocused:true,excludedApps});}
function fixture(integral=false,left=0,transpose=false){
 const area=rect(left,0,5120,2160),frames=[rect(left,0,1707,2160),rect(left+1707,0,1706,1440),rect(left+3413,integral?720:719.9999999999999,1707,720),rect(left+3413,1440,1707,720)];
 const snapshot={outputs:[{id:'lg',connector:'lg',geometry:area,usableRect:copy(area),scale:1}],windows:frames.map((r,i)=>({identity:'w'+i,appId:i<2?'single'+i:'dolphin',rect:r,outputIds:['lg'],integerPosition:integral,resizable:true,minimumSize:{width:100,height:100},visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:'normal',skipReasons:[]})),focusedIdentity:null,topologyRevision:'groups'};
 if(transpose){const flip=r=>rect(r.y,r.x,r.height,r.width);snapshot.outputs.forEach(o=>{o.geometry=flip(o.geometry);o.usableRect=flip(o.usableRect);});snapshot.windows.forEach(w=>w.rect=flip(w.rect));}
 return {snapshot,registry:reg(snapshot)};
}
function options(seed=1,extra={}){return Object.assign({outputId:'lg',maximumWindows:8,sizeTolerance:.1,allowTransientOverlap:true,keepSimilarWindowsTogether:true,random:M.seededRandom(seed),now:()=>0,deadlineMs:1000,maxEvaluations:50000},extra);}
function safety(now){return{observedAt:now,companion:'connected',locked:false,suspended:false,topologyStable:true,topologyGeneration:1,topologyOutputIds:['lg'],allOutputsQuiet:true,heldButtons:false,outputs:[{id:'lg',known:true,quiet:true,buttonsDown:false}],capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},quietSince:now-5000,quietMs:1000};}
function execute(f,result,failAt=-1){let state=E.createOrderedState(),live=copy(f.snapshot),now=10000,writes=0;
 function send(e){const r=E.transitionOrdered(state,e);state=r.state;return r.commands;}
 send({type:'resume'});
 function drain(commands){for(let k=0;commands.length&&k<100;k++){
  const command=commands[0];now+=50;
  if(command.type==='requestSnapshot'){commands=send({type:'snapshotReady',generation:state.generation,commandId:command.id,now,snapshot:copy(live),currentSettingsRevision:'s1',safety:safety(now)});continue;}
  assert.equal(command.type,'writeGeometry');
  const gate=Write.validate(command,state,{now,enabled:true,paused:false,nativeTokenCurrent:true,settingsRevision:'s1',currentSettingsRevision:'s1',snapshot:copy(live),registry:reg(live),safety:safety(now)});
  assert.equal(gate.valid,true,JSON.stringify(gate));
  if(!command.rollback&&writes++===failAt){commands=send({type:'writeResult',generation:state.generation,commandId:command.id,accepted:false,now});continue;}
  live.windows.find(w=>w.identity===command.identity).rect=copy(command.destination);
  const observations=[20,40].map((delta,i)=>({id:command.id+':'+i,now:now+delta,snapshot:copy(live),currentSettingsRevision:'s1',safety:safety(now+delta)}));
  commands=send({type:'writeResult',generation:state.generation,commandId:command.id,accepted:true,observations});
 }assert.equal(commands.length,0);}
 function begin(plan,steps){drain(send({type:'begin',generation:state.generation,warningCleared:true,now,snapshot:copy(live),registry:reg(live),plan,steps,settingsRevision:'s1',currentSettingsRevision:'s1',safety:safety(now)}));}
 begin(result.plan,result.steps);
 if(failAt<0){assert.equal(state.lastResult.status,'committed');assert.ok(state.undoRecord);
  const command=send({type:'queueRestore',generation:state.generation,now:now+50,snapshot:copy(live),registry:reg(live),settingsRevision:'s1',sessionEpoch:'groups',safety:safety(now+50)})[0];assert.ok(command,JSON.stringify({result:state.lastResult,plan:result.plan}));assert.equal(command.type,'restoreQueued');begin(command.plan,command.steps);assert.equal(state.lastResult.status,'committed');
 }else assert.equal(state.lastResult.status,'failed-rolled-back');
 assert.deepEqual(live.windows.map(w=>w.rect),f.snapshot.windows.map(w=>w.rect));
}
let cases=0;
for(const integral of [false,true])for(const left of [0,-5120])for(const transpose of [false,true])for(let seed=1;seed<=8;seed++){
 const f=fixture(integral,left,transpose),result=Swap.planSwap(f.snapshot,f.registry,options(seed));assert.ok(result.plan,JSON.stringify(result));assert.equal(result.plan.moves.length,4);
 const moves=result.plan.moves,first=moves.find(m=>m.identity==='w2').toRect,second=moves.find(m=>m.identity==='w3').toRect;
 if(transpose){assert.equal(first.y,second.y);assert.equal(second.x-first.x,720);}else{assert.equal(first.x,second.x);assert.equal(second.y-first.y,720);}
 assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps).valid,true);
 execute(f,result);cases++;
}
{
 const f=fixture();f.snapshot.windows.forEach(w=>w.resizable=false);f.registry=reg(f.snapshot);const r=Swap.planSwap(f.snapshot,f.registry,options());assert.ok(r.plan);assert.equal(r.plan.moves.length,3);assert.ok(r.plan.moves.every(m=>m.fromRect.width===m.toRect.width&&m.fromRect.height===m.toRect.height));execute(f,r);cases++;
}
{
 const f=fixture();f.snapshot.windows[1].resizable=false;f.registry=reg(f.snapshot);const r=Swap.planSwap(f.snapshot,f.registry,options());assert.ok(r.plan);assert.equal(r.plan.moves.length,4);const writer=r.plan.moves.find(m=>m.identity==='w1');assert.equal(writer.fromRect.width,writer.toRect.width);execute(f,r);cases++;
}
for(const maximumWindows of [2,3]){
 const f=fixture(),r=Swap.planSwap(f.snapshot,f.registry,options(2,{maximumWindows}));assert.ok(r.plan);assert.equal(r.plan.moves.length,maximumWindows);
 const moving=new Set(r.plan.moves.map(m=>m.identity));assert.equal(moving.has('w2'),moving.has('w3'));execute(f,r);cases++;
}
{
 const f=fixture();f.snapshot.windows[2].appId='excluded';f.registry=reg(f.snapshot,['excluded']);const r=Swap.planSwap(f.snapshot,f.registry,options());if(r.plan)assert.ok(r.plan.moves.every(m=>!['w2','w3'].includes(m.identity)));cases++;
}
{
 const f=fixture(),r=Swap.planSwap(f.snapshot,f.registry,options());assert.ok(r.plan.moves.some(m=>m.permitsRoundingResize));execute(f,r,2);execute(f,r,r.steps.length-1);
 assert.equal(Swap.planSwap(f.snapshot,f.registry,options(1,{shouldCancel:()=>true})).plan,null);
 assert.equal(Swap.planSwap(f.snapshot,f.registry,options(1,{deadlineMs:0})).plan,null);cases+=4;
}
// A candidate cap cannot cut twelve members out of a thirteen-window block.
{
 const f=fixture();f.snapshot.outputs[0].geometry=f.snapshot.outputs[0].usableRect=rect(0,0,5120,2160);const proto=f.snapshot.windows[0];f.snapshot.windows=Array.from({length:13},(_,i)=>Object.assign(copy(proto),{identity:'t'+i,rect:rect(i*100,0,100,100)}));f.registry=reg(f.snapshot);
 const r=Swap.planSwap(f.snapshot,f.registry,options(2));assert.equal(r.plan,null);assert.ok(r.trace.some(t=>t.reason==='unselected_neighbour'));cases++;
}
console.log('PASS '+cases+' whole-group column/row assignments, size fitting, writer, Restore, rollback and cap cases');

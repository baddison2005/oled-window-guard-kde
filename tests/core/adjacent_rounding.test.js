'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const cache={};
function load(file){if(cache[file])return cache[file];const source=fs.readFileSync('core/'+file,'utf8'),deps={};for(const m of source.matchAll(/^\.import "([^"]+)" as (\w+)$/gm))deps[m[2]]=load(m[1]);const c=Object.assign({},deps);cache[file]=c;vm.runInNewContext(source.replace(/^\.pragma library\s*/m,'').replace(/^\.import .*$/gm,''),c);return c;}
const M=load('model.js'),R=load('registry.js'),P=load('planner.js'),Swap=load('swap.js'),Steps=load('swap_steps.js'),G=load('groups.js'),GE=load('group_execution.js'),E=load('ordered_executor.js');
const copy=x=>JSON.parse(JSON.stringify(x)),rect=(x,y,width,height)=>({x,y,width,height});
function fixture(integral=true,left=0,room=false){const area=rect(left,0,1201,room?650:200),widths=[400,401,400];let x=left;
 const windows=widths.map((width,i)=>{const w={identity:'w'+i,appId:'app',rect:rect(x,0,width,200),outputIds:['panel'],integerPosition:integral,resizable:true,visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:'normal',skipReasons:[]};x+=width;return w;});
 const snapshot={outputs:[{id:'panel',connector:'panel',geometry:area,usableRect:area,scale:1.5}],windows,focusedIdentity:null,topologyRevision:'adjacent'};
 const registry=reg(snapshot);return{snapshot,registry};}
function reg(snapshot){return R.reconcile(snapshot,R.createRegistryState('adjacent'),{sessionEpoch:'adjacent',selectedOutputIds:['panel']});}
function options(extra={}){return Object.assign({outputId:'panel',maximumWindows:3,sizeTolerance:.1,allowTransientOverlap:true,keepSimilarWindowsTogether:false,random:M.seededRandom(7),now:()=>0,deadlineMs:1000,maxEvaluations:50000},extra);}
let cases=0;
for(const integral of [true,false])for(const left of [0,-1201])for(let seed=1;seed<=12;seed++){
 const f=fixture(integral,left),r=Swap.planSwap(f.snapshot,f.registry,options({random:M.seededRandom(seed)}));
 assert.ok(r.plan,JSON.stringify(r));assert.equal(r.plan.moves.length,3);assert.ok(r.steps.some(s=>s.kind==='resize'));
 for(const m of r.plan.moves){assert.ok(M.rectangleContains(f.snapshot.outputs[0].usableRect,m.toRect));assert.ok(Math.abs(m.fromRect.width-m.toRect.width)<=2);if(m.toRect.width!==m.fromRect.width)assert.equal(m.permitsRoundingResize,true);}
 assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,r.plan,r.steps).valid,true);
 const bad=copy(r.plan),shrunk=bad.moves.find(m=>m.permitsRoundingResize);delete shrunk.permitsRoundingResize;
 assert.equal(P.validateSwapFinalPlan(f.snapshot,f.registry,bad).valid,false);
 shrunk.permitsRoundingResize=true;shrunk.toRect.width=shrunk.fromRect.width-3;
 assert.equal(P.validateSwapFinalPlan(f.snapshot,f.registry,bad).valid,false);
 cases++;
}
{
 const f=fixture();f.snapshot.windows[1].resizable=false;f.registry=reg(f.snapshot);
 const r=Swap.planSwap(f.snapshot,f.registry,options());assert.ok(r.plan);
 const m=r.plan.moves.find(m=>m.identity==='w1');if(m)assert.equal(m.toRect.width,m.fromRect.width);
 const limited=fixture();limited.snapshot.windows[1].minimumSize={width:401,height:200};limited.registry=reg(limited.snapshot);const preserving=Swap.planSwap(limited.snapshot,limited.registry,options());assert.ok(preserving.plan);assert.equal(preserving.plan.moves.find(m=>m.identity==='w1').toRect.width,401);
 const overlapping=fixture();overlapping.snapshot.windows[1].rect.x-=1;overlapping.registry=reg(overlapping.snapshot);
 assert.equal(overlapping.registry.windows.find(w=>w.identity==='w1').candidate,true);
 overlapping.snapshot.windows[1].rect.x-=2;overlapping.registry=reg(overlapping.snapshot);assert.equal(overlapping.registry.windows.find(w=>w.identity==='w1').candidate,false);
 const ghosted=fixture(),ghost=copy(ghosted.snapshot.windows[0]);ghost.identity='minimized-ghost';ghost.rect=copy(ghosted.snapshot.outputs[0].usableRect);ghost.minimized=true;ghosted.snapshot.windows.push(ghost);ghosted.registry=reg(ghosted.snapshot);
 assert.equal(P.rawObstacle(ghost),false);assert.ok(ghosted.registry.windows.filter(w=>w.identity!=='minimized-ghost').every(w=>w.candidate));
 assert.ok(Swap.planSwap(ghosted.snapshot,ghosted.registry,options()).plan);
 const signature=R.snapshotSignature(ghosted.snapshot);ghost.minimized=false;
 assert.notEqual(R.snapshotSignature(ghosted.snapshot),signature);assert.equal(P.rawObstacle(ghost),true);
 const strict=fixture(true,0,true),r2=Swap.planSwap(strict.snapshot,strict.registry,options({allowTransientOverlap:false}));
 assert.ok(r2.plan);assert.ok(r2.steps.some(s=>s.kind==='stage'));assert.equal(Steps.validateSwapSteps(strict.snapshot,strict.registry,r2.plan,r2.steps).valid,true);
 cases+=5;
}
// Real provider decimals and independently rounded edges: all three windows
// still rotate, with a bounded resize only on a wider-to-narrower assignment.
for(const source of ['guard','window-layouts-kde'])for(const integral of [false,true]){
 const f=fixture(integral),library={sourceId:'kde:window-layouts',status:'available',executionReady:true,revision:'layout-1',padding:0,layouts:[0,1,2].map(i=>({id:'z'+i,name:'Third',x:i/3,y:0,width:1/3,height:1,groupId:'thirds'})),groups:[{id:'thirds',name:'Thirds',layoutIds:['z0','z1','z2']}]};
 const group=source==='guard'?G.builtinGroups(0)[4]:G.groupsFromLibrary(library)[0];
 // Use the actual provider slots, including its uneven widths.
 const slots=G.groupFrames(group,f.snapshot.outputs[0].usableRect);
 f.snapshot.windows.forEach((w,i)=>w.rect=copy(slots[i]));f.registry=reg(f.snapshot);
 const r=GE.planGroupMovement(f.snapshot,f.registry,options({group,library:source==='guard'?undefined:library,rotateGroup:true}));
 assert.ok(r.plan,JSON.stringify(r));assert.equal(r.plan.moves.length,3);assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,r.plan,r.steps,{group,library}).valid,true);cases++;
}
function safety(now){return{observedAt:now,companion:'connected',locked:false,suspended:false,topologyStable:true,topologyGeneration:1,topologyOutputIds:['panel'],allOutputsQuiet:true,heldButtons:false,outputs:[{id:'panel',known:true,quiet:true,buttonsDown:false}],capabilities:{keyboard:true,pointer:true,scroll:true,buttonState:true},quietSince:now-5000,quietMs:1000};}
function execute(f,plan,steps,failAt=-1){let state=E.createOrderedState(),live=copy(f.snapshot),now=10000,writes=0;
 const send=e=>{const r=E.transitionOrdered(state,e);state=r.state;return r.commands;};send({type:'resume'});
 function drain(commands){for(let i=0;commands.length&&i<80;i++){const command=commands[0];now+=50;
  if(command.type==='requestSnapshot')commands=send({type:'snapshotReady',generation:state.generation,commandId:command.id,now,snapshot:copy(live),currentSettingsRevision:'s1',safety:safety(now)});
  else{assert.equal(command.type,'writeGeometry');if(!command.rollback&&writes++===failAt){commands=send({type:'writeResult',generation:state.generation,commandId:command.id,accepted:false,now});continue;}
   live.windows.find(w=>w.identity===command.identity).rect=copy(command.destination);
   const observations=[20,40].map((delta,k)=>({id:command.id+':'+k,now:now+delta,snapshot:copy(live),currentSettingsRevision:'s1',safety:safety(now+delta)}));
   commands=send({type:'writeResult',generation:state.generation,commandId:command.id,accepted:true,observations});
  }
 }assert.equal(commands.length,0);}
 function begin(plan,steps){drain(send({type:'begin',generation:state.generation,warningCleared:true,now,snapshot:copy(live),registry:reg(live),plan,steps,settingsRevision:'s1',currentSettingsRevision:'s1',safety:safety(now)}));}
 begin(plan,steps);
 if(failAt<0){assert.equal(state.lastResult.status,'committed');assert.ok(state.undoRecord);
  const command=send({type:'queueRestore',generation:state.generation,now:now+50,snapshot:copy(live),registry:reg(live),settingsRevision:'s1',sessionEpoch:'adjacent',safety:safety(now+50)})[0];
  assert.equal(command.type,'restoreQueued');assert.equal(command.plan.moves.some(m=>m.permitsRoundingResize),plan.moves.some(m=>m.permitsRoundingResize));
  begin(command.plan,command.steps);assert.equal(state.lastResult.status,'committed');
 }else assert.equal(state.lastResult.status,'failed-rolled-back');
 assert.deepEqual(live.windows.map(w=>w.rect),f.snapshot.windows.map(w=>w.rect));
}
{const f=fixture(),r=Swap.planSwap(f.snapshot,f.registry,options());execute(f,r.plan,r.steps);execute(f,r.plan,r.steps,2);cases+=2;}
// Exact LG layout reported by the compositor: two stacked middle thirds,
// a left two-thirds-height Writer, and full-height Chrome on the right.
for (const together of [false,true]) for(let seed=1;seed<=16;seed++) {
 const f=fixture(false);f.snapshot.outputs[0].geometry=f.snapshot.outputs[0].usableRect=rect(0,0,5120,2160);
 const proto=f.snapshot.windows[0];f.snapshot.windows=[rect(1706.0000000000014,0,1707,720),rect(1705.9999999999995,720,1707,720),rect(3413,0,1707,2160),rect(0,0,1707,1440)].map((r,i)=>Object.assign(copy(proto),{identity:'w'+i,rect:r,minimumSize:{width:200,height:100}}));f.registry=reg(f.snapshot);
 assert.ok(f.registry.windows.every(w=>w.candidate));
 const r=Swap.planSwap(f.snapshot,f.registry,options({maximumWindows:8,keepSimilarWindowsTogether:together,random:M.seededRandom(seed)}));
 assert.ok(r.plan,JSON.stringify({together,seed,r}));assert.ok(r.plan.moves.length>=2);
 execute(f,r.plan,r.steps);
 const shift=Swap.planCoordinatedShift(f.snapshot,f.registry,options({maximumWindows:8,shiftRangePercent:100,shiftHorizontalOrder:true,shiftVerticalOrder:true,random:M.seededRandom(seed)}));
 assert.ok(shift.plan.moves.length>0,JSON.stringify({seed,shift}));
 const steps=shift.steps.length?shift.steps:Steps.orderOrdinarySteps(f.snapshot,f.registry,shift.plan);
 execute(f,shift.plan,steps);cases+=2;
}
// The allowance cannot conceal new pairs, deeper borders, other axes, or dialogs.
{
 const f=fixture();f.snapshot.windows[1].rect.x-=1;f.registry=reg(f.snapshot);
 const [a,b,c]=f.snapshot.windows;
 assert.equal(M.movementCollision(f.snapshot,null,a.identity,a.rect,b.identity,b.rect),false);
 assert.equal(M.movementCollision(f.snapshot,null,a.identity,a.rect,b.identity,Object.assign({},b.rect,{x:b.rect.x-1.1})),true);
 assert.equal(M.movementCollision(f.snapshot,null,a.identity,Object.assign({},a.rect,{x:402}),c.identity,c.rect),true);
 b.windowType='dialog';assert.equal(M.movementCollision(f.snapshot,null,a.identity,a.rect,b.identity,b.rect),true);
 cases++;
}
// An optional larger search must not erase an already validated arrangement.
for(const cancel of [false,true]) {
 const f=fixture(),originalOrder=Steps.orderSwapSteps;let ended=false;
 Steps.orderSwapSteps=function(...args){const result=originalOrder(...args);if(result.steps.length)ended=true;return result;};
 try {
  const r=Swap.planSwap(f.snapshot,f.registry,options({now:()=>ended?2000:0,deadlineMs:1000,shouldCancel:()=>cancel&&ended}));
  if(cancel)assert.equal(r.plan,null);else {assert.ok(r.plan);assert.equal(Steps.validateSwapSteps(f.snapshot,f.registry,r.plan,r.steps).valid,true);}
 }finally{Steps.orderSwapSteps=originalOrder;}
 cases++;
}
console.log('PASS '+cases+' adjacent rounding, fixed-client, staging, group, inverse-size and rollback cases');

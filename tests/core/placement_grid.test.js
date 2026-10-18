"use strict";
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const cache={};
function load(file){
 if(cache[file])return cache[file];
 const source=fs.readFileSync('core/'+file,'utf8'),deps={};
 for(const match of source.matchAll(/^\.import "([^"]+)" as (\w+)$/gm))deps[match[2]]=load(match[1]);
 const context=Object.assign({},deps);cache[file]=context;
 vm.runInNewContext(source.replace(/^\.pragma library\s*/m,'').replace(/^\.import .*$/gm,''),context);return context;
}
const M=load('model.js'),R=load('registry.js'),P=load('planner.js'),S=load('swap.js'),Steps=load('swap_steps.js'),G=load('groups.js'),GE=load('group_execution.js');
const frame=(x,y,width=160,height=100)=>({x,y,width,height});
const window=(id,x,y,integral)=>({identity:id,appId:'app',rect:frame(x,y),outputIds:['DP-1'],integerPosition:integral,visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:'normal',skipReasons:[]});
let cases=0,waylandFractional=false;
for(const integral of [false,true])for(const left of [0,-1000.5]){
 const area=frame(left,0,900,650),offset=integral?Math.ceil(left):left+.25;
 const snapshot={outputs:[{id:'DP-1',connector:'DP-1',geometry:area,usableRect:area,scale:1.5}],windows:[window('a',offset+40,40,integral),window('b',offset+500,300,integral)],focusedIdentity:null,topologyRevision:'grid'};
 const registry=R.reconcile(snapshot,R.createRegistryState('grid'),{sessionEpoch:'grid',selectedOutputIds:['DP-1']});
 const options={outputId:'DP-1',maximumWindows:2,shiftRangePercent:100,shiftHorizontalOrder:true,shiftVerticalOrder:true,allowTransientOverlap:true,keepSimilarWindowsTogether:false,sizeTolerance:.1,now:()=>0,deadlineMs:100,maxEvaluations:50000,random:()=>.37};
 const ordinary=P.planOrdinaryShift(snapshot,registry,options);
 const results=[{plan:ordinary.plan,steps:Steps.orderOrdinarySteps(snapshot,registry,ordinary.plan)},S.planSwap(snapshot,registry,options),S.planCoordinatedShift(snapshot,registry,options)];
 for(const rotateGroup of [false,true])results.push(GE.planGroupMovement(snapshot,registry,{...options,group:G.builtinGroups(0)[0],rotateGroup}));
 for(const result of results){
  assert.ok(result.plan && result.plan.moves.length,JSON.stringify(result));
  for(const move of result.plan.moves){
   if(integral){assert.equal(move.toRect.x,Math.round(move.toRect.x));assert.equal(move.toRect.y,Math.round(move.toRect.y));}
   else waylandFractional=waylandFractional||move.toRect.x!==Math.round(move.toRect.x)||move.toRect.y!==Math.round(move.toRect.y);
   assert.ok(M.rectangleContains(area,move.toRect));
  }
  for(const step of result.steps)if(integral){assert.equal(step.toRect.x,Math.round(step.toRect.x));assert.equal(step.toRect.y,Math.round(step.toRect.y));}
  cases++;
 }
 if(integral){
  const bad=JSON.parse(JSON.stringify(ordinary.plan));bad.moves[0].toRect.x+=.25;
  assert.equal(P.validateFinalPlan(snapshot,registry,bad).valid,false);
  const reclassified=JSON.parse(JSON.stringify(snapshot));reclassified.windows[0].integerPosition=false;
  assert.notEqual(R.snapshotSignature(snapshot),R.snapshotSignature(reclassified));
 }
}
assert.ok(waylandFractional,'Wayland retains fractional destinations');
console.log('PASS '+cases+' XWayland/Wayland placement cases across drift, swap, coordination and groups');

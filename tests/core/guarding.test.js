'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const c={};vm.runInNewContext(fs.readFileSync('core/guarding.js','utf8').replace(/^\.pragma library\s*/m,''),c);
const sample=changes=>Object.assign({epoch:'local',topologyGeneration:1,lockState:'unlocked',sleepState:'awake',sessionActivity:'active'},changes);
const start=(scope='all')=>c.start(c.createGuardingState(),scope,true,true);
assert.equal(c.createGuardingState().movement,false);assert.equal(c.createGuardingState().brightness,false);
assert.equal(start('movement').brightness,false);assert.equal(start('brightness').movement,false);
for(const scope of ['all','movement','brightness']){
 const before=start(scope),locked=c.boundary(before,'native-lock',sample({lockState:'locking'}),true);
 assert.equal(locked.movement,false);assert.equal(locked.brightness,false);assert.ok(locked.unlock);
 const waiting=c.boundary(locked,'native-lock',sample({lockState:'locked'}),true);
 const unlocked=c.boundary(waiting,'native-lock',sample(),true);
 assert.ok(c.afterUnlock(unlocked,sample(),false,false,true).unlock);
 const result=c.afterUnlock(unlocked,sample(),true,true,true);
 assert.equal(result.movement,before.movement);assert.equal(result.brightness,before.brightness);assert.equal(result.unlock,null);
 assert.equal(c.afterUnlock(unlocked,sample({lockState:'locked'}),true,true,true).movement,false);
 assert.equal(c.afterUnlock(unlocked,sample({sleepState:'sleeping'}),true,true,true).brightness,false);
 for(const reason of ['native-topology','native-unavailable','companion-unavailable','native-other-input','native-login-service'])
  assert.equal(c.boundary(locked,reason,sample(),true).unlock,null);
 assert.ok(c.boundary(locked,'native-session',sample({topologyGeneration:2}),true).unlock);
 assert.equal(c.afterUnlock(locked,sample({epoch:'replacement'}),true,true,true).unlock,null);
 assert.equal(c.afterUnlock(locked,sample(),true,true,false).unlock,null);
 assert.equal(c.boundary(before,'native-lock',sample({lockState:'locked'}),false).unlock,null);
}
let locked=c.boundary(start(),'native-lock',sample({lockState:'locked'}),true);
locked=c.pause(locked,'movement');const resumed=c.afterUnlock(locked,sample(),true,true,true);assert.equal(resumed.movement,false);assert.equal(resumed.brightness,true);
assert.equal(c.pause(locked,'all').unlock,null);
assert.equal(c.boundary(c.createGuardingState(),'native-lock',sample({lockState:'locked'}),true).unlock,null);
console.log('PASS independent guards, opt-in unlock, partial pause, epoch/topology and failure revocation');

const output=(id,x=0)=>({id,connector:id,geometry:{x,y:0,width:900,height:650},scale:1});
const a=output('LG'),b=output('other',900),both=[a,b];
let running=c.start(c.createGuardingState(),'all',true,true,[a],[a]);
let retained=c.topology(running,[a]);
assert.equal(retained.movement,true);assert.equal(retained.brightness,true);
assert.equal(c.topology(running,[b]).movement,false);
running=c.start(c.createGuardingState(),'all',true,true,both,both);
retained=c.topology(running,[a]);assert.equal(retained.movement,true);assert.equal(retained.movementOutputs.length,1);
retained=c.topology(retained,both);assert.equal(retained.movementOutputs.length,1); // reconnect never silently arms
retained=c.topology(retained,[output('LG',100)]);assert.equal(retained.movement,true); // replan after origin changes
running=c.start(c.createGuardingState(),'all',true,true,[a],[b]);
retained=c.topology(running,[a]);assert.equal(retained.movement,true);assert.equal(retained.brightness,false);
assert.ok(c.topology(c.boundary(running,'native-lock',sample({lockState:'locked'}),true),both).unlock);
console.log('PASS unrelated disconnect retention, scoped disconnect, reconnect, origin changes and locked topology');

for(const [reason,change] of [['native-sleep',{sleepState:'sleeping'}],['native-session',{sessionActivity:'inactive'}],['native-lock',{lockState:'locking'}]]) {
 let state=c.start(c.createGuardingState(),'all',true,true,both,both);
 state=c.boundary(state,reason,sample(change),true);assert.ok(state.unlock);
 state=c.topology(state,[]);state=c.topology(state,[a]);assert.ok(state.unlock);
 state=c.boundary(state,'native-wake',sample({topologyGeneration:9}),true);
 state=c.afterUnlock(state,sample({topologyGeneration:10}),true,true,true,{movement:[a],brightness:[a]});
 assert.equal(state.movement,true);assert.equal(state.brightness,true);assert.ok(state.unlock);
 assert.equal(state.unlock.movementOutputs[0].id,b.id);
 state=c.afterUnlock(state,sample({topologyGeneration:11}),true,true,true,{movement:[b],brightness:[b]});
 assert.equal(state.unlock,null);assert.equal(state.movementOutputs.length,2);
}
let waiting=c.boundary(c.start(c.createGuardingState(),'all',true,true,both,both),'native-sleep',sample({sleepState:'sleeping'}),true);
waiting=c.pause(waiting,'movement');waiting=c.afterUnlock(waiting,sample(),true,true,true,{movement:both,brightness:both});
assert.equal(waiting.movement,false);assert.equal(waiting.brightness,true);
assert.equal(c.afterUnlock(c.createGuardingState(),sample(),true,true,true).movement,false);
console.log('PASS lock/sleep/inactive return, transient missing displays, staggered recovery, manual pause and cold startup');

let empty=c.boundary(start(),'native-lock',sample({lockState:'locked'}),true);
empty=c.boundary(empty,'native-invalid',sample({lockState:'locked',otherInputObserved:false,outputIdentityComplete:false,outputs:[]}),true);
assert.ok(empty.unlock && empty.unlock.awaitingOutputs);
empty=c.boundary(empty,'native-recovered',sample({otherInputObserved:false}),true);assert.ok(empty.unlock);
assert.equal(c.boundary(empty,'native-invalid',sample({otherInputObserved:false,outputs:[{}]}),true).unlock,null);
assert.equal(c.boundary(empty,'native-other-input',sample({otherInputObserved:true}),true).unlock,null);
console.log('PASS empty display set during known interruption preserves intent without accepting arbitrary invalid input');

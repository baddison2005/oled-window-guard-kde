'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const clock={};vm.runInNewContext(fs.readFileSync('core/warning_clock.js','utf8').replace(/^\.pragma library\s*/,''),clock);
for(const invalid of [NaN,Infinity,-1,undefined])assert.equal(clock.start(invalid,8000),null);
for(const duration of [0,2999,60001,NaN])assert.equal(clock.start(100,duration),null);
let state=clock.start(100,8000),previous=8;
// Grace transitions, repeated samples and stale samples cannot reverse time.
for(const elapsed of [0,1000,2000,3000,4000,3000,4050,6000,5000,8000,7000,9000]) {
 state=clock.sample(state,100+elapsed);
 const seconds=Math.ceil(state.remainingMs/1000);
 assert.ok(seconds<=previous);previous=seconds;
}
assert.equal(previous,0);assert.equal(state.deadline,8100);
assert.equal(clock.sample(state,NaN),null);
assert.equal(clock.sample(null,100),null);
console.log('PASS warning clock: monotonic countdown, invalid samples, exact expiry');

"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
const N={};vm.runInNewContext(fs.readFileSync("core/native_observation.js","utf8").replace(/^\.pragma library\s*/m,""),N);
function sample(changes={}) {
    return Object.assign({schema:2,epoch:"effect-1",sequence:4,topologyGeneration:0,sessionGeneration:0,
        focusGeneration:0,lockState:"unknown",sleepState:"unknown",sessionActivity:"unknown",
        pointer:1,button:2,axis:1,keyboard:0,unknownFocusKeyboard:0,focusKnown:false,
        ageMs:10,buttonKnown:true,buttonsDown:false,outputIdentityComplete:true,
        outputs:[{id:"DP-1",events:3},{id:"HDMI-A-1",events:1}]},changes);
}
let cases=0;
const initial=N.consumeObservation(N.createObservationState(),sample(),100).state;
assert.equal(initial.available,true);
assert.equal(N.consumeObservation(initial,sample({ageMs:110}),200).cancel,null);cases++;
for (const [change,reason] of [
    [{sequence:5,pointer:2},"native-input"],
    [{sequence:5,button:3,buttonsDown:true},"native-input"],
    [{sequence:5,axis:2},"native-input"],
    [{sequence:5,keyboard:1,unknownFocusKeyboard:1},"native-input"],
    [{focusGeneration:1},"native-focus"],
    [{epoch:"effect-2"},"native-replaced"],
    [{sessionGeneration:2,lockState:"unlocked",buttonKnown:false},"native-session-or-topology"],
    [{sessionGeneration:1,lockState:"locked",buttonKnown:false},"native-session-or-topology"],
    [{sessionGeneration:1,sleepState:"sleeping",buttonKnown:false},"native-session-or-topology"],
    [{sessionGeneration:1,sessionActivity:"inactive",buttonKnown:false},"native-session-or-topology"],
    [{topologyGeneration:1,outputs:[{id:"DP-1",events:0}],buttonKnown:false},"native-session-or-topology"]
]) {
    assert.equal(N.consumeObservation(initial,sample(change),200).cancel,reason);cases++;
}
for (const change of [
    {schema:1},{outputIdentityComplete:false},{sequence:NaN},{sequence:5},{pointer:-1},
    {buttonKnown:false,buttonsDown:true},{lockState:"false"},{sleepState:false},{ageMs:-2},
    {unknownFocusKeyboard:1},{outputs:[]},{outputs:[{id:"DP-1",events:0},{id:"DP-1",events:0}]},
    {outputs:[{id:"DP-1",events:5}]}
]) {
    const result=N.consumeObservation(initial,sample(change),200);
    assert.equal(result.state.available,false);assert.equal(result.cancel,"native-invalid");cases++;
}
for (const change of [
    {sequence:3,pointer:0},{lockState:"unlocked"},
    {outputs:[{id:"DP-1",events:2},{id:"HDMI-A-1",events:1}]},
    {outputs:[{id:"DP-1",events:3},{id:"other",events:1}]}
]) {
    const result=N.consumeObservation(initial,sample(change),200);
    assert.equal(result.state.available,false);assert.ok(result.cancel);cases++;
}
assert.equal(N.expireObservation(initial,599,500).cancel,null);
for (const now of [600,99,NaN]) {
    const result=N.expireObservation(initial,now,500);
    assert.equal(result.cancel,"native-stale");assert.equal(result.state.observedAt,null);
    assert.equal(N.expireObservation(result.state,1000,500).cancel,null);
    assert.equal(N.consumeObservation(result.state,sample(),1000).cancel,"native-recovered");cases++;
}
const input=sample();const consumed=N.consumeObservation(initial,input,200);input.outputs[0].events=99;
assert.equal(consumed.state.sample.outputs[0].events,3);cases++;
console.log("native observation consumer: "+cases+" cases passed");

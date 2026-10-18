"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");

// Exercise the actual QML callback with a controlled clock. A timer interval
// alone cannot guarantee separation between asynchronous verification turns.
const qml = fs.readFileSync("packages/kwin-script/contents/ui/OrderedGuardedWriter.qml", "utf8");
const verify = qml.slice(qml.indexOf("    function verify(command)"), qml.indexOf("    property Timer verification:"));
function fixture() {
    const frame = {x:20,y:20,width:100,height:100};
    const command = {id:"write-1",identity:"a",expectedFrom:{...frame,x:10}};
    let now = 100, authorized = true, matches = true;
    const acknowledgements = [], cancellations = [];
    const context = {
        state:{phase:"Applying",tx:{expected:{a:command.expectedFrom}},pending:{id:command.id}},
        activeCommand:command,readbacks:[],observations:0,
        captureContext:()=>({now,snapshot:{windows:[{identity:"a",rect:{...frame}}]}}),
        authority:()=>authorized,
        Model:{copyPlain:x=>JSON.parse(JSON.stringify(x))},
        Ordered:{orderedWindow:s=>s.windows[0],orderedSame:(a,b)=>JSON.stringify(a)===JSON.stringify(b),
            orderedSnapshotMatches:()=>matches},
        cancel:reason=>cancellations.push(reason),
        acknowledge:event=>acknowledgements.push(event),
    };
    vm.createContext(context);vm.runInContext(verify,context);
    return {context,command,frame,acknowledgements,cancellations,
        at:time=>{now=time;context.verify(command)},
        unsafe:()=>{authorized=false},changed:()=>{matches=false}};
}
{
    const f=fixture();f.at(100);f.at(100);f.at(115);
    assert.equal(f.acknowledgements.length,0);
    assert.equal(f.context.readbacks.length,1);
    f.at(116);
    assert.equal(f.acknowledgements.length,1);
    assert.equal(f.acknowledgements[0].observations[1].now-f.acknowledgements[0].observations[0].now,16);
    assert.equal(f.context.activeCommand,null);
    f.at(140);assert.equal(f.acknowledgements.length,1,"late callbacks cannot confirm twice");
}
{
    const f=fixture();f.at(100);f.unsafe();f.at(105);
    assert.equal(f.cancellations[0],"ordered-authority-lost");
    assert.equal(f.acknowledgements.length,0,"closely spaced callbacks still check cancellation authority");
}
{
    const f=fixture();Object.assign(f.frame,f.command.expectedFrom);f.changed();f.at(100);
    assert.equal(f.cancellations[0],"ordered-external-desktop-change");
    assert.equal(f.context.readbacks.length,0);
}
console.log("ordered QML readback: elapsed-time sampling and cancellation passed");

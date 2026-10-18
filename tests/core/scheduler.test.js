"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("core/scheduler.js", "utf8").replace(/^\.pragma library\s*/m, "");
const context = {};
vm.runInNewContext(source + "\nthis.api={createSchedulerState,transitionScheduler,nextCheck};", context);
const { createSchedulerState, transitionScheduler } = context.api;
function send(state, event) { return transitionScheduler(state, event); }
function start(outputIds, intervals, at = 0) {
    return send(createSchedulerState(), { type: "start", now: at, outputIds, intervals }).state;
}
function resume(state, now) { return send(state, { type: "resume", now }).state; }
function due(state, now) { return send(state, { type: "due", now }); }
function finish(state, command, type, now) {
    return send(state, { type, outputId: command.outputId, generation: command.generation, now }).state;
}

// Starts paused, gives every display its own clock, and breaks exact ties by ID.
{
    let state = start(["DP-2", "DP-1"], { "DP-1": 100, "DP-2": 200 });
    assert.equal(state.phase, "Paused");
    state = resume(state, 10);
    assert.deepEqual({ ...state.deadlines }, { "DP-1": 110, "DP-2": 210 });
    let result = due(state, 109);
    assert.equal(result.commands.length, 0);
    result = due(state, 110);
    assert.equal(result.commands[0].outputId, "DP-1");
    state = result.state;
    assert.equal(due(state, 500).commands.length, 0, "only one transaction may be in flight");
    state = finish(state, result.commands[0], "completed", 150);
    assert.deepEqual({ ...state.deadlines }, { "DP-1": 250, "DP-2": 210 });
    result = due(state, 210);
    assert.equal(result.commands[0].outputId, "DP-2");
}
{
    let state = resume(start(["B", "A"], { A: 50, B: 50 }), 0);
    let first = due(state, 50);
    assert.equal(first.commands[0].outputId, "A");
    state = finish(first.state, first.commands[0], "skipped", 80);
    assert.deepEqual({ ...state.deadlines }, { A: 130, B: 50 }, "skip resets only the selected display");
    let next = due(state, 80);
    assert.equal(next.commands[0].outputId, "B");
}
{
    let state = resume(start(["A", "B", "C"], { A: 20, B: 20, C: 20 }), 0);
    // A delayed timer still starts only one transaction; no loop emits a burst.
    const late = due(state, 10000);
    assert.equal(late.commands.length, 1);
    assert.equal(late.commands[0].outputId, "A");
    assert.equal(due(late.state, 10000).commands.length, 0);
}
{
    let state = resume(start(["A"], { A: 10 }), 0);
    const originalGeneration = state.generation;
    state = resume(state, 100);
    assert.equal(state.generation, originalGeneration, "repeated Resume is idempotent");
    assert.equal(state.deadlines.A, 10, "repeated Resume does not postpone an active interval");
    const pending = due(state, 10);
    const paused = send(pending.state, { type: "pause" }).state;
    assert.equal(paused.inFlight, null);
    const stale = finish(paused, pending.commands[0], "completed", 11);
    assert.equal(stale.deadlines.A, undefined, "cancelled transaction cannot resurrect a deadline");
    let running = resume(paused, 30);
    assert.equal(running.deadlines.A, 40);
    running = send(running, { type: "wake", now: 35, outputIds: ["A"], intervals: { A: 10 } }).state;
    assert.equal(running.phase, "Suspended", "wake requires safety revalidation");
    assert.deepEqual({ ...running.deadlines }, {});
    running = resume(running, 35);
    assert.equal(running.deadlines.A, 45, "resume after wake gives a fresh interval");
    running = send(running, { type: "settingsChanged", now: 36, outputIds: ["A", "B"], intervals: { A: 10, B: 30 } }).state;
    assert.equal(running.phase, "Suspended");
    assert.deepEqual({ ...running.deadlines }, {});
    assert.equal(running.inFlight, null);
    running = resume(running, 36);
    assert.deepEqual({ ...running.deadlines }, { A: 46, B: 66 });
    running = send(running, { type: "topologyChanged", now: 40, outputIds: ["B"], intervals: { B: 30 } }).state;
    assert.equal(running.phase, "Suspended");
    assert.deepEqual({ ...running.deadlines }, {}, "hotplug prunes stale output state");
}
{
    let state = resume(start(["A"], { A: 20 }), 0);
    state = send(state, { type: "companionLost" }).state;
    assert.equal(state.phase, "Suspended");
    assert.deepEqual({ ...state.deadlines }, {});
    assert.equal(due(state, 100).commands.length, 0);
}
{
    const invalid = send(createSchedulerState(), { type: "start", now: 0,
        outputIds: Array.from({ length: 65 }, (_, i) => "O" + i), intervalMs: 10 });
    assert.deepEqual({ ...invalid.state.intervals }, {}, "scheduler maps have a fixed output bound");
}
console.log("core scheduler: independent deadline and lifecycle fixtures passed");

{
 let state=resume(start(['A','B'],{A:10000,B:20000}),0);
 const before=JSON.stringify(state);
 assert.deepEqual({...context.api.nextCheck(state,2500)},{outputId:'A',remainingSeconds:7.5});
 assert.equal(JSON.stringify(state),before);
 assert.equal(context.api.nextCheck(state,20000).remainingSeconds,0);
 state=due(state,10000).state;assert.equal(context.api.nextCheck(state,10000),null);
 assert.equal(context.api.nextCheck(createSchedulerState(),0),null);
}

{
 const interval=14400000, now=10000000.1234567;
 assert.ok((now+interval-now)/1000>14400,"fixture reproduces fractional rounding overflow");
 const state=resume(start(['A'],{A:interval}),now);
 const before=JSON.stringify(state);
 assert.equal(context.api.nextCheck(state,now).remainingSeconds,14400);
 assert.ok(Math.abs(context.api.nextCheck(state,now+500).remainingSeconds-14399.5)<1e-8);
 assert.equal(context.api.nextCheck(state,now-1).remainingSeconds,14400);
 assert.equal(context.api.nextCheck(state,now+interval+1).remainingSeconds,0);
 assert.equal(JSON.stringify(state),before,"display clamping cannot alter execution deadlines");
}

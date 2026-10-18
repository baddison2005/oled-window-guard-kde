"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("core/dimming_timing.js", "utf8").replace(/^\.pragma library\s*/m, "");
const context = {};
vm.runInNewContext(source + "\nthis.api={createDimmingDelayState,updateDimmingDelay,resetDimmingDelay,createDimmingFadeState,updateDimmingFade,createDimmingScopeState,dimmingScopeEvent,resolveDimmingScope,createBrightnessRestoreState,restoreDimmingBrightness,isBrightnessRestoreActive,gateDimmingTiming};", context);
const D = context.api;

// Each entity has an independent continuous clock; losing eligibility clears
// only that entity, and changing the configured delay restarts the clocks.
{
    let a = D.createDimmingDelayState(), b = D.createDimmingDelayState();
    a = D.updateDimmingDelay(a, ["window"], 5, 100).state;
    b = D.updateDimmingDelay(b, ["display"], 10, 100).state;
    assert.deepEqual(Array.from(D.updateDimmingDelay(a, ["window"], 5, 105).ready), ["window"]);
    assert.deepEqual(Array.from(D.updateDimmingDelay(b, ["display"], 10, 105).ready), []);
    a = D.updateDimmingDelay(a, [], 5, 106).state;
    assert.deepEqual(Array.from(D.updateDimmingDelay(a, ["window"], 5, 106).ready), []);
    const changed = D.updateDimmingDelay(a, ["window"], 6, 111);
    assert.deepEqual(Array.from(changed.ready), []);
    assert.deepEqual(Array.from(D.updateDimmingDelay(changed.state, ["window"], 6, 117).ready), ["window"]);
}

// Fade clocks start when readiness begins and progress independently.
{
    let fade = D.createDimmingFadeState();
    fade = D.updateDimmingFade(fade, ["a", "b"], 4, 0).state;
    assert.equal(D.updateDimmingFade(fade, ["a", "b"], 4, 2).progress.a, 0.5);
    fade = D.updateDimmingFade(fade, ["a"], 4, 2).state;
    assert.equal(D.updateDimmingFade(fade, ["a", "b"], 4, 3).progress.a, 0.75);
    assert.equal(D.updateDimmingFade(fade, ["a"], 0, 3).progress.a, 1);
}

// Desktop clicks keep their display bright through stale focus samples. A
// desktop/activity switch scopes to the pointer display; window click/key
// releases the override and permits focus to drive scope again.
{
    let scope = D.createDimmingScopeState();
    scope = D.dimmingScopeEvent(scope, { type:"desktopClick", displayId:"DP-1" });
    for (let i = 0; i < 4; i++) assert.deepEqual(JSON.parse(JSON.stringify(D.resolveDimmingScope(scope, "DP-2", "DP-2"))),
        { focusedWindowId:null, displayId:"DP-1", desktop:true });
    scope = D.dimmingScopeEvent(scope, { type:"windowKey" });
    assert.equal(D.resolveDimmingScope(scope, "DP-2", "DP-1").focusedWindowId, "DP-2");
    scope = D.dimmingScopeEvent(scope, { type:"activitySwitch", activityId:"work", pointerDisplayId:"DP-1" });
    assert.deepEqual(JSON.parse(JSON.stringify(D.resolveDimmingScope(scope, "DP-2", "DP-2"))),
        { focusedWindowId:null, displayId:"DP-1", desktop:false });
    assert.deepEqual(JSON.parse(JSON.stringify(D.resolveDimmingScope(scope, "window-1", "DP-1"))),
        { focusedWindowId:"window-1", displayId:"DP-1", desktop:false });
}

// Restore holds brightness for at least five seconds or the longer configured
// delay. A repeated restore restarts the interval and advances its generation.
{
    let restore = D.createBrightnessRestoreState();
    restore = D.restoreDimmingBrightness(restore, 100000, 3000);
    assert.equal(D.isBrightnessRestoreActive(restore, 104999), true);
    assert.equal(D.isBrightnessRestoreActive(restore, 105000), false);
    restore = D.restoreDimmingBrightness(restore, 104000, 12000);
    assert.equal(D.isBrightnessRestoreActive(restore, 115999), true);
    assert.equal(D.isBrightnessRestoreActive(restore, 116000), false);
    assert.equal(restore.generation, 2);
}

// Loss of renderer/session availability and pause, lock, inactivity or sleep
// clears delay/fade state so resumed dimming starts bright and waits again.
{
    let state = { delay:D.updateDimmingDelay(D.createDimmingDelayState(), ["DP-1"], 20, 0).state,
        fade:D.updateDimmingFade(D.createDimmingFadeState(), ["DP-1"], 3, 20).state };
    const ready={available:true,rendererProven:true,paused:false,locked:false,inactive:false,sleeping:false,rendererError:false};
    for (const status of [{available:false}, {paused:true}, {locked:true},
        {inactive:true}, {sleeping:true}, {rendererError:true}, {rendererProven:false}]) {
        const gated = D.gateDimmingTiming(state, {...ready,...status});
        assert.equal(gated.bright, true);
        assert.deepEqual(Object.keys(gated.state.delay.since), []);
        assert.deepEqual(Object.keys(gated.state.fade.starts), []);
    }
    assert.equal(D.gateDimmingTiming(state, ready).bright, false);
    assert.equal(D.gateDimmingTiming(state, {available:true}).bright, true);
    const resumed = D.gateDimmingTiming(state, {...ready,available:false}).state;
    assert.deepEqual(Array.from(D.updateDimmingDelay(resumed.delay, ["DP-1"], 20, 21).ready), []);
}

"use strict";
const assert = require("node:assert/strict");
const { renderDiagnostic } = require("../../tools/diagnose_snapshot.js");

const snapshot = {
    topologyRevision: "topo-1", focusedIdentity: null,
    outputs: [{ id: "DP-1", connector: "DP-1", serial: "private-serial", scale: 1,
        geometry: { x: -100, y: 0, width: 500, height: 400 },
        usableRect: { x: -100, y: 20, width: 500, height: 380 } }],
    windows: [{ identity: "opaque-1", appId: "private-app", title: "private window title",
        outputIds: ["DP-1"], rect: { x: 0, y: 30, width: 100, height: 80 },
        visible: true, onCurrentDesktop: true, onCurrentActivity: true,
        focused: false, managed: true, modal: false, transient: false,
        movable: true, resizable: true, minimized: false,
        maximized: false, fullscreen: false, tiled: false, windowType: "normal", skipReasons: [] }],
};
const view = renderDiagnostic(`qml: OLED_WINDOW_GUARD_SNAPSHOT ${JSON.stringify({ snapshot })}\n`, ["DP-1"]);
assert.match(view, /Eligible: 1/);
assert.match(view, /Obstacles: 1/);
assert.doesNotMatch(view, /private|opaque|DP-1/);
const adjacent=JSON.parse(JSON.stringify(snapshot));
adjacent.windows.push({...adjacent.windows[0],identity:'opaque-2',rect:{x:99,y:30,width:100,height:80}});
assert.match(renderDiagnostic(JSON.stringify(adjacent),['DP-1']),/Eligible: 2/);
adjacent.windows[1].rect.x=97;
assert.match(renderDiagnostic(JSON.stringify(adjacent),['DP-1']),/overlapping_window: 2/);
console.log("diagnostic view: tests passed");

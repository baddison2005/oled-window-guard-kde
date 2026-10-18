"use strict";
const assert = require("node:assert/strict");
const { proposeGroupRotation } = require("../../core/group_rotation.js");
const rect = (x, y, width = 100, height = 100) => ({ x, y, width, height });
const win = (identity, x, y, width = 100, height = 100, resizable = false) => ({ identity, rect: rect(x, y, width, height), resizable });
const area = rect(0, 0, 1000, 800);
const opts = extra => Object.assign({ padding: 0, sizeTolerance: 0.1, maximumWindows: 8, random: () => 0.25, now: () => 10 }, extra);

{
    // Overlapping rectangles describe alternate placements. Final windows still cannot overlap.
    const zones = [rect(0, 0), rect(100, 0), rect(50, 0)];
    const windows = [win("a", 0, 0), win("b", 100, 0)];
    const result = proposeGroupRotation(windows, zones, area, [], opts());
    assert.equal(result.moves.length, 2);
    assert.equal(new Set(result.moves.map(m => m.identity)).size, 2);
    for (let i = 0; i < result.moves.length; i++) for (let j = i + 1; j < result.moves.length; j++) {
        const a = result.moves[i].toRect, b = result.moves[j].toRect;
        assert.ok(!(a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height));
    }
}

{
    const zones = [rect(0, 0, 200, 200), rect(300, 0, 195, 200)];
    const fixed = proposeGroupRotation([win("fixed", 0, 0, 200, 200)], zones, area, [], opts());
    assert.equal(fixed.moves.length, 0);
    const capable = proposeGroupRotation([win("capable", 0, 0, 200, 200, true)], zones, area, [], opts({ padding: 2 }));
    assert.equal(capable.moves.length, 1);
    assert.deepEqual(capable.moves[0].toRect, rect(300, 0, 195, 200));
    assert.equal(capable.moves[0].permitsRoundingResize, true);
    assert.equal(capable.moves[0].roundingResizeLimit, 6);
}

{
    const zones = [rect(0, 0), rect(200, 0), rect(400, 0)];
    const windows = [win("a", 0, 0), win("b", 200, 0)];
    const limited = proposeGroupRotation(windows, zones, area, [], opts({ maximumWindows: 1 }));
    assert.equal(limited.moves.length, 1);
    assert.equal(new Set(limited.moves.map(m => m.identity)).size, limited.moves.length);
}

{
    const zones = [rect(0, 0), rect(200, 0)];
    const blocked = proposeGroupRotation([win("a", 0, 0)], zones, area, [rect(200, 0)], opts());
    assert.equal(blocked.moves.length, 0);
    const cancelled = proposeGroupRotation([win("a", 0, 0)], zones, area, [], opts({ shouldCancel: () => true }));
    assert.equal(cancelled.reason, "search_stopped");
    assert.equal(cancelled.moves.length, 0);
    const exhausted = proposeGroupRotation([win("a", 0, 0)], zones, area, [], opts({ maxEvaluations: 1 }));
    assert.ok(exhausted.stats.evaluations <= 1);
    assert.equal(exhausted.stats.bounded, true);
}

console.log("group rotation tests passed");

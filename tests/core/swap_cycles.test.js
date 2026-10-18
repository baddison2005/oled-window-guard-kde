"use strict";
const assert = require("node:assert/strict");
const { proposeSimilarCycles, proposeGroupedCycles } = require("../../core/swap_cycles.js");
const rect = (x, y, width, height) => ({ x, y, width, height });
const win = (identity, x, y, width = 100, height = 100) => ({ identity, rect: rect(x, y, width, height) });
const area = rect(0, 0, 1000, 800);
const options = extra => Object.assign({ maximumWindows: 8, sizeTolerance: 0.1, random: () => 0.2, now: () => 10 }, extra);

{
    const windows = [win("a", 0, 0), win("b", 200, 0), win("c", 500, 0, 200, 100)];
    const result = proposeSimilarCycles(windows, area, options());
    assert.equal(result.moves.length, 2);
    assert.deepEqual(new Set(result.moves.map(m => m.identity)), new Set(["a", "b"]));
    for (const move of result.moves) {
        const source = windows.find(w => w.identity === move.identity).rect;
        assert.equal(move.toRect.width, source.width);
        assert.equal(move.toRect.height, source.height);
    }
    assert.equal(proposeSimilarCycles([windows[0]], area, options()).reason, "no_similar_cycle");
}

{
    const windows = [win("a", 0, 0), win("b", 108, 0), win("c", 500, 0), win("d", 608, 0)];
    const result = proposeGroupedCycles(windows, area, options());
    assert.equal(result.moves.length, 4);
    assert.equal(result.moves.find(m => m.identity === "b").toRect.x - result.moves.find(m => m.identity === "a").toRect.x, 108);
    assert.equal(result.moves.find(m => m.identity === "d").toRect.x - result.moves.find(m => m.identity === "c").toRect.x, 108);
    assert.equal(result.stats.groups, 2);
}

{
    let calls = 0;
    const windows = [win("0", 0, 0), win("1", 108, 0), win("2", 500, 0), win("3", 608, 0)];
    const result = proposeSimilarCycles(windows, area, options({ maxEvaluations: 1, shouldCancel: () => ++calls > 2 }));
    assert.ok(result.stats.evaluations <= 1);
    const stopped = proposeGroupedCycles(windows, area, options({ shouldCancel: () => true }));
    assert.equal(stopped.reason, "search_stopped");
    assert.equal(stopped.moves.length, 0);
}

{
    const windows = [win("a", 0, 0), win("b", 108, 0), win("c", 0, 100), win("d", 108, 100)];
    assert.equal(proposeGroupedCycles(windows, area, options({ maximumWindows: 3 })).moves.length, 0);
    const result = proposeSimilarCycles(windows, area, options({ maximumWindows: 2 }));
    assert.ok(result.moves.length <= 2);
}

console.log("swap cycle tests passed");

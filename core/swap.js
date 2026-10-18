.pragma library
.import "model.js" as Model
.import "planner.js" as Planner
.import "swap_cycles.js" as Cycles
.import "free_swap.js" as FreeSwap
.import "swap_steps.js" as Steps

// Plan only. A live adapter must still pass the warning/safety transaction
// gates before any ordered step can be issued.
function swapFinite(n) { return typeof n === "number" && isFinite(n); }
function swapCopy(value) { return Model.copyPlain(value); }
function swapShuffle(items, random) {
    var result = items.slice();
    for (var i = result.length - 1; i > 0; i--) {
        var value = random();
        if (!swapFinite(value) || value < 0 || value >= 1) throw new Error("random must yield values in [0,1)");
        var j = Math.floor(value * (i + 1)), temporary = result[i]; result[i] = result[j]; result[j] = temporary;
    }
    return result;
}
function planSwap(snapshot, registryState, options) {
    options = options || {};
    if (!Model.validateSnapshot(snapshot).valid || !Planner.registryMatches(snapshot, registryState) ||
            !Planner.focusResolved(snapshot)) throw new Error("snapshot and registry mismatch or unresolved focus");
    var outputId = options.outputId, maximumWindows = options.maximumWindows, tolerance = options.sizeTolerance;
    if (typeof outputId !== "string" || !outputId || !swapFinite(maximumWindows) || Math.floor(maximumWindows) !== maximumWindows ||
            maximumWindows < 2 || maximumWindows > 12 || !swapFinite(tolerance) || tolerance < 0 || tolerance > .25 ||
            typeof options.allowTransientOverlap !== "boolean" || typeof options.keepSimilarWindowsTogether !== "boolean")
        throw new Error("invalid swap options");
    var random = options.random || Math.random, now = options.now || function() { return Date.now(); };
    var maxEvaluations = options.maxEvaluations === undefined ? 2400 : options.maxEvaluations;
    var deadlineMs = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    if (typeof random !== "function" || typeof now !== "function" ||
            (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function") ||
            !swapFinite(maxEvaluations) || Math.floor(maxEvaluations) !== maxEvaluations || maxEvaluations < 1 ||
            !swapFinite(deadlineMs) || deadlineMs < 0) throw new Error("invalid swap callbacks or budgets");
    var start = now(), deadline = start + deadlineMs;
    if (!swapFinite(start) || !swapFinite(deadline)) throw new Error("invalid swap clock");
    var output = snapshot.outputs.filter(function(o) { return o.id === outputId; })[0];
    if (!output || !Array.isArray(registryState.selectedOutputIds) || registryState.selectedOutputIds.indexOf(outputId) < 0)
        throw new Error("output is unknown or not selected");
    var registered = Object.create(null), eligible = [], ineligible = [], trace = [];
    registryState.windows.forEach(function(w) { registered[w.identity] = w; });
    snapshot.windows.forEach(function(w) {
        if (Planner.eligible(w, snapshot, output, registered[w.identity])) eligible.push(w);
        else { ineligible.push(w); trace.push({ identity:w.identity, status:"skipped", reason:registered[w.identity].skipReasons[0] || "ineligible" }); }
    });
    if (options.keepSimilarWindowsTogether) {
        var blockedIds = Object.create(null), changed = true;
        ineligible.forEach(function(w) {
            if (Planner.rawObstacle(w) && w.outputIds && w.outputIds.length === 1 && w.outputIds[0] === outputId)
                blockedIds[w.identity] = true;
        });
        while (changed) {
            changed = false;
            eligible.forEach(function(w) {
                if (blockedIds[w.identity]) return;
                var connected = snapshot.windows.some(function(other) { return blockedIds[other.identity] &&
                    Planner.rawObstacle(other) && Cycles.closeSize(w.rect, other.rect, tolerance) &&
                    Cycles.adjacent(w.rect, other.rect); });
                if (connected) { blockedIds[w.identity] = true; changed = true; }
            });
        }
        eligible = eligible.filter(function(w) {
            var blocked = blockedIds[w.identity] === true;
            if (blocked) trace.push({ identity:w.identity, status:"skipped", reason:"ineligible_neighbour" });
            return !blocked;
        });
    }
    var selected = swapShuffle(eligible, random).slice(0, 12), selectedIds = Object.create(null);
    selected.forEach(function(w) { selectedIds[w.identity] = true; });
    if (options.keepSimilarWindowsTogether) {
        // The bounded candidate selection must not cut a connected block.
        var incomplete=true;
        while(incomplete) {
            incomplete=false;
            selected=selected.filter(function(w) {
                var missing=eligible.some(function(other){return !selectedIds[other.identity] &&
                    Cycles.closeSize(w.rect,other.rect,tolerance) && Cycles.adjacent(w.rect,other.rect);});
                if(missing){delete selectedIds[w.identity];incomplete=true;trace.push({identity:w.identity,status:"skipped",reason:"unselected_neighbour"});}
                return !missing;
            });
        }
    }
    var obstacles = snapshot.windows.filter(function(w) { return Planner.rawObstacle(w) && !selectedIds[w.identity]; });
    var bare = selected.map(function(w) { return { identity:w.identity, rect:swapCopy(w.rect), integerPosition:w.integerPosition===true, resizable:w.resizable===true, minimumSize:w.minimumSize }; });
    var hasNeighbourBlock = options.keepSimilarWindowsTogether && selected.some(function(a, i) {
        return selected.some(function(b, j) { return i !== j && Cycles.closeSize(a.rect,b.rect,tolerance) && Cycles.adjacent(a.rect,b.rect); });
    });
    var attempts = [], candidates = [];
    function remaining() { return Math.max(0, deadline - now()); }
    function stopped() { return options.shouldCancel && options.shouldCancel() || now() >= deadline; }
    function trial(kind, proposed) {
        var attempt={ kind:kind, reason:proposed.reason, stats:proposed.stats };
        if(attempts.length<32)attempts.push(attempt);
        if (!proposed.moves || proposed.moves.length < 2 || proposed.moves.length > maximumWindows || stopped()) return false;
        var plan = Model.createPlan({ snapshotRevision:snapshot.topologyRevision, outputIds:[outputId], moves:proposed.moves,
            createdAt:start, metadata:{ mode:"swap", strategy:kind, maximumWindows:maximumWindows,
                allowTransientOverlap:options.allowTransientOverlap, complete:true } });
        var finalCheck = Planner.validateSwapFinalPlan(snapshot, registryState, plan);
        if (!finalCheck.valid) { attempt.reason = "unsafe-final-layout"; return false; }
        if (stopped()) { attempt.reason = "search-stopped-after-validation"; return false; }
        var ordered = Steps.orderSwapSteps(snapshot, registryState, plan, { maxEvaluations:maxEvaluations,
            now:now, deadlineMs:remaining(), shouldCancel:options.shouldCancel });
        if (!ordered.steps.length) { attempt.reason = ordered.reason; return false; }
        candidates.push({ plan:plan, steps:ordered.steps, strategy:kind, stepStats:ordered.stats });
        candidates.sort(function(a,b){return b.plan.moves.length-a.plan.moves.length;});
        if(candidates.length>8)candidates.length=8;
        return true;
    }
    var searchOptions = { maximumWindows:maximumWindows, sizeTolerance:tolerance, random:random,
        now:now, maxEvaluations:maxEvaluations, shouldCancel:options.shouldCancel,
        movementCollision:function(id,rect,otherId,otherRect) {return Model.movementCollision(snapshot,null,id,rect,otherId,otherRect);},
        acceptMoves:function(moves) { return Planner.validateSwapFinalPlan(snapshot,registryState,Model.createPlan({snapshotRevision:snapshot.topologyRevision,outputIds:[outputId],moves:moves,
            metadata:{mode:"swap",maximumWindows:maximumWindows,allowTransientOverlap:options.allowTransientOverlap,complete:true}})).valid; } };
    if (options.keepSimilarWindowsTogether && !stopped()) {
        searchOptions.deadlineMs = remaining();
        searchOptions.obstacles=obstacles;
        searchOptions.acceptMoves=function(moves){return trial("grouped-cycle",{moves:moves,reason:"grouped_arrangement",stats:{}});};
        var grouped=Cycles.proposeGroupedCycles(bare, output.usableRect, searchOptions);
        if(!grouped.moves.length)trial("grouped-cycle",grouped);
    }
    if (!stopped() && !hasNeighbourBlock) {
        searchOptions.deadlineMs = remaining();
        trial("similar-cycle", Cycles.proposeSimilarCycles(bare, output.usableRect, searchOptions));
    }
    if (!stopped() && !hasNeighbourBlock && (!candidates.length || candidates[0].plan.moves.length < maximumWindows)) {
        searchOptions.deadlineMs = remaining();
        trial("free-swap", FreeSwap.proposeFreeSwap(bare, output.usableRect,
            obstacles.map(function(w) { return swapCopy(w.rect); }), searchOptions));
    }
    candidates.sort(function(a, b) { return b.plan.moves.length - a.plan.moves.length; });
    // An optional search may consume its budget after an earlier proposal has
    // passed final validation and ordered-step validation. Keep that proposal;
    // cancellation still revokes every result, including a validated fallback.
    var chosen = options.shouldCancel && options.shouldCancel() ? null : (candidates[0] || null);
    if (chosen) chosen.plan.moves.forEach(function(move) { trace.push({ identity:move.identity, status:"proposed", strategy:chosen.strategy }); });
    return { plan:chosen ? chosen.plan : null, steps:chosen ? chosen.steps : [], trace:trace,
        attempts:attempts, stats:{ candidates:eligible.length, selected:selected.length, complete:!!chosen,
            stopped:options.shouldCancel && options.shouldCancel() ? "cancelled" : now() >= deadline ? "deadline_exceeded" : null } };
}

function planCoordinatedShift(snapshot, registryState, options) {
    options = options || {};
    if (!Model.validateSnapshot(snapshot).valid || !Planner.registryMatches(snapshot, registryState) ||
            !Planner.focusResolved(snapshot)) throw new Error("snapshot and registry mismatch or unresolved focus");
    var outputId = options.outputId, maximumWindows = options.maximumWindows, range = options.shiftRangePercent;
    if (typeof outputId !== "string" || !outputId || !swapFinite(maximumWindows) || Math.floor(maximumWindows) !== maximumWindows ||
            maximumWindows < 1 || maximumWindows > 12 || !swapFinite(range) || range < 1 || range > 100 ||
            typeof options.shiftHorizontalOrder !== "boolean" || typeof options.shiftVerticalOrder !== "boolean")
        throw new Error("invalid coordinated shift options");
    var output = snapshot.outputs.filter(function(o) { return o.id === outputId; })[0];
    if (!output || !Array.isArray(registryState.selectedOutputIds) || registryState.selectedOutputIds.indexOf(outputId) < 0)
        throw new Error("output is unknown or not selected");
    var byId = Object.create(null), random = options.random || Math.random, now = options.now || function() { return Date.now(); };
    registryState.windows.forEach(function(w) { byId[w.identity] = w; });
    var eligible = snapshot.windows.filter(function(w) { return Planner.eligible(w, snapshot, output, byId[w.identity]); });
    var selected = swapShuffle(eligible, random).slice(0, 12), selectedIds = Object.create(null);
    selected.forEach(function(w) { selectedIds[w.identity] = true; });
    if (options.keepSimilarWindowsTogether) {
        // The bounded candidate selection must not cut a connected block.
        var incomplete=true;
        while(incomplete) {
            incomplete=false;
            selected=selected.filter(function(w) {
                var missing=eligible.some(function(other){return !selectedIds[other.identity] &&
                    Cycles.closeSize(w.rect,other.rect,tolerance) && Cycles.adjacent(w.rect,other.rect);});
                if(missing){delete selectedIds[w.identity];incomplete=true;trace.push({identity:w.identity,status:"skipped",reason:"unselected_neighbour"});}
                return !missing;
            });
        }
    }
    var obstacles = snapshot.windows.filter(function(w) { return Planner.rawObstacle(w) && !selectedIds[w.identity]; });
    // Order changes require multiple participants. A valid one-window limit
    // still permits ordinary drift; skip coordination and use the safe fallback.
    var proposal = maximumWindows === 1 ? {moves:[],stats:{}} : FreeSwap.proposeCoordinatedShift(selected.map(function(w) { return { identity:w.identity, rect:swapCopy(w.rect), integerPosition:w.integerPosition===true }; }),
        output.usableRect, obstacles.map(function(w) { return swapCopy(w.rect); }), {
            maximumWindows:maximumWindows, shiftRangePercent:range,
            enableHorizontalOrderChange:options.shiftHorizontalOrder,
            enableVerticalOrderChange:options.shiftVerticalOrder,
            random:random, now:now, deadlineMs:options.deadlineMs === undefined ? 30 : options.deadlineMs,
            maxEvaluations:options.maxEvaluations === undefined ? 2400 : options.maxEvaluations,
            shouldCancel:options.shouldCancel,
            movementCollision:function(id,rect,otherId,otherRect) {return Model.movementCollision(snapshot,null,id,rect,otherId,otherRect);} });
    if (proposal.moves.length >= 2) {
        var plan = Model.createPlan({ snapshotRevision:snapshot.topologyRevision, outputIds:[outputId], moves:proposal.moves,
            createdAt:now(), metadata:{ mode:"coordinated_shift", complete:true, maximumWindows:maximumWindows,
                shiftRangePercent:range, shiftHorizontalOrder:options.shiftHorizontalOrder,
                shiftVerticalOrder:options.shiftVerticalOrder, allowTransientOverlap:true } });
        if (Planner.validateCoordinatedFinalPlan(snapshot, registryState, plan).valid) {
            var ordered = Steps.orderSwapSteps(snapshot, registryState, plan, {
                now:now, deadlineMs:options.deadlineMs === undefined ? 30 : options.deadlineMs,
                maxEvaluations:options.maxEvaluations === undefined ? 2400 : options.maxEvaluations,
                shouldCancel:options.shouldCancel });
            if (ordered.steps.length && !(options.shouldCancel && options.shouldCancel()))
                return { plan:plan, steps:ordered.steps, reason:"coordinated_shift", stats:proposal.stats };
        }
    }
    // The optional order change is best effort. Ordinary drift remains the
    // fallback, with its own strict final-safety validator.
    var ordinary = Planner.planOrdinaryShift(snapshot, registryState, { outputId:outputId,
        shiftRangePercent:range, maximumWindows:maximumWindows, random:random, now:now,
        deadlineMs:options.deadlineMs === undefined ? 30 : options.deadlineMs,
        maxEvaluations:options.maxEvaluations === undefined ? 2400 : options.maxEvaluations,
        shouldCancel:options.shouldCancel });
    return { plan:ordinary.plan, steps:[], reason:"ordinary_shift_fallback", stats:proposal.stats };
}

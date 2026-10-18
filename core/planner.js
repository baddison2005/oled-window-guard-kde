.pragma library
.import "model.js" as Model
.import "registry.js" as Registry

// Pure, bounded ordinary shift planning. No compositor objects or writes.
var DEFAULT_MAX_EVALUATIONS = 2400;
var HARD_MAX_EVALUATIONS = 50000;

function copy(value) { return Model.copyPlain(value); }
function sameRect(a, b) {
    return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height;
}
function validRect(r) { return !!Model.rectangle(r); }
function contained(a, b) { return Model.rectangleContains(a, b); }
function overlaps(a, b) { return Model.rectangleOverlaps(a, b); }
function finite(n) { return typeof n === "number" && isFinite(n); }
function overlapsNearby(a, b) {
    return a.x < b.x + b.width && b.x < a.x + a.width &&
        a.y < b.y + b.height && b.y < a.y + a.height && overlaps(a, b);
}
function gaussian(random) {
    var u = random(), v = random();
    if (!finite(u) || u < 0 || u >= 1 || !finite(v) || v < 0 || v >= 1) throw new Error("random must yield values in [0,1)");
    if (u === 0) u = 1 / 4294967296;
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}
function shuffled(items, random) {
    var result = items.slice();
    for (var i = result.length - 1; i > 0; i--) {
        var value = random();
        if (!finite(value) || value < 0 || value >= 1) throw new Error("random must yield values in [0,1)");
        var j = Math.floor(value * (i + 1)), tmp = result[i]; result[i] = result[j]; result[j] = tmp;
    }
    return result;
}
function collides(rect, obstacles) {
    for (var i = 0; i < obstacles.length; i++) if (overlapsNearby(rect, obstacles[i])) return true;
    return false;
}
function rawObstacle(w) {
    return w.visible !== false && w.minimized !== true && w.onCurrentDesktop !== false && w.onCurrentActivity !== false &&
        w.windowType !== "desktop" && w.windowType !== "dock" && w.windowType !== "panel" &&
        w.windowType !== "guard" && w.windowType !== "lock" && w.windowType !== "notification" && w.windowType !== "osd" && validRect(w.rect);
}
function focusResolved(snapshot) {
    if (snapshot.focusedIdentity === null) return true;
    if (typeof snapshot.focusedIdentity !== "string" || !snapshot.focusedIdentity) return false;
    for (var i = 0; i < snapshot.windows.length; i++)
        if (snapshot.windows[i].identity === snapshot.focusedIdentity) return true;
    return false;
}
function registryMatches(snapshot, state) {
    if (!snapshot || !state || snapshot.topologyRevision !== state.topologyRevision ||
            state.snapshotSignature !== Registry.snapshotSignature(snapshot) ||
            !Array.isArray(state.windows) || !Array.isArray(state.outputs) ||
            state.windows.length !== snapshot.windows.length || state.outputs.length !== snapshot.outputs.length) return false;
    var byIdentity = Object.create(null), byOutput = Object.create(null), i;
    for (i = 0; i < state.windows.length; i++) {
        var rw = state.windows[i];
        if (!rw || typeof rw.identity !== "string" || byIdentity[rw.identity] || !validRect(rw.rect) || !validRect(rw.lastFrame) ||
                !sameRect(rw.rect, rw.lastFrame)) return false;
        byIdentity[rw.identity] = rw;
    }
    for (i = 0; i < state.outputs.length; i++) {
        var ro = state.outputs[i];
        if (!ro || typeof ro.id !== "string" || byOutput[ro.id]) return false;
        byOutput[ro.id] = ro;
    }
    for (i = 0; i < snapshot.windows.length; i++) {
        var sw = snapshot.windows[i], match = sw && byIdentity[sw.identity];
        if (!match || !sameRect(match.rect, sw.rect)) return false;
    }
    for (i = 0; i < snapshot.outputs.length; i++) {
        var so = snapshot.outputs[i], out = so && byOutput[so.id];
        if (!out || !sameRect(out.geometry, so.geometry) || !sameRect(out.usableRect, so.usableRect)) return false;
    }
    return true;
}
function eligible(w, snapshot, output, registryWindow) {
    if (!w || !registryWindow || registryWindow.candidate !== true || registryWindow.obstacle !== true ||
            !validRect(w.rect) || !contained(output.usableRect, w.rect)) return false;
    if ((typeof w.focused !== "boolean" || ((snapshot.focusedIdentity === w.identity || w.focused) && registryWindow.allowIdleFocused!==true)) || w.visible !== true ||
            w.onCurrentDesktop !== true || w.onCurrentActivity !== true || w.managed !== true ||
            w.modal !== false || w.transient !== false || w.movable !== true ||
            w.minimized !== false || w.maximized !== false || w.fullscreen !== false ||
            w.tiled !== false || w.windowType !== "normal" || !Array.isArray(w.outputIds) ||
            w.outputIds.length !== 1 || w.outputIds[0] !== output.id || !Array.isArray(w.skipReasons) ||
            w.skipReasons.length !== 0) return false;
    return true;
}
// XWayland rounds placement to logical pixels. Choose that destination before
// containment/collision/range validation and preview, never after validation.
// Preserve sizes and exact readback checks; unexpected client changes fail.
function makeRect(from, dx, dy, integral) { return { x: integral?Math.round(from.x + dx):from.x + dx, y: integral?Math.round(from.y + dy):from.y + dy, width: from.width, height: from.height }; }
function proposed(from, frame, range, magnitude, angle, directionScale, integral) {
    var dx = Math.cos(angle) * frame.width * range * magnitude * directionScale;
    var dy = Math.sin(angle) * frame.height * range * magnitude * directionScale;
    return makeRect(from, dx, dy, integral);
}
function edgeTargets(from, frame, range, magnitude, obstacles, integral) {
    var mx = frame.width * range * magnitude, my = frame.height * range * magnitude;
    var values = [], seenX = Object.create(null), seenY = Object.create(null);
    function addX(position) {
        if (Math.abs(position - from.x) > mx || seenX[position]) return;
        seenX[position] = true; values.push(makeRect(from, position - from.x, 0, integral));
    }
    function addY(position) {
        if (Math.abs(position - from.y) > my || seenY[position]) return;
        seenY[position] = true; values.push(makeRect(from, 0, position - from.y, integral));
    }
    addX(frame.x); addX(frame.x + frame.width - from.width);
    addY(frame.y); addY(frame.y + frame.height - from.height);
    for (var i = 0; i < obstacles.length; i++) {
        addX(obstacles[i].x - from.width); addX(obstacles[i].x + obstacles[i].width);
        addY(obstacles[i].y - from.height); addY(obstacles[i].y + obstacles[i].height);
    }
    return values;
}
function safeCandidate(target, from, frame, range) {
    if (!validRect(target) || !contained(frame, target) || sameRect(target, from)) return false;
    var nx = (target.x - from.x) / (frame.width * range), ny = (target.y - from.y) / (frame.height * range);
    if (nx * nx + ny * ny > 1.000000001) return false;
    return true;
}
function planOrdinaryShift(snapshot, registryState, options) {
    options = options || {};
    var errors = Model.validateSnapshot(snapshot).errors;
    if (errors.length || !focusResolved(snapshot) || !registryMatches(snapshot, registryState))
        throw new Error("snapshot and registry mismatch or unresolved focus");
    var outputId = options.outputId, rangePercent = options.shiftRangePercent, maxWindows = options.maximumWindows;
    if (typeof outputId !== "string" || !outputId || !finite(rangePercent) || rangePercent < 1 || rangePercent > 100 ||
            !finite(maxWindows) || maxWindows < 1 || maxWindows > 12 || Math.floor(maxWindows) !== maxWindows)
        throw new Error("invalid ordinary shift options");
    var maxEval = options.maxEvaluations === undefined ? DEFAULT_MAX_EVALUATIONS : options.maxEvaluations;
    if (!finite(maxEval) || Math.floor(maxEval) !== maxEval || maxEval < 1) throw new Error("maxEvaluations must be a positive finite integer");
    maxEval = Math.min(maxEval, HARD_MAX_EVALUATIONS);
    var random = options.random || Math.random, now = options.now || function() { return Date.now(); };
    if (typeof random !== "function" || typeof now !== "function" || (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function"))
        throw new Error("invalid planner callback");
    var duration = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    if (!finite(duration) || duration < 0) throw new Error("deadlineMs must be nonnegative");
    var start = now(), deadline = start + duration;
    if (!finite(start) || !finite(deadline)) throw new Error("planner clock must be finite");
    var output = null, i;
    for (i = 0; i < snapshot.outputs.length; i++) if (snapshot.outputs[i].id === outputId) output = snapshot.outputs[i];
    if (!output || !Array.isArray(registryState.selectedOutputIds) || registryState.selectedOutputIds.indexOf(outputId) < 0)
        throw new Error("outputId is unknown or not selected");
    var registryById = Object.create(null), candidates = [], obstacles = [], trace = [];
    registryState.windows.forEach(function(w) { registryById[w.identity] = w; });
    snapshot.windows.forEach(function(w) {
        var registered = registryById[w.identity];
        if (rawObstacle(w) || registered.obstacle === true) obstacles.push({ identity: w.identity, rect: w.rect });
        if (eligible(w, snapshot, output, registered)) candidates.push(w);
        else trace.push({ identity: w.identity, status: "skipped",
            reason: registered.skipReasons && registered.skipReasons.length ? registered.skipReasons[0] :
                (w.outputIds && w.outputIds.length === 1 && w.outputIds[0] !== outputId ? "other_output" : "ineligible") });
    });
    var moves = [], evaluated = 0, stopped = false, reason;
    function halted() {
        if (options.shouldCancel && options.shouldCancel()) return "cancelled";
        if (evaluated >= maxEval) return "evaluation_budget_exhausted";
        if (now() >= deadline) return "deadline_exceeded";
        return null;
    }
    var order = shuffled(candidates, random), staged = [];
    for (i = 0; i < order.length && moves.length < maxWindows; i++) {
        reason = halted();
        if (reason) { stopped = true; break; }
        var w = order[i], frame = output.usableRect, from = w.rect, prev = registryById[w.identity].previousFrame;
        var fixedWindows = obstacles.filter(function(o) { return o.identity !== w.identity; });
        function collidesFixed(target) { return fixedWindows.some(function(o){return Model.movementCollision(snapshot,null,w.identity,target,o.identity,o.rect);}); }
        var fixed = fixedWindows.map(function(o){return o.rect;});
        if (collidesFixed(from)) { trace.push({ identity: w.identity, status: "skipped", reason: "overlapping_window" }); continue; }
        staged.forEach(function(m) { fixed.push(m.toRect); fixedWindows.push({identity:m.identity,rect:m.toRect}); });
        var g = gaussian(random), magnitude = Math.max(0.2, Math.min(1, 0.6 + g * (0.8 / 3)));
        var best = null, bestScore = -Infinity, previousFallback = null, fallbackScore = -Infinity, preferredMinimum = 0.2;
        // Random legal directions followed by edge alignments; relax the preferred move size in steps.
        for (var relax = 0; relax < 4 && !best; relax++) {
            var minMove = preferredMinimum * (3 - relax) / 3;
            for (var attempt = 0; attempt < 32; attempt++) {
                reason = halted(); if (reason) { stopped = true; break; }
                evaluated++;
                var angle = random() * Math.PI * 2, radial = Math.sqrt(random()) * magnitude;
                var target = proposed(from, frame, rangePercent / 100, radial, angle, 1, w.integerPosition===true);
                if (!safeCandidate(target, from, frame, rangePercent / 100)) continue;
                var nx = (target.x - from.x) / (frame.width * rangePercent / 100), ny = (target.y - from.y) / (frame.height * rangePercent / 100);
                var distance = Math.sqrt(nx * nx + ny * ny);
                if (distance < minMove || collidesFixed(target)) continue;
                var score = distance + (prev && overlaps(target, prev) ? 0 : 0.03);
                if (prev && sameRect(target, prev)) {
                    if (score > fallbackScore) { previousFallback = target; fallbackScore = score; }
                } else if (score > bestScore) { best = target; bestScore = score; }
            }
            if (stopped) break;
            var edges = edgeTargets(from, frame, rangePercent / 100, magnitude, fixed, w.integerPosition===true);
            for (var ei = 0; ei < edges.length; ei++) {
                reason = halted(); if (reason) { stopped = true; break; }
                evaluated++;
                var edge = edges[ei];
                if (!safeCandidate(edge, from, frame, rangePercent / 100) || collidesFixed(edge)) continue;
                var ex = (edge.x - from.x) / (frame.width * rangePercent / 100), ey = (edge.y - from.y) / (frame.height * rangePercent / 100);
                var ed = Math.sqrt(ex * ex + ey * ey);
                if (ed < minMove) continue;
                var es = ed + (prev && overlaps(edge, prev) ? 0 : 0.03);
                if (prev && sameRect(edge, prev)) {
                    if (es > fallbackScore) { previousFallback = edge; fallbackScore = es; }
                } else if (es > bestScore) { best = edge; bestScore = es; }
            }
            if (stopped) break;
            preferredMinimum *= 0.5;
        }
        if (stopped) { trace.push({ identity: w.identity, status: "skipped", reason: reason }); break; }
        if (!best) best = previousFallback;
        if (!best) trace.push({ identity: w.identity, status: "skipped", reason: "no_safe_destination" });
        else {
            var move = { identity: w.identity, fromRect: copy(from), toRect: copy(best) };
            moves.push(move); staged.push(move);
            trace.push({ identity: w.identity, status: "proposed", fromRect: copy(from), toRect: copy(best), reason: "safe_destination" });
        }
    }
    if (!stopped && options.shouldCancel && options.shouldCancel()) { stopped = true; reason = "cancelled"; }
    if (!stopped && now() >= deadline) { stopped = true; reason = "deadline_exceeded"; }
    if (stopped) {
        moves = [];
        trace.forEach(function(item) { if (item.status === "proposed") { item.status = "skipped"; item.reason = reason; delete item.fromRect; delete item.toRect; } });
    }
    for (; i < order.length; i++) trace.push({ identity: order[i].identity, status: "skipped", reason: stopped ? reason : "maximum_windows_reached" });
    var plan = Model.createPlan({ snapshotRevision: snapshot.topologyRevision, outputIds: [outputId], moves: moves,
        skipReasons: trace.filter(function(item) { return item.status === "skipped"; }).map(function(item) { return { identity: item.identity, reason: item.reason }; }),
        createdAt: finite(start) ? start : null, metadata: { mode: "ordinary_shift", allowTransientOverlap:false, shiftRangePercent: rangePercent,
            maximumWindows: maxWindows, evaluationLimit: maxEval, evaluations: evaluated, complete: !stopped, stopReason: stopped ? reason : null } });
    return { plan: plan, trace: trace, stats: { candidates: candidates.length, moved: moves.length,
        evaluations: evaluated, evaluationLimit: maxEval, complete: !stopped, stopReason: stopped ? reason : null } };
}

function validateFinalPlan(snapshot, registryState, plan) {
    return validateFinalPlanForMode(snapshot, registryState, plan, "ordinary_shift");
}

function validateSwapFinalPlan(snapshot, registryState, plan) {
    return validateFinalPlanForMode(snapshot, registryState, plan, "swap");
}

function validateCoordinatedFinalPlan(snapshot, registryState, plan) {
    return validateFinalPlanForMode(snapshot, registryState, plan, "coordinated_shift");
}

function validateGroupCommonFinalPlan(snapshot, registryState, plan) {
    var mode = plan && plan.metadata && plan.metadata.mode;
    if (mode !== "group-drift" && mode !== "group-rotation")
        return { valid:false, errors:["invalid group mode"] };
    return validateFinalPlanForMode(snapshot, registryState, plan, mode);
}

function validateRestoreFinalPlan(snapshot, registryState, plan) {
    if (!plan || !plan.metadata || plan.metadata.restore !== true ||
            ["ordinary_shift","swap","coordinated_shift","group-drift","group-rotation"].indexOf(plan.metadata.sourceMode) < 0)
        return { valid:false, errors:["invalid restore provenance"] };
    return validateFinalPlanForMode(snapshot, registryState, plan, "restore");
}

// Shared strict final-layout validator. Mode-specific rules are checked here
// after the same eligibility, source, containment and collision checks.
function validateFinalPlanForMode(snapshot, registryState, plan, mode) {
    var errors = Model.validateSnapshot(snapshot).errors.slice(), planCheck = Model.validatePlan(plan);
    planCheck.errors.forEach(function(e) { errors.push("plan: " + e); });
    if (errors.length) return { valid: false, errors: errors };
    if (!focusResolved(snapshot)) errors.push("focused window is absent or invalid");
    if (!registryMatches(snapshot, registryState)) errors.push("snapshot and registry mismatch");
    if (!plan || plan.snapshotRevision !== snapshot.topologyRevision) errors.push("snapshot revision mismatch");
    if (!plan || !plan.metadata || plan.metadata.mode !== mode || plan.metadata.complete !== true) errors.push("plan is incomplete or has invalid mode");
    if (!plan || !Array.isArray(plan.outputIds) || plan.outputIds.length !== 1) errors.push("plan must name exactly one output");
    if (errors.length) return { valid: false, errors: errors };
    var output = null;
    snapshot.outputs.forEach(function(o) { if (o.id === plan.outputIds[0]) output = o; });
    var range = plan.metadata.shiftRangePercent, maximumWindows = plan.metadata.maximumWindows;
    if (!output || ((mode === "ordinary_shift" || mode === "coordinated_shift") &&
            (!finite(range) || range < 1 || range > 100))) errors.push("invalid output or shift range");
    if (!Array.isArray(registryState.selectedOutputIds) || registryState.selectedOutputIds.indexOf(plan.outputIds[0]) < 0)
        errors.push("output is not selected");
    if (!finite(maximumWindows) || Math.floor(maximumWindows) !== maximumWindows || maximumWindows < 1 || maximumWindows > 12 ||
            plan.moves.length > maximumWindows) errors.push("invalid movement budget");
    if ((mode === "swap" || mode === "coordinated_shift") && plan.moves.length < 2)
        errors.push("coordinated movement needs at least two moves");
    if (errors.length) return { valid: false, errors: errors };
    var windowById = Object.create(null), registryById = Object.create(null), moved = Object.create(null), obstacles = [];
    snapshot.windows.forEach(function(w) { windowById[w.identity] = w; if (rawObstacle(w)) obstacles.push(w); });
    registryState.windows.forEach(function(w) { registryById[w.identity] = w; });
    for (var i = 0; i < plan.moves.length; i++) {
        var move = plan.moves[i], w = move && windowById[move.identity], rw = move && registryById[move.identity];
        if (!w || !rw) { errors.push("move references unknown identity"); continue; }
        if (moved[move.identity]) errors.push("duplicate moved identity"); moved[move.identity] = true;
        if (!sameRect(move.fromRect, w.rect) || !sameRect(rw.rect, w.rect)) errors.push("move source frame mismatch");
        if (!eligible(w, snapshot, output, rw)) errors.push("move identity is not eligible");
        if (sameRect(move.fromRect, move.toRect)) errors.push("move has no displacement");
        for (var sourceIndex = 0; sourceIndex < obstacles.length; sourceIndex++)
            if (obstacles[sourceIndex].identity !== move.identity && Model.movementCollision(snapshot,null,move.identity,move.fromRect,obstacles[sourceIndex].identity,obstacles[sourceIndex].rect))
                errors.push("move source already overlaps an obstacle");
        var sameSize = move.toRect && move.fromRect && move.fromRect.width === move.toRect.width && move.fromRect.height === move.toRect.height;
        var resizeMode=mode==="restore"?plan.metadata.sourceMode:mode;
        var resizeLimit=resizeMode==="swap"?2:resizeMode==="group-rotation" && finite(plan.metadata.padding) &&
            plan.metadata.padding>=0 && plan.metadata.padding<=200?Math.min(32,2*plan.metadata.padding+2):0;
        var shrink = !sameSize && move.permitsRoundingResize===true &&
            Model.roundingResizeAllowed(move.fromRect,move.toRect,w.resizable,resizeLimit,w.minimumSize);
        var restoreExpansion = mode==="restore" && !sameSize &&
            (resizeMode==="group-rotation" || move.permitsRoundingResize===true) &&
            Model.roundingResizeAllowed(move.toRect,move.fromRect,w.resizable,resizeLimit,w.minimumSize);
        if (mode==="restore") shrink=false;
        if (!validRect(move.toRect) || (!sameSize && !shrink && !restoreExpansion)) errors.push("move changes size or has invalid geometry");
        else {
            if (w.integerPosition===true && (move.toRect.x!==Math.round(move.toRect.x) || move.toRect.y!==Math.round(move.toRect.y)))
                errors.push("destination cannot be represented by XWayland");
            if (!contained(output.usableRect, move.toRect)) errors.push("destination outside usable frame");
            if (mode === "ordinary_shift" || mode === "coordinated_shift") {
                var dx = (move.toRect.x - move.fromRect.x) / (output.usableRect.width * range / 100);
                var dy = (move.toRect.y - move.fromRect.y) / (output.usableRect.height * range / 100);
                if (dx * dx + dy * dy > 1.000000001) errors.push("destination exceeds normalized range");
            }
        }
    }
    for (i = 0; i < plan.moves.length; i++) {
        var m = plan.moves[i]; if (!m || !validRect(m.toRect)) continue;
        for (var oi = 0; oi < obstacles.length; oi++) if (obstacles[oi].identity !== m.identity) {
            var other = moved[obstacles[oi].identity] ? null : obstacles[oi].rect;
            if (other && Model.movementCollision(snapshot,plan,m.identity,m.toRect,obstacles[oi].identity,other)) errors.push("destination collides with stationary obstacle");
        }
        for (var j = i + 1; j < plan.moves.length; j++) if (plan.moves[j] && Model.movementCollision(snapshot,plan,m.identity,m.toRect,plan.moves[j].identity,plan.moves[j].toRect)) errors.push("planned destinations collide");
    }
    if (mode === "coordinated_shift") {
        var xEnabled = plan.metadata.shiftHorizontalOrder === true, yEnabled = plan.metadata.shiftVerticalOrder === true;
        if ((!xEnabled && !yEnabled) || plan.metadata.allowTransientOverlap !== true) errors.push("invalid coordinated order policy");
        var largeMove = false, reversedX = false, reversedY = false;
        for (i = 0; i < plan.moves.length; i++) {
            m = plan.moves[i];
            var mx = (m.toRect.x - m.fromRect.x) / (output.usableRect.width * range / 100);
            var my = (m.toRect.y - m.fromRect.y) / (output.usableRect.height * range / 100);
            if (Math.sqrt(mx * mx + my * my) >= .4 - 1e-9) largeMove = true;
            for (j = i + 1; j < plan.moves.length; j++) {
                var n = plan.moves[j], ax = m.fromRect.x + m.fromRect.width / 2 - n.fromRect.x - n.fromRect.width / 2;
                var bx = m.toRect.x + m.toRect.width / 2 - n.toRect.x - n.toRect.width / 2;
                var ay = m.fromRect.y + m.fromRect.height / 2 - n.fromRect.y - n.fromRect.height / 2;
                var by = m.toRect.y + m.toRect.height / 2 - n.toRect.y - n.toRect.height / 2;
                if (ax * bx < -1) { if (!xEnabled) errors.push("horizontal order changed while disabled"); reversedX = true; }
                if (ay * by < -1) { if (!yEnabled) errors.push("vertical order changed while disabled"); reversedY = true; }
            }
        }
        if (!largeMove || !(xEnabled && reversedX || yEnabled && reversedY)) errors.push("coordinated order or magnitude requirement missing");
    }
    return { valid: errors.length === 0, errors: errors };
}

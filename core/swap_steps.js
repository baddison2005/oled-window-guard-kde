.pragma library
.import "model.js" as Model
.import "planner.js" as Planner
.import "groups.js" as Groups
.import "group_safety.js" as GroupSafety

// Pure step ordering for a previously validated final swap layout. No writes.
var DEFAULT_STEP_EVALUATIONS = 2400;
var HARD_STEP_EVALUATIONS = 50000;

function stepCopy(value) { return Model.copyPlain(value); }
function stepSame(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function stepObstacle(w) { return Planner.rawObstacle(w); }
function stepKey(current, ids) {
    return ids.map(function(id) { var r = current[id]; return id + ":" + [r.x, r.y, r.width, r.height].join(","); }).join("|");
}
function stepCollides(rect, current, identity, snapshot, plan) {
    for (var key in current) if (Object.prototype.hasOwnProperty.call(current, key) && key !== identity && stepOverlapsNearby(rect, current[key]) && (!snapshot || Model.movementCollision(snapshot,plan,identity,rect,key,current[key]))) return true;
    return false;
}
// Frames reaching step search have passed finalGeometryCheck. Keep the common
// strict overlap test for intersecting envelopes, avoiding its rectangle
// validation/copy cost for distant frames in crowded snapshots.
function stepOverlapsNearby(a, b) {
    return a.x < b.x + b.width && b.x < a.x + a.width &&
        a.y < b.y + b.height && b.y < a.y + a.height && Model.rectangleOverlaps(a, b);
}
function stepUniqueCoordinates(values) {
    var seen = Object.create(null), result = [];
    values.forEach(function(value) {
        var key = String(value);
        if (!seen[key]) { seen[key] = true; result.push(value); }
    });
    return result;
}
function finalGeometryCheck(snapshot, registryState, plan, context) {
    if (plan && plan.metadata && plan.metadata.mode === "ordinary_shift")
        return Planner.validateFinalPlan(snapshot, registryState, plan);
    if (plan && plan.metadata && plan.metadata.mode === "restore")
        return Planner.validateRestoreFinalPlan(snapshot, registryState, plan);
    if (plan && plan.metadata && (plan.metadata.mode === "group-drift" || plan.metadata.mode === "group-rotation"))
        return GroupSafety.validateGroupFinalPlan(snapshot, registryState, plan, context && context.group, context && context.library);
    if (plan && plan.metadata && plan.metadata.mode === "coordinated_shift")
        return Planner.validateCoordinatedFinalPlan(snapshot, registryState, plan);
    return Planner.validateSwapFinalPlan(snapshot, registryState, plan);
}
// Ordinary drift avoids every original frame and every earlier destination.
// Replay the planner's order directly: no staging or extra displacements.
function orderOrdinarySteps(snapshot, registryState, plan) {
    var steps=plan.moves.map(function(move) { return {identity:move.identity,
        fromRect:stepCopy(move.fromRect),toRect:stepCopy(move.toRect),kind:"place"}; });
    return validateSwapSteps(snapshot,registryState,plan,steps).valid ? steps : [];
}
function stepCandidates(from, area, current, finalRects, shouldStop, integral) {
    var xs = [area.x, area.x + area.width - from.width], ys = [area.y, area.y + area.height - from.height];
    function edges(r) {
        xs.push(r.x - from.width, r.x + r.width);
        ys.push(r.y - from.height, r.y + r.height);
    }
    for (var id in current) if (Object.prototype.hasOwnProperty.call(current, id)) edges(current[id]);
    for (id in finalRects) if (Object.prototype.hasOwnProperty.call(finalRects, id)) edges(finalRects[id]);
    xs = stepUniqueCoordinates(xs); ys = stepUniqueCoordinates(ys);
    var result = [];
    for (var i = 0; i < xs.length && result.length < 64; i++) for (var j = 0; j < ys.length && result.length < 64; j++) {
        if (shouldStop && shouldStop()) return [];
        var x = integral?Math.round(xs[i]):xs[i], y = integral?Math.round(ys[j]):ys[j];
        var rect = { x:x, y:y, width:from.width, height:from.height };
        if (!Model.rectangleContains(area, rect)) continue;
        var occupied = false;
        for (var currentId in current) if (Object.prototype.hasOwnProperty.call(current, currentId) && stepOverlapsNearby(rect, current[currentId])) { occupied = true; break; }
        if (!occupied) for (var finalId in finalRects) if (Object.prototype.hasOwnProperty.call(finalRects, finalId) && stepOverlapsNearby(rect, finalRects[finalId])) { occupied = true; break; }
        if (!occupied) result.push(rect);
    }
    return result;
}
function validateSwapSteps(snapshot, registryState, plan, steps, context) {
    var errors = finalGeometryCheck(snapshot, registryState, plan, context).errors.slice();
    if (!Array.isArray(steps) || steps.length > 36) errors.push("invalid step list");
    if (errors.length) return { valid:false, errors:errors };
    var area = snapshot.outputs.filter(function(o) { return o.id === plan.outputIds[0]; })[0].usableRect;
    var current = Object.create(null), moved = Object.create(null), finalRects = Object.create(null);
    snapshot.windows.forEach(function(w) { if (stepObstacle(w)) current[w.identity] = stepCopy(w.rect); });
    plan.moves.forEach(function(move) { moved[move.identity] = true; finalRects[move.identity] = move.toRect; });
    if (plan.metadata.allowTransientOverlap !== true && plan.metadata.allowTransientOverlap !== false)
        errors.push("transient overlap policy must be explicit");
    if (plan.metadata.mode === "ordinary_shift") {
        if (plan.metadata.allowTransientOverlap !== false || steps.length !== plan.moves.length)
            errors.push("ordinary drift requires one non-overlapping placement per window");
        else steps.forEach(function(step,index) {
            var move=plan.moves[index];
            if (!step || step.identity!==move.identity || step.kind!=="place" ||
                    !stepSame(step.fromRect,move.fromRect) || !stepSame(step.toRect,move.toRect))
                errors.push("ordinary drift steps differ from planned placements");
        });
    }
    for (var i = 0; i < steps.length && !errors.length; i++) {
        var step = steps[i];
        if (!step || !moved[step.identity] || !Model.rectangle(step.fromRect) || !Model.rectangle(step.toRect) ||
                (step.kind !== "stage" && step.kind !== "place" && step.kind !== "resize" && step.kind !== "reverse")) { errors.push("invalid step"); break; }
        if (!stepSame(current[step.identity], step.fromRect)) { errors.push("step source does not match current frame"); break; }
        var resizing = step.fromRect.width !== step.toRect.width || step.fromRect.height !== step.toRect.height;
        var liveWindow = snapshot.windows.filter(function(w) { return w.identity === step.identity; })[0];
        if (liveWindow.integerPosition===true && (step.toRect.x!==Math.round(step.toRect.x) || step.toRect.y!==Math.round(step.toRect.y))) {
            errors.push("step cannot be represented by XWayland"); break;
        }
        var resizeMode=plan.metadata.mode==="restore"?plan.metadata.sourceMode:plan.metadata.mode;
        var declared=plan.moves.some(function(m){return m.identity===step.identity && m.permitsRoundingResize===true;});
        var resizeLimit=resizeMode==="swap"?2:resizeMode==="group-rotation"?Math.min(32,2*plan.metadata.padding+2):0;
        var restoreExpansion = plan.metadata.mode === "restore" && (resizeMode==="group-rotation" || declared) &&
            step.kind === "reverse" && step.fromRect.x === step.toRect.x && step.fromRect.y === step.toRect.y &&
            Model.roundingResizeAllowed(step.toRect,step.fromRect,liveWindow.resizable,resizeLimit,liveWindow.minimumSize);
        if (resizing && !restoreExpansion && ((plan.metadata.mode!=="group-rotation" && !(plan.metadata.mode==="swap" && declared)) || step.kind !== "resize" ||
                step.fromRect.x !== step.toRect.x || step.fromRect.y !== step.toRect.y ||
                !Model.roundingResizeAllowed(step.fromRect,step.toRect,liveWindow.resizable,resizeLimit,liveWindow.minimumSize))) {
            errors.push("unsupported or excessive resize step"); break;
        }
        var declaredMove=plan.moves.filter(function(m){return m.identity===step.identity;})[0];
        if (resizing && (step.toRect.width<Math.min(declaredMove.fromRect.width,declaredMove.toRect.width) ||
                step.toRect.width>Math.max(declaredMove.fromRect.width,declaredMove.toRect.width) ||
                step.toRect.height<Math.min(declaredMove.fromRect.height,declaredMove.toRect.height) ||
                step.toRect.height>Math.max(declaredMove.fromRect.height,declaredMove.toRect.height))) {
            errors.push("resize leaves declared size envelope"); break;
        }
        if ((!resizing && step.kind === "resize") || !Model.rectangleContains(area, step.toRect)) {
            errors.push("step size or usable area is invalid"); break;
        }
        if (plan.metadata.allowTransientOverlap !== true && stepCollides(step.toRect, current, step.identity, snapshot, plan)) {
            errors.push("intermediate step overlaps another window"); break;
        }
        current[step.identity] = stepCopy(step.toRect);
    }
    plan.moves.forEach(function(move) { if (!stepSame(current[move.identity], move.toRect)) errors.push("steps do not reach final layout"); });
    return { valid:errors.length === 0, errors:errors };
}
function orderSwapSteps(snapshot, registryState, plan, options) {
    options = options || {};
    var max = options.maxEvaluations === undefined ? DEFAULT_STEP_EVALUATIONS : options.maxEvaluations;
    var now = options.now || function() { return Date.now(); }, deadlineMs = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    if (typeof max !== "number" || !isFinite(max) || max < 1 || Math.floor(max) !== max ||
            typeof now !== "function" || typeof deadlineMs !== "number" || !isFinite(deadlineMs) || deadlineMs < 0 ||
            (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function")) throw new Error("invalid step-search options");
    max = Math.min(max, HARD_STEP_EVALUATIONS);
    var start = now(), deadline = start + deadlineMs;
    if (typeof start !== "number" || !isFinite(start) || !isFinite(deadline)) throw new Error("invalid step-search clock");
    if (options.shouldCancel && options.shouldCancel())
        return { steps:[], reason:"cancelled", errors:[], stats:{ evaluated:0 } };
    if (now() >= deadline)
        return { steps:[], reason:"deadline_exceeded", errors:[], stats:{ evaluated:0 } };
    var checked = finalGeometryCheck(snapshot, registryState, plan, options);
    if (!checked.valid) return { steps:[], reason:"invalid-final-plan", errors:checked.errors, stats:{ evaluated:0 } };
    if (now() >= deadline)
        return { steps:[], reason:"deadline_exceeded", errors:[], stats:{ evaluated:0 } };
    var area = snapshot.outputs.filter(function(o) { return o.id === plan.outputIds[0]; })[0].usableRect;
    var ids = plan.moves.map(function(m) { return m.identity; }), finalRects = Object.create(null), current = Object.create(null);
    var destinations=Object.create(null);
    plan.moves.forEach(function(move) {
        destinations[move.identity]=stepCopy(move.toRect);
        finalRects[move.identity]={x:move.toRect.x,y:move.toRect.y,width:Math.min(move.fromRect.width,move.toRect.width),height:Math.min(move.fromRect.height,move.toRect.height)};
    });
    snapshot.windows.forEach(function(w) { if (stepObstacle(w)) current[w.identity] = stepCopy(w.rect); });
    var steps = [], evaluated = 0, visited = Object.create(null), stopped = null;
    function halt() {
        if (options.shouldCancel && options.shouldCancel()) return "cancelled";
        if (evaluated >= max) return "evaluation_budget_exhausted";
        if (now() >= deadline) return "deadline_exceeded";
        return null;
    }
    function makeStep(identity, destination, kind) {
        return { identity:identity, fromRect:stepCopy(current[identity]), toRect:stepCopy(destination), kind:kind };
    }
    function search(stagedCount) {
        var h = halt(); if (h) { stopped = h; return false; }
        if (ids.every(function(id) { return stepSame(current[id], finalRects[id]); })) return true;
        var key = stepKey(current, ids);
        if (visited[key]) return false; visited[key] = true;
        // Prefer direct legal placements. A stage is considered only when no
        // direct placement can advance the current state.
        for (var i = 0; i < ids.length; i++) {
            var id = ids[i], target = finalRects[id];
            if (stepSame(current[id], target) || stepCollides(target, current, id, snapshot, plan)) continue;
            evaluated++;
            var step = makeStep(id, target, "place"), before = current[id];
            current[id] = target; steps.push(step);
            if (search(stagedCount)) return true;
            steps.pop(); current[id] = before;
            if (stopped) return false;
        }
        if (stagedCount >= 2) return false;
        for (i = 0; i < ids.length; i++) {
            id = ids[i];
            if (stepSame(current[id], finalRects[id])) continue;
            var integral=snapshot.windows.some(function(w){return w.identity===id && w.integerPosition===true;});
            var candidates = stepCandidates(current[id], area, current, finalRects, function() {
                var reason = halt();
                if (reason) stopped = reason;
                return !!reason;
            }, integral);
            if (stopped) return false;
            for (var j = 0; j < candidates.length; j++) {
                h = halt(); if (h) { stopped = h; return false; }
                evaluated++;
                step = makeStep(id, candidates[j], "stage"); before = current[id];
                current[id] = candidates[j]; steps.push(step);
                if (search(stagedCount + 1)) return true;
                steps.pop(); current[id] = before;
                if (stopped) return false;
            }
        }
        return false;
    }
    // Reduce in place before either placement or strict staging. This retains
    // the same collision policy for routes with brief overlap disabled.
    ids.forEach(function(id) {
        var from=current[id],target=finalRects[id];
        if (from.width!==target.width || from.height!==target.height) {
            var reduced={x:from.x,y:from.y,width:target.width,height:target.height};
            steps.push(makeStep(id,reduced,"resize"));current[id]=reduced;
        }
    });
    if (plan.metadata.allowTransientOverlap === true) {
        ids.forEach(function(id) {
            var from = current[id], target = finalRects[id];
            var step = makeStep(id, target, "place"); current[id] = target; steps.push(step);
        });
    } else if (plan.metadata.allowTransientOverlap === false) {
        if (!search(0) && !stopped) return {steps:[],reason:"no-safe-step-order",errors:[],stats:{evaluated:evaluated}};
    }
    else return { steps:[], reason:"overlap-policy-missing", errors:[], stats:{ evaluated:0 } };
    // Expand only after every participant has vacated its old slot. Strict
    // routing therefore never grows into a neighbour that is still waiting.
    ids.forEach(function(id) {
        if (!stepSame(current[id],destinations[id])) {
            steps.push(makeStep(id,destinations[id],"resize"));current[id]=destinations[id];
        }
    });
    if (!stopped) stopped = halt();
    if (stopped || !steps.length) return { steps:[], reason:stopped || "no-safe-step-order", errors:[], stats:{ evaluated:evaluated } };
    var verified = validateSwapSteps(snapshot, registryState, plan, steps, options);
    if (!verified.valid) return { steps:[], reason:"step-validation-failed", errors:verified.errors, stats:{ evaluated:evaluated } };
    stopped = halt();
    if (stopped) return { steps:[], reason:stopped, errors:[], stats:{ evaluated:evaluated } };
    return { steps:steps, reason:null, errors:[], stats:{ evaluated:evaluated } };
}

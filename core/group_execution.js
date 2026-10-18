.pragma library
.import "model.js" as Model
.import "planner.js" as Planner
.import "groups.js" as Groups
.import "group_rotation.js" as Rotation
.import "group_safety.js" as GroupSafety
.import "swap_steps.js" as Steps

// Shared group entry point: both drift and rotation return a validated plan
// and ordered steps for the same inert transaction reducer.
function planGroupMovement(snapshot, registryState, options) {
    options = options || {};
    if (!Model.validateSnapshot(snapshot).valid || !Planner.registryMatches(snapshot, registryState) ||
            !Planner.focusResolved(snapshot)) throw new Error("snapshot and registry mismatch");
    var output = snapshot.outputs.filter(function(o) { return o.id === options.outputId; })[0];
    var group = options.group, library = options.library;
    if (!output || !registryState.selectedOutputIds || registryState.selectedOutputIds.indexOf(output.id) < 0 ||
            !group || typeof group.id !== "string" || !Array.isArray(group.zones) ||
            (group.source !== "guard" && group.source !== "window-layouts-kde") ||
            typeof options.rotateGroup !== "boolean") throw new Error("invalid group movement options");
    if (group.source === "window-layouts-kde" && !Groups.groupsFromLibrary(library).some(function(g) {
        return g.id === group.id && g.revision === group.revision && g.padding === group.padding &&
            JSON.stringify(g.zones) === JSON.stringify(group.zones);
    })) return { plan:null, steps:[], reason:"custom-library-unavailable-or-changed", stats:{} };
    var plan, trace = [], stats = {}, proposal;
    if (options.rotateGroup) {
        var registered = Object.create(null), candidates = [], candidateIds = Object.create(null);
        registryState.windows.forEach(function(w) { registered[w.identity] = w; });
        snapshot.windows.forEach(function(w) { if (Planner.eligible(w,snapshot,output,registered[w.identity])) {
            candidates.push({ identity:w.identity, rect:Model.copyPlain(w.rect), resizable:w.resizable === true, integerPosition:w.integerPosition===true, minimumSize:w.minimumSize });
        } });
        candidates = candidates.slice(0,12);
        candidates.forEach(function(w) { candidateIds[w.identity] = true; });
        var obstacles = snapshot.windows.filter(function(w) { return Planner.rawObstacle(w) && !candidateIds[w.identity]; })
            .map(function(w) { return Model.copyPlain(w.rect); });
        proposal = Rotation.proposeGroupRotation(candidates, Groups.groupFrames(group,output.usableRect),
            output.usableRect, obstacles, { padding:group.padding, sizeTolerance:options.sizeTolerance,
                maximumWindows:options.maximumWindows, random:options.random, now:options.now,
                deadlineMs:options.deadlineMs, maxEvaluations:options.maxEvaluations,
                shouldCancel:options.shouldCancel });
        stats = proposal.stats;
        if (!proposal.moves.length || proposal.reason === "search_stopped")
            return { plan:null, steps:[], reason:proposal.reason, stats:stats };
        plan = Model.createPlan({ snapshotRevision:snapshot.topologyRevision, outputIds:[output.id], moves:proposal.moves,
            metadata:{ mode:"group-rotation", complete:true, maximumWindows:options.maximumWindows,
                groupId:group.id, source:group.source, padding:group.padding,
                libraryRevision:group.revision || null, allowTransientOverlap:true } });
    } else {
        proposal = Groups.planZoneDrift(snapshot,registryState,options);
        stats = proposal.stats; trace = proposal.trace;
        plan = proposal.plan;
        plan.metadata.allowTransientOverlap = false;
        if (!plan.moves.length || plan.metadata.complete !== true)
            return { plan:null, steps:[], reason:stats.stopped || "no-zone-drift", stats:stats, trace:trace };
    }
    var checked = GroupSafety.validateGroupFinalPlan(snapshot,registryState,plan,group,library);
    if (!checked.valid) return { plan:null, steps:[], reason:"invalid-group-final-layout", errors:checked.errors, stats:stats, trace:trace };
    var ordered = Steps.orderSwapSteps(snapshot,registryState,plan,{ group:group, library:library,
        now:options.now, deadlineMs:options.deadlineMs, maxEvaluations:options.maxEvaluations,
        shouldCancel:options.shouldCancel });
    if (!ordered.steps.length) return { plan:null, steps:[], reason:ordered.reason, errors:ordered.errors, stats:stats, trace:trace };
    return { plan:plan, steps:ordered.steps, reason:options.rotateGroup ? "group-rotation" : "group-drift",
        stats:stats, trace:trace };
}

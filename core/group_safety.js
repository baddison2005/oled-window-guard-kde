.pragma library
.import "model.js" as Model
.import "planner.js" as Planner
.import "groups.js" as Groups

// One strict group-specific layer on top of the common final-layout checker.
function groupSame(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function groupFinite(n) { return typeof n === "number" && isFinite(n); }
function groupFind(snapshot, identity) {
    for (var i = 0; i < snapshot.windows.length; i++) if (snapshot.windows[i].identity === identity) return snapshot.windows[i];
    return null;
}
function validateGroupFinalPlan(snapshot, registryState, plan, group, library) {
    var common = Planner.validateGroupCommonFinalPlan(snapshot, registryState, plan), errors = common.errors.slice();
    if (errors.length) return { valid:false, errors:errors };
    if (!group || typeof group.id !== "string" || !group.id ||
            (group.source !== "guard" && group.source !== "window-layouts-kde") ||
            !groupFinite(group.padding) || group.padding < 0 || group.padding > 200 ||
            !Array.isArray(group.zones) || !group.zones.length ||
            plan.metadata.groupId !== group.id || plan.metadata.source !== group.source ||
            plan.metadata.padding !== group.padding)
        return { valid:false, errors:["group identity or geometry is invalid"] };
    if (group.source === "window-layouts-kde") {
        var current = Groups.groupsFromLibrary(library).filter(function(g) { return g.id === group.id; })[0];
        if (!current || !group.revision || group.revision !== library.revision ||
                plan.metadata.libraryRevision !== library.revision ||
                current.padding !== group.padding ||
                JSON.stringify(current.zones) !== JSON.stringify(group.zones))
            return { valid:false, errors:["custom group library is missing, changed, or unstable"] };
    } else {
        var builtin = Groups.builtinGroups(group.padding).filter(function(g) { return g.id === group.id; })[0];
        if (!builtin || JSON.stringify(builtin.zones) !== JSON.stringify(group.zones))
            errors.push("built-in group geometry does not match the catalog");
        if (plan.metadata.libraryRevision !== null) errors.push("built-in group cannot use a library revision");
    }
    var output = snapshot.outputs.filter(function(o) { return o.id === plan.outputIds[0]; })[0];
    var frames = Groups.groupFrames(group, output.usableRect);
    if (frames.length !== group.zones.length) errors.push("invalid or collapsed group zone");
    if (errors.length) return { valid:false, errors:errors };
    for (var i = 0; i < plan.moves.length; i++) {
        var move = plan.moves[i], window = groupFind(snapshot, move.identity);
        if (plan.metadata.mode === "group-drift") {
            var sources = frames.filter(function(frame) { return Model.rectangleContains(frame, move.fromRect); });
            if (sources.length !== 1) { errors.push("drift requires exactly one strict source zone"); continue; }
            if (!groupSame({x:0,y:0,width:move.fromRect.width,height:move.fromRect.height},
                    {x:0,y:0,width:move.toRect.width,height:move.toRect.height}) ||
                    !Model.rectangleContains(sources[0], move.toRect)) errors.push("drift leaves its source zone or changes size");
            var range = plan.metadata.shiftRangePercent;
            var registered = registryState.windows.filter(function(w) { return w.identity === move.identity; })[0];
            if (!groupFinite(range) || range < 1 || range > 100 || !registered || !Model.rectangle(registered.originFrame))
                errors.push("invalid drift range or anchor");
            else {
                var dx = output.usableRect.width * range / 100, dy = output.usableRect.height * range / 100;
                if (Math.abs(move.toRect.x - move.fromRect.x) > dx + 1e-9 ||
                        Math.abs(move.toRect.y - move.fromRect.y) > dy + 1e-9 ||
                        Math.abs(move.toRect.x - registered.originFrame.x) > dx + 1e-9 ||
                        Math.abs(move.toRect.y - registered.originFrame.y) > dy + 1e-9)
                    errors.push("drift exceeds current or anchor range");
            }
        } else {
            if (!Groups.matchingZones(move.fromRect, frames, group.padding).length)
                errors.push("rotation source has no tolerated zone");
            if (!frames.some(function(frame) { return Model.rectangleContains(frame, move.toRect); }))
                errors.push("rotation destination is outside every strict zone");
            if (move.fromRect.width !== move.toRect.width || move.fromRect.height !== move.toRect.height) {
                if (!Groups.allowedShrink(move.fromRect, move.toRect, group.padding, window.resizable))
                    errors.push("rotation resize exceeds bounded resizable allowance");
            }
        }
    }
    return { valid:errors.length === 0, errors:errors };
}

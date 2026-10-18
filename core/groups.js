.pragma library
.import "model.js" as Model
.import "registry.js" as Registry

// Source-aware layout group geometry and bounded zone drift. This module only
// creates proposals; the transaction layer owns all compositor interaction.
var MEMBERSHIP_EPSILON = 0.000001;
var DEFAULT_EVALUATIONS = 1200;
var HARD_MAX_EVALUATIONS = 50000;

function finite(value) { return typeof value === "number" && isFinite(value); }
function copy(value) { return Model.copyPlain(value); }
function sameRect(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function roundSwift(value) { return value < 0 ? Math.ceil(value - 0.5) : Math.floor(value + 0.5); }
function roundKde(value) { return Math.floor(value + 0.5); }
function sourceAllowance(padding) { return Math.min(32, Math.max(0, finite(padding) ? padding : 0) + 2); }
function resizeLimit(padding) { return Math.min(32, Math.max(0, finite(padding) ? padding : 0) * 2 + 2); }

function validZone(zone) {
    return !!zone && typeof zone.id === "string" && !!zone.id && typeof zone.name === "string" &&
        finite(zone.x) && finite(zone.y) && finite(zone.width) && finite(zone.height) &&
        zone.x >= 0 && zone.y >= 0 && zone.width > 0 && zone.height > 0 &&
        zone.x + zone.width <= 1.000001 && zone.y + zone.height <= 1.000001;
}
function validSource(source) { return source === "guard" || source === "window-layouts-kde"; }
function validPadding(padding) { return finite(padding) && padding >= 0 && padding <= 200; }

function normalizedGroup(id, name, zones, padding, source) {
    if (typeof id !== "string" || !id || typeof name !== "string" || !name ||
            !Array.isArray(zones) || !validPadding(padding) || !validSource(source))
        throw new Error("invalid layout group");
    var seen = Object.create(null), clean = [];
    zones.forEach(function(zone) {
        if (!validZone(zone) || seen[zone.id]) throw new Error("invalid or duplicate layout zone");
        seen[zone.id] = true; clean.push(copy(zone));
    });
    if (!clean.length) throw new Error("layout group must contain zones");
    return { id: id, name: name, zones: clean, padding: padding, source: source };
}

function builtinGroups(padding) {
    if (padding === undefined) padding = 0;
    if (!validPadding(padding)) throw new Error("invalid built-in group padding");
    var halves = [[0,0,0.5,1],[0.5,0,0.5,1],[0,0,1,0.5],[0,0.5,1,0.5]];
    var definitions = [
        ["halves", "Halves", halves],
        ["horizontal-halves", "Horizontal Halves", halves.slice(0, 2)],
        ["vertical-halves", "Vertical Halves", halves.slice(2)],
        ["quarters", "Quarters", [[0,0,.5,.5],[.5,0,.5,.5],[0,.5,.5,.5],[.5,.5,.5,.5]]],
        ["thirds", "Thirds", [[0,0,1/3,1],[1/3,0,1/3,1],[2/3,0,1/3,1]]],
        ["two-thirds", "Two Thirds", [[0,0,2/3,1],[1/6,0,2/3,1],[1/3,0,2/3,1]]]
    ];
    return definitions.map(function(def) {
        return normalizedGroup("builtin." + def[0], def[1], def[2].map(function(r, index) {
            return { id: "builtin." + def[0] + ".zone." + index, name: def[1], x:r[0], y:r[1], width:r[2], height:r[3], groupId:null };
        }), padding, "guard");
    });
}

// The provider's stable IDs remain intact inside zones; only group preference
// IDs are namespaced so a custom group cannot collide with a guard built-in.
function groupsFromLibrary(library) {
    if (!library || library.sourceId !== "kde:window-layouts" || library.status !== "available" ||
            library.executionReady !== true || typeof library.revision !== "string" || !library.revision ||
            !Array.isArray(library.groups) || library.groups.length > 64 ||
            !Array.isArray(library.layouts) || library.layouts.length > 20 || !validPadding(library.padding)) return [];
    var layoutById = Object.create(null), groupIds = Object.create(null), result = [], malformed = false;
    library.layouts.forEach(function(layout) {
        if (!layout || typeof layout.id !== "string" || !layout.id || layoutById[layout.id] || !validZone(layout)) { malformed = true; return; }
        layoutById[layout.id] = layout;
    });
    library.groups.forEach(function(group) {
        if (!group || typeof group.id !== "string" || !group.id || groupIds[group.id] ||
                typeof group.name !== "string" || !group.name || !Array.isArray(group.layoutIds)) {
            malformed = true; return;
        }
        groupIds[group.id] = true;
        var zones = [], seen = Object.create(null);
        group.layoutIds.forEach(function(layoutId) {
            var layout = layoutById[layoutId];
            if (!layout || seen[layoutId] || (layout.groupId !== undefined && layout.groupId !== group.id)) { malformed = true; return; }
            seen[layoutId] = true;
            zones.push({ id:layout.id, name:layout.name, x:layout.x, y:layout.y, width:layout.width, height:layout.height, groupId:group.id });
        });
        if (zones.length) result.push({ id:"kde:" + group.id, sourceId:group.id, name:group.name, zones:zones,
            padding:library.padding, source:"window-layouts-kde", revision:library.revision });
    });
    return malformed ? [] : result;
}

function geometryForZone(zone, usableRect, padding, source) {
    if (!validZone(zone) || !Model.rectangle(usableRect) || !validPadding(padding) || !validSource(source)) return null;
    var x1 = usableRect.x + usableRect.width * zone.x, y1 = usableRect.y + usableRect.height * zone.y;
    var x2 = usableRect.x + usableRect.width * (zone.x + zone.width), y2 = usableRect.y + usableRect.height * (zone.y + zone.height);
    var round = source === "guard" ? roundSwift : roundKde;
    var left = round(x1), top = round(y1), right = round(x2), bottom = round(y2);
    var insetLeft = zone.x > MEMBERSHIP_EPSILON ? padding : 0;
    var insetTop = zone.y > MEMBERSHIP_EPSILON ? padding : 0;
    var insetRight = zone.x + zone.width < 1 - MEMBERSHIP_EPSILON ? padding : 0;
    var insetBottom = zone.y + zone.height < 1 - MEMBERSHIP_EPSILON ? padding : 0;
    if (source === "window-layouts-kde") {
        var horizontal = constrainedInsets(right - left, insetLeft, insetRight);
        var vertical = constrainedInsets(bottom - top, insetTop, insetBottom);
        insetLeft = horizontal.start; insetRight = horizontal.end;
        insetTop = vertical.start; insetBottom = vertical.end;
    }
    var width = right - left - insetLeft - insetRight;
    var height = bottom - top - insetTop - insetBottom;
    // Window Layouts returns a one-unit rect even when both normalized edges
    // round to the same pixel. The guard contract keeps the macOS empty-zone
    // behavior separate.
    if (source === "window-layouts-kde") { width = Math.max(1, width); height = Math.max(1, height); }
    return Model.rectangle({ x:left + insetLeft, y:top + insetTop, width:width, height:height });
}

function constrainedInsets(size, start, end) {
    if (!finite(size) || !finite(start) || !finite(end) || size < 1) return { start:0, end:0 };
    var total = start + end, available = Math.max(0, size - 1);
    if (total > available && total > 0) {
        start = Math.floor(start * available / total);
        return { start:start, end:Math.max(0, available - start) };
    }
    return { start:Math.floor(start), end:Math.floor(end) };
}

function groupFrames(group, usableRect) {
    if (!group || !validSource(group.source) || !Array.isArray(group.zones) || !validPadding(group.padding)) return [];
    return group.zones.map(function(zone) { return geometryForZone(zone, usableRect, group.padding, group.source); })
        .filter(function(frame) { return !!frame; });
}

function matchingZones(windowRect, zones, padding) {
    var window = Model.rectangle(windowRect), allowance = sourceAllowance(padding);
    if (!window || !Array.isArray(zones)) return [];
    return zones.filter(function(zone) {
        var r = Model.rectangle(zone);
        return r && window.x >= r.x - allowance && window.y >= r.y - allowance &&
            window.x + window.width <= r.x + r.width + allowance &&
            window.y + window.height <= r.y + r.height + allowance;
    });
}

function containingZones(windowRect, zones) {
    if (!Model.rectangle(windowRect) || !Array.isArray(zones)) return [];
    return zones.filter(function(zone) { return Model.rectangleContains(zone, windowRect); });
}

function allowedShrink(fromRect, toRect, padding, resizable) {
    var from = Model.rectangle(fromRect), to = Model.rectangle(toRect);
    if (!from || !to || resizable !== true || !validPadding(padding)) return false;
    var max = resizeLimit(padding), dw = from.width - to.width, dh = from.height - to.height;
    return (dw !== 0 || dh !== 0) && dw >= -2 && dh >= -2 && dw <= max && dh <= max;
}

function planZoneDrift(snapshot, registryState, options) {
    options = options || {};
    var snapshotCheck = Model.validateSnapshot(snapshot);
    if (!snapshotCheck.valid || !Array.isArray(registryState && registryState.windows) ||
            registryState.topologyRevision !== snapshot.topologyRevision ||
            registryState.snapshotSignature !== Registry.snapshotSignature(snapshot))
        throw new Error("snapshot and registry mismatch");
    if (snapshot.focusedIdentity !== null &&
            !snapshot.windows.some(function(w) { return w.identity === snapshot.focusedIdentity; }))
        throw new Error("unresolved focus");
    if (registryState.windows.length !== snapshot.windows.length ||
            registryState.windows.some(function(rw) { return !snapshot.windows.some(function(sw) {
                return sw.identity === rw.identity && sameRect(sw.rect, rw.rect) && sameRect(sw.rect, rw.lastFrame);
            }); })) throw new Error("snapshot and registry window mismatch");
    var outputId = options.outputId, group = options.group, rangePercent = options.shiftRangePercent;
    var maximumWindows = options.maximumWindows;
    if (typeof outputId !== "string" || !outputId || !group || !validSource(group.source) ||
            !validPadding(group.padding) || !finite(rangePercent) || rangePercent < 1 || rangePercent > 100 ||
            !finite(maximumWindows) || maximumWindows < 1 || maximumWindows > 12 || Math.floor(maximumWindows) !== maximumWindows)
        throw new Error("invalid zone drift options");
    var output = snapshot.outputs.filter(function(o) { return o.id === outputId; })[0];
    if (!output || !Array.isArray(registryState.selectedOutputIds) || registryState.selectedOutputIds.indexOf(outputId) < 0)
        throw new Error("outputId is unknown or not selected");
    var maxEvaluations = options.maxEvaluations === undefined ? DEFAULT_EVALUATIONS : options.maxEvaluations;
    if (!finite(maxEvaluations) || maxEvaluations < 1 || Math.floor(maxEvaluations) !== maxEvaluations) throw new Error("invalid evaluation budget");
    maxEvaluations = Math.min(maxEvaluations, HARD_MAX_EVALUATIONS);
    var deadlineMs = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    var now = options.now || function() { return Date.now(); }, random = options.random || Math.random;
    if (!finite(deadlineMs) || deadlineMs < 0 || typeof now !== "function" || typeof random !== "function" ||
            (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function")) throw new Error("invalid planner callback");
    var started = now(), deadline = started + deadlineMs, evaluations = 0;
    if (!finite(started) || !finite(deadline)) throw new Error("planner clock must be finite");
    var zoneRects = groupFrames(group, output.usableRect), byId = Object.create(null), candidates = [], obstacles = [], trace = [];
    registryState.windows.forEach(function(w) { byId[w.identity] = w; });
    snapshot.windows.forEach(function(w) {
        var registered = byId[w.identity];
        if (w.visible !== false && w.minimized !== true && w.onCurrentDesktop !== false && w.onCurrentActivity !== false &&
                w.windowType !== "desktop" && w.windowType !== "dock" && w.windowType !== "panel" && w.windowType !== "notification" && w.windowType !== "osd" &&
                w.windowType !== "guard" && w.windowType !== "lock" && Model.rectangle(w.rect)) obstacles.push(w);
        if (registered && registered.candidate === true && w.movable === true && w.visible === true &&
                w.onCurrentDesktop === true && w.onCurrentActivity === true && w.maximized === false &&
                w.fullscreen === false && w.tiled === false && w.outputIds && w.outputIds.length === 1 && w.outputIds[0] === outputId &&
                Model.rectangleContains(output.usableRect, w.rect)) candidates.push(w);
        else trace.push({ identity:w.identity, status:"skipped", reason:"ineligible" });
    });
    var moves = [], planned = [];
    for (var ci = 0; ci < candidates.length && moves.length < maximumWindows; ci++) {
        if (evaluations >= maxEvaluations || now() >= deadline || (options.shouldCancel && options.shouldCancel())) break;
        // Tolerant recognition is reserved for a future rotation planner.
        // Drift requires an actual, unique containing source zone.
        var window = candidates[ci], matches = containingZones(window.rect, zoneRects);
        if (matches.length !== 1) { trace.push({ identity:window.identity, status:"skipped", reason:matches.length ? "ambiguous_zone" : "no_zone" }); continue; }
        var zone = matches[0], anchor = byId[window.identity].originFrame || window.rect;
        var fixed = obstacles.filter(function(o) { return o.identity !== window.identity; }).map(function(o) { return o.rect; });
        planned.forEach(function(m) { fixed.push(m.toRect); });
        if (fixed.some(function(r) { return Model.rectangleOverlaps(window.rect, r); })) { trace.push({ identity:window.identity, status:"skipped", reason:"overlapping_window" }); continue; }
        var dxLimit = output.usableRect.width * rangePercent / 100;
        var dyLimit = output.usableRect.height * rangePercent / 100;
        var left = Math.ceil(Math.max(zone.x, output.usableRect.x, anchor.x - dxLimit, window.rect.x - dxLimit));
        var right = Math.floor(Math.min(zone.x + zone.width - window.rect.width,
            output.usableRect.x + output.usableRect.width - window.rect.width, anchor.x + dxLimit, window.rect.x + dxLimit));
        var top = Math.ceil(Math.max(zone.y, output.usableRect.y, anchor.y - dyLimit, window.rect.y - dyLimit));
        var bottom = Math.floor(Math.min(zone.y + zone.height - window.rect.height,
            output.usableRect.y + output.usableRect.height - window.rect.height, anchor.y + dyLimit, window.rect.y + dyLimit));
        if (left > right || top > bottom) { trace.push({ identity:window.identity, status:"skipped", reason:"no_safe_destination" }); continue; }
        var best = null, bestScore = -Infinity;
        function consider(x, y) {
            if (evaluations >= maxEvaluations || now() >= deadline || (options.shouldCancel && options.shouldCancel())) return;
            evaluations++;
            if (window.integerPosition===true) {x=Math.round(x);y=Math.round(y);}
            var target = { x:x, y:y, width:window.rect.width, height:window.rect.height };
            if (x < left || x > right || y < top || y > bottom || sameRect(target, window.rect) ||
                    fixed.some(function(r) { return Model.rectangleOverlaps(target, r); })) return;
            var nx = (x - window.rect.x) / output.usableRect.width, ny = (y - window.rect.y) / output.usableRect.height;
            var score = Math.sqrt(nx * nx + ny * ny);
            if (score > bestScore) { best = target; bestScore = score; }
        }
        // Sample inside the legal intersection, then try its edges to find
        // narrow gaps without allowing any destination to escape that bound.
        for (var attempt = 0; attempt < 96 && evaluations < maxEvaluations; attempt++) {
            if (now() >= deadline || (options.shouldCancel && options.shouldCancel())) break;
            var u = random(), v = random();
            if (!finite(u) || u < 0 || u >= 1 || !finite(v) || v < 0 || v >= 1) throw new Error("random must yield values in [0,1)");
            consider(left + Math.floor(u * (right - left + 1)), top + Math.floor(v * (bottom - top + 1)));
        }
        var xs = [left, right, window.rect.x], ys = [top, bottom, window.rect.y];
        fixed.forEach(function(r) { xs.push(r.x + r.width, r.x - window.rect.width); ys.push(r.y + r.height, r.y - window.rect.height); });
        for (var xi = 0; xi < xs.length && evaluations < maxEvaluations; xi++)
            for (var yi = 0; yi < ys.length && evaluations < maxEvaluations; yi++) consider(xs[xi], ys[yi]);
        if (!best) { trace.push({ identity:window.identity, status:"skipped", reason:"no_safe_destination" }); continue; }
        var move = { identity:window.identity, fromRect:copy(window.rect), toRect:best };
        moves.push(move); planned.push(move);
        trace.push({ identity:window.identity, status:"proposed", fromRect:copy(move.fromRect), toRect:copy(move.toRect), zone:zone });
    }
    var stopped = options.shouldCancel && options.shouldCancel() ? "cancelled" : evaluations >= maxEvaluations ? "evaluation_budget_exhausted" : now() >= deadline ? "deadline_exceeded" : null;
    if (stopped) {
        moves = [];
        trace.forEach(function(item) { if (item.status === "proposed") { item.status = "skipped"; item.reason = stopped; delete item.fromRect; delete item.toRect; delete item.zone; } });
    }
    var plan = Model.createPlan({ snapshotRevision:snapshot.topologyRevision, outputIds:[outputId], moves:moves,
        skipReasons:stopped ? [stopped] : [], createdAt:finite(started) ? started : null,
        metadata:{ mode:"group-drift", groupId:group.id, source:group.source, padding:group.padding,
            libraryRevision:group.revision || null, shiftRangePercent:rangePercent,
            maximumWindows:maximumWindows, complete:!stopped } });
    return { plan:plan, trace:trace, stats:{ evaluations:evaluations, stopped:stopped } };
}

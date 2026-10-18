.pragma library
.import "model.js" as Model

// Pure reconciliation and policy over immutable, plain-data desktop snapshots.
// Runtime adapters own compositor objects and pass opaque identity tokens here.

function copy(value) { return Model.copyPlain(value); }
function rectEqual(a, b) { return !!a && !!b && a.x === b.x && a.y === b.y && a.width === b.width && a.height === b.height; }
function validRect(r) { return !!Model.rectangle(r); }
function contains(a, b) { return Model.rectangleContains(a, b); }
function overlapsStrict(a, b) { return Model.rectangleOverlaps(a, b); }

function createRegistryState(sessionEpoch) {
    if (sessionEpoch === undefined || sessionEpoch === null || sessionEpoch === "") throw new Error("sessionEpoch required");
    return { sessionEpoch: sessionEpoch, windows: {}, outputs: [], topologyRevision: null, selectedOutputIds: [], requestedOutputSelections: [] };
}

function reasonAdd(reasons, reason) { if (reasons.indexOf(reason) < 0) reasons.push(reason); }
// Records only planning-relevant facts. Titles and process IDs never enter the signature.
function scopeSnapshot(snapshot,outputIds) {
    if (!Array.isArray(outputIds) || !outputIds.length) return snapshot;
    var next=copy(snapshot), targets=snapshot.outputs.filter(function(o){return outputIds.indexOf(o.id)>=0;});
    next.windows=next.windows.filter(function(w){return (Array.isArray(w.outputIds) && w.outputIds.some(function(id){return outputIds.indexOf(id)>=0;})) ||
        targets.some(function(o){return Model.rectangleOverlaps(w.rect,o.geometry);});});
    next.outputs=copy(targets);next.interactionScope=outputIds.slice();
    next.windows.forEach(function(w){
        if (Array.isArray(w.outputIds)) {
            if (w.outputIds.length>1) w.skipReasons=w.skipReasons.concat(["spanning"]);
            w.outputIds=w.outputIds.filter(function(id){return outputIds.indexOf(id)>=0;});
        }
    });
    if (!next.windows.some(function(w){return w.identity===next.focusedIdentity;})) next.focusedIdentity=null;
    return next;
}
function snapshotSignature(snapshot) {
    // Scoped execution validates focus on each written window and uses native
    // focus counters for the target output. Losing focus to another display is
    // harmless; it must not masquerade as a geometry change.
    var scoped=Array.isArray(snapshot.interactionScope) && snapshot.interactionScope.length===snapshot.outputs.length &&
        snapshot.outputs.every(function(o){return snapshot.interactionScope.indexOf(o.id)>=0;});
    return JSON.stringify({ focusedIdentity: scoped?null:snapshot.focusedIdentity,
        outputs: snapshot.outputs.map(function(o) { return [o.id, o.geometry, o.usableRect, o.scale]; }),
        // These surfaces are never movement obstacles. Strut/usable-area
        // changes remain covered by the output signature above.
        windows: snapshot.windows.filter(function(w){return ["desktop","dock","panel","guard","outline","notification","osd"].indexOf(w.windowType)<0;}).map(function(w) { return [w.identity, w.appId, w.outputIds, w.rect,
            w.visible, w.onCurrentDesktop, w.onCurrentActivity, scoped?null:w.focused, w.managed, w.modal,
            w.transient, w.movable, w.resizable, w.minimized, w.maximized, w.fullscreen,
            w.tiled, w.windowType, w.skipReasons, w.integerPosition===true, w.minimumSize || null]; }) });
}
function classifyWindow(w, outputById, selected, focusedIdentity, options) {
    var reasons = [], normal = w.windowType === "normal" || w.windowType === "unknown";
    if (!w.appId) reasonAdd(reasons, "unknown_app_identity");
    if (Model.applicationExcluded(w.appId,options.excludedApps,options.applicationAliases))
        reasonAdd(reasons, "excluded_app");
    if (w.appId && Array.isArray(options.ownAppIds) && options.ownAppIds.indexOf(w.appId) >= 0)
        reasonAdd(reasons, "guard_owned");
    if (w.managed === false) reasonAdd(reasons, "unmanaged");
    else if (w.managed !== true) reasonAdd(reasons, "unknown_managed");
    if (w.modal === true) reasonAdd(reasons, "modal");
    else if (w.modal !== false) reasonAdd(reasons, "unknown_modal");
    if (w.transient === true) reasonAdd(reasons, "transient");
    else if (w.transient !== false) reasonAdd(reasons, "unknown_transient");
    if (w.visible === false) reasonAdd(reasons, "not_visible");
    if (w.visible === null || w.visible === undefined) reasonAdd(reasons, "unknown_visibility");
    if (w.onCurrentDesktop === false) reasonAdd(reasons, "other_desktop");
    if (w.onCurrentDesktop === null || w.onCurrentDesktop === undefined) reasonAdd(reasons, "unknown_desktop");
    if (w.onCurrentActivity === false) reasonAdd(reasons, "other_activity");
    if (w.onCurrentActivity === null || w.onCurrentActivity === undefined) reasonAdd(reasons, "unknown_activity");
    if (options.allowIdleFocused !== true && options.excludeFocused !== false && (w.focused === true || w.identity === focusedIdentity)) reasonAdd(reasons, "focused");
    if (w.focused === null || w.focused === undefined) reasonAdd(reasons, "unknown_focus");
    if (w.windowType !== "normal") reasonAdd(reasons, w.windowType === "unknown" ? "unknown_window_type" : "unsupported_window_type");
    if (w.minimized === true) reasonAdd(reasons, "minimized"); else if (w.minimized !== false) reasonAdd(reasons, "unknown_minimized");
    if (w.maximized === true) reasonAdd(reasons, "maximized"); else if (w.maximized !== false) reasonAdd(reasons, "unknown_maximized");
    if (w.fullscreen === true) reasonAdd(reasons, "fullscreen"); else if (w.fullscreen !== false) reasonAdd(reasons, "unknown_fullscreen");
    if (w.tiled === true) reasonAdd(reasons, "tiled"); else if (w.tiled !== false) reasonAdd(reasons, "unknown_tiled");
    if (w.movable === false) reasonAdd(reasons, "immovable"); else if (w.movable !== true) reasonAdd(reasons, "unknown_movable");
    if (!validRect(w.rect)) reasonAdd(reasons, "unknown_geometry");
    var ids = Array.isArray(w.outputIds) ? w.outputIds : [];
    if (w.outputIds === null || w.outputIds === undefined) reasonAdd(reasons, "unknown_output_association");
    if (Array.isArray(w.outputIds) && ids.length === 0) reasonAdd(reasons, "missing_output_association");
    var spanning = ids.length > 1;
    if (ids.length === 1 && !outputById[ids[0]]) reasonAdd(reasons, "unknown_output_association");
    if (ids.length === 1 && validRect(w.rect)) {
        var contained = [];
        for (var i = 0; i < ids.length; i++) if (outputById[ids[i]] && contains(outputById[ids[i]].usableRect, w.rect)) contained.push(ids[i]);
        if (contained.length !== 1) spanning = true;
        else if (selected && !selected[contained[0]]) reasonAdd(reasons, "unselected_output");
    }
    if (spanning) reasonAdd(reasons, "spanning");
    if (Array.isArray(w.skipReasons)) for (var j = 0; j < w.skipReasons.length; j++) reasonAdd(reasons, w.skipReasons[j]);
    var current = w.visible !== false && w.minimized !== true && w.onCurrentDesktop !== false && w.onCurrentActivity !== false;
    // Preserve relevant visible windows as obstacles even when policy/state is unknown.
    var ignoredType = ["desktop","dock","panel","guard","lock","notification","osd"].indexOf(w.windowType)>=0;
    var obstacle = current && !ignoredType && validRect(w.rect);
    var candidate = reasons.length === 0 && normal && !spanning;
    return { candidate: candidate, obstacle: obstacle, spanning: spanning, skipReasons: reasons };
}

function reconcile(snapshot, prior, options) {
    var validation = Model.validateSnapshot(snapshot);
    if (!validation.valid) throw new Error("invalid snapshot: " + validation.errors.join(", "));
    snapshot = copy(snapshot); options = copy(options || {});
    var epoch = options.sessionEpoch === undefined ? (prior && prior.sessionEpoch) : options.sessionEpoch;
    if (epoch === undefined || epoch === null) throw new Error("sessionEpoch required");
    var resetSession = !prior || prior.sessionEpoch !== epoch;
    var topologyChanged = !prior || prior.topologyRevision !== snapshot.topologyRevision;
    var hasRequestedIds = Object.prototype.hasOwnProperty.call(options, "selectedOutputIds");
    var selectedIds = [], outputSelection, requestedSelections;
    if (hasRequestedIds) requestedSelections = describeOutputSelections(options.selectedOutputIds || [], snapshot.outputs, prior && prior.requestedOutputSelections, prior && prior.outputs);
    else requestedSelections = copy((prior && prior.requestedOutputSelections) || describeOutputSelections((prior && prior.selectedOutputIds) || [], (prior && prior.outputs) || [], [], []));
    if (topologyChanged && prior && !resetSession && prior.topologyRevision !== null && prior.topologyRevision !== undefined) outputSelection = mapOutputSelections(requestedSelections, snapshot.outputs);
    else {
        var requestedIds = hasRequestedIds ? (options.selectedOutputIds || []) : ((prior && prior.selectedOutputIds) || requestedSelections.map(function(item) { return item.requestedId; }));
        var present = Object.create(null), kept = [], missing = [];
        snapshot.outputs.forEach(function(o) { present[o.id] = true; });
        requestedIds.forEach(function(id) { if (present[id]) kept.push(id); else missing.push(id); });
        outputSelection = { selectedOutputIds: kept, ambiguousOldIds: [], missingOldIds: missing };
    }
    selectedIds = outputSelection.selectedOutputIds;
    var selected = Object.create(null), si;
    for (si = 0; si < selectedIds.length; si++) selected[selectedIds[si]] = true;
    var outputById = Object.create(null);
    snapshot.outputs.forEach(function(o) { outputById[o.id] = o; });
    var oldWindows = resetSession ? {} : (prior.records || {}), nextWindows = {}, windows = [], focused = snapshot.focusedIdentity;
    snapshot.windows.forEach(function(source) {
        var w = copy(source), key = w.identity;
        if (typeof key !== "string" || !key) throw new Error("window identity required");
        var old = oldWindows[key], sameSession = !!old && !resetSession;
        var geometryChanged = !!old && !rectEqual(old.lastFrame, w.rect);
        var adopted = !sameSession || topologyChanged || !validRect(w.rect) || (geometryChanged && !old.pendingAck);
        var origin = adopted ? copy(w.rect) : copy(old.originFrame);
        var previous = sameSession && !topologyChanged ? copy(old.previousFrame) : null;
        var revision = sameSession ? old.geometryRevision : 0;
        if (sameSession && geometryChanged) { revision += 1; previous = copy(old.lastFrame); }
        var classification = classifyWindow(w, outputById, selected, focused, options);
        var item = { identity: key, rect: copy(w.rect), originFrame: origin, lastFrame: copy(w.rect), previousFrame: previous,
            allowIdleFocused:options.allowIdleFocused===true, geometryRevision: revision, sessionEpoch: epoch, anchorAdopted: adopted, candidate: classification.candidate,
            obstacle: classification.obstacle, spanning: classification.spanning, skipReasons: classification.skipReasons };
        nextWindows[key] = { originFrame: copy(origin), lastFrame: copy(w.rect), previousFrame: copy(previous), geometryRevision: revision, pendingAck: null };
        windows.push(item);
    });
    // Substantive overlaps remain protected. Existing rounded border contacts
    // may participate; movement validation forbids new/deeper contacts.
    for (var wi = 0; wi < windows.length; wi++) {
        if (!windows[wi].candidate) continue;
        for (var wj = 0; wj < windows.length; wj++) if (wi !== wj && windows[wj].obstacle && Model.movementCollision(snapshot,null,windows[wi].identity,windows[wi].rect,windows[wj].identity,windows[wj].rect)) {
            windows[wi].candidate = false; reasonAdd(windows[wi].skipReasons, "overlapping_window"); break;
        }
    }
    var result = { sessionEpoch: epoch, topologyRevision: snapshot.topologyRevision === undefined ? null : snapshot.topologyRevision,
        snapshotSignature: snapshotSignature(snapshot),
        topologyChanged: topologyChanged, outputs: copy(snapshot.outputs), windows: windows, records: nextWindows,
        removedIdentities: [], selectedOutputIds: selectedIds.slice(), requestedOutputSelections: requestedSelections, outputSelection: outputSelection };
    if (!resetSession) Object.keys(oldWindows).forEach(function(id) { if (!Object.prototype.hasOwnProperty.call(nextWindows, id)) result.removedIdentities.push(id); });
    return result;
}

// A guard acknowledgement is the only geometry update that retains the origin.
function acknowledgeGuard(state, identity, acceptedRect) {
    var result = copy(state), record = result.records && result.records[identity];
    if (!record || !validRect(acceptedRect)) throw new Error("unknown identity or invalid accepted rectangle");
    record.previousFrame = copy(record.lastFrame); record.lastFrame = copy(acceptedRect); record.pendingAck = null; record.geometryRevision += 1;
    for (var i = 0; i < result.windows.length; i++) if (result.windows[i].identity === identity) {
        result.windows[i].previousFrame = copy(record.previousFrame); result.windows[i].lastFrame = copy(acceptedRect); result.windows[i].rect = copy(acceptedRect);
        result.windows[i].geometryRevision = record.geometryRevision; result.windows[i].anchorAdopted = false;
    }
    return result;
}

function outputKey(o) {
    // Connector plus a serial/EDID is required. A model name cannot distinguish
    // two otherwise identical monitors plugged into the same connector.
    if (!o || !o.connector) return null;
    var stable = o.edid || o.serial;
    return stable ? String(o.connector) + "|" + String(stable) : null;
}
function reconnectOutputSelection(oldOutputs, oldSelectedIds, newOutputs) {
    return mapOutputSelections(describeOutputSelections(oldSelectedIds, oldOutputs, [], []), newOutputs);
}
function describeOutputSelections(ids, currentOutputs, oldSelections, oldOutputs) {
    var byId = Object.create(null), descriptors = [], oldDescriptorById = Object.create(null), oldOutputById = Object.create(null);
    currentOutputs.forEach(function(output) { byId[output.id] = output; });
    (oldSelections || []).forEach(function(item) { oldDescriptorById[item.requestedId] = item; });
    (oldOutputs || []).forEach(function(output) { oldOutputById[output.id] = output; });
    ids.forEach(function(id) {
        var output = byId[id] || oldOutputById[id], key = outputKey(output), priorDescriptor = oldDescriptorById[id];
        if (!key && priorDescriptor) key = priorDescriptor.key;
        descriptors.push({ requestedId: id, key: key || null });
    });
    return descriptors;
}
function mapOutputSelections(descriptors, outputs) {
    var counts = Object.create(null), result = { selectedOutputIds: [], ambiguousOldIds: [], missingOldIds: [] };
    outputs.forEach(function(output) { var key = outputKey(output); if (key) counts[key] = (counts[key] || 0) + 1; });
    descriptors.forEach(function(descriptor) {
        var matches = [];
        if (descriptor.key) outputs.forEach(function(output) { if (outputKey(output) === descriptor.key) matches.push(output.id); });
        if (matches.length === 1 && counts[descriptor.key] === 1) result.selectedOutputIds.push(matches[0]);
        else if (matches.length > 1 || (descriptor.key && counts[descriptor.key] > 1)) result.ambiguousOldIds.push(descriptor.requestedId);
        else result.missingOldIds.push(descriptor.requestedId);
    });
    return result;
}

function diagnosticSummary(windows) {
    var summary = { eligible: 0, obstacles: 0, skipped: 0, byReason: Object.create(null) };
    (windows || []).forEach(function(w) {
        if (w.candidate) summary.eligible++;
        if (w.obstacle) summary.obstacles++;
        if (!w.candidate) summary.skipped++;
        (w.skipReasons || []).forEach(function(reason) { summary.byReason[reason] = (summary.byReason[reason] || 0) + 1; });
    });
    return summary;
}

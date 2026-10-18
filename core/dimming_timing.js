.pragma library

// Pure timing and interaction policy for a future dimming backend.  The host
// owns polling, desktop input hooks, session state, and all rendering.
function createDimmingDelayState() { return { since: {}, delay: null }; }
function dimmingCopy(value) { return JSON.parse(JSON.stringify(value)); }
function dimmingFinite(value) { return typeof value === "number" && isFinite(value); }
function dimmingIds(ids) {
    if (!Array.isArray(ids)) return [];
    var result = [], seen = Object.create(null);
    for (var i = 0; i < ids.length; i++) {
        if (typeof ids[i] === "string" && ids[i] && !seen["$" + ids[i]]) {
            result.push(ids[i]); seen["$" + ids[i]] = true;
        }
    }
    return result;
}

// Eligibility is continuous: losing eligibility drops only that item's clock.
function updateDimmingDelay(state, eligibleIds, delay, now) {
    state = state || createDimmingDelayState();
    var ids = dimmingIds(eligibleIds);
    if (!dimmingFinite(now) || !dimmingFinite(delay))
        return { state:createDimmingDelayState(), ready:[] };
    delay = Math.max(0, delay);
    var since = state.delay === delay ? dimmingCopy(state.since || {}) : {};
    var present = Object.create(null), ready = [];
    for (var i = 0; i < ids.length; i++) present["$" + ids[i]] = true;
    Object.keys(since).forEach(function(key) { if (!present[key]) delete since[key]; });
    for (var j = 0; j < ids.length; j++) {
        var id = ids[j], key = "$" + id;
        if (!Object.prototype.hasOwnProperty.call(since, key) || since[key] > now) since[key] = now;
        if (now - since[key] >= delay) ready.push(id);
    }
    return { state:{ since:since, delay:delay }, ready:ready };
}

function resetDimmingDelay(state) { return createDimmingDelayState(); }

function createDimmingFadeState() { return { starts:{} }; }
function updateDimmingFade(state, readyIds, duration, now) {
    state = state || createDimmingFadeState();
    var ids = dimmingIds(readyIds);
    if (!dimmingFinite(now) || !dimmingFinite(duration))
        return { state:createDimmingFadeState(), progress:{} };
    duration = Math.max(0, duration);
    var starts = dimmingCopy(state.starts || {}), present = Object.create(null), progress = {};
    for (var i = 0; i < ids.length; i++) present["$" + ids[i]] = true;
    Object.keys(starts).forEach(function(key) { if (!present[key]) delete starts[key]; });
    for (var j = 0; j < ids.length; j++) {
        var id = ids[j], key = "$" + id;
        if (!Object.prototype.hasOwnProperty.call(starts, key) || starts[key] > now) starts[key] = now;
        progress[id] = duration <= 0 ? 1 : Math.min(1, Math.max(0, (now - starts[key]) / duration));
    }
    return { state:{ starts:starts }, progress:progress };
}
function resetDimmingFade(state) { return createDimmingFadeState(); }

function createDimmingScopeState() { return { displayId:null, desktop:false, activity:null }; }
function dimmingScopeEvent(state, event) {
    state = state || createDimmingScopeState();
    var next = dimmingCopy(state), e = event || {};
    if (e.type === "desktopClick" && typeof e.displayId === "string" && e.displayId) {
        next.displayId = e.displayId; next.desktop = true;
    } else if ((e.type === "desktopSwitch" || e.type === "activitySwitch") && typeof e.pointerDisplayId === "string" && e.pointerDisplayId) {
        next.displayId = e.pointerDisplayId; next.desktop = false;
        if (e.type === "activitySwitch") next.activity = e.activityId || null;
    } else if (e.type === "windowClick" || e.type === "windowKey" || e.type === "windowInteraction") {
        next.displayId = null; next.desktop = false;
    } else if (e.type === "reset") return createDimmingScopeState();
    return next;
}

// If desktop scope is held, stale focus reports cannot move it. A real window
// interaction clears the override through dimmingScopeEvent before resolution.
function resolveDimmingScope(state, reportedWindowId, reportedDisplayId) {
    state = state || createDimmingScopeState();
    if (state.displayId && (state.desktop || !reportedWindowId || reportedDisplayId !== state.displayId))
        return { focusedWindowId:null, displayId:state.displayId, desktop:!!state.desktop };
    return { focusedWindowId:reportedWindowId || null,
        displayId:reportedWindowId ? (state.displayId || reportedDisplayId || null) : (reportedDisplayId || null),
        desktop:false };
}

function createBrightnessRestoreState() { return { until:0, generation:0 }; }
function restoreDimmingBrightness(state, now, configuredDelayMs) {
    state = state || createBrightnessRestoreState();
    if (!dimmingFinite(now)) return createBrightnessRestoreState();
    var hold = dimmingFinite(configuredDelayMs) ? Math.max(5000, configuredDelayMs) : 5000;
    return { until:now + hold, generation:(state.generation || 0) + 1 };
}
function isBrightnessRestoreActive(state, now) {
    return !!state && dimmingFinite(now) && dimmingFinite(state.until) && now < state.until;
}

// Central fail-bright gate. Any loss of readiness immediately clears timing
// progress so becoming ready again requires fresh continuous eligibility.
function dimmingMustBeBright(status) {
    status = status || {};
    return status.available !== true || status.rendererProven !== true ||
        status.paused !== false || status.locked !== false ||
        status.inactive !== false || status.sleeping !== false || status.rendererError !== false;
}
function gateDimmingTiming(state, status) {
    state = state || { delay:createDimmingDelayState(), fade:createDimmingFadeState() };
    if (!dimmingMustBeBright(status)) return { bright:false, state:dimmingCopy(state) };
    return { bright:true, state:{ delay:createDimmingDelayState(), fade:createDimmingFadeState() } };
}

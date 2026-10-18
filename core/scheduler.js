.pragma library

// Pure per-output deadline policy. The host owns timers and translates
// commands into controller transactions; this module owns no runtime objects.
var MAX_OUTPUTS = 64;

function createSchedulerState() {
    return { phase: "Paused", generation: 0, intervals: {}, deadlines: {},
        inFlight: null, lastResult: null, lastNow: null };
}

function schedulerCopy(value) { return JSON.parse(JSON.stringify(value)); }
function schedulerFinite(value) { return typeof value === "number" && isFinite(value); }
function schedulerValidIds(ids) {
    if (!Array.isArray(ids) || !ids.length || ids.length > MAX_OUTPUTS) return false;
    var seen = Object.create(null);
    for (var i = 0; i < ids.length; i++) {
        if (typeof ids[i] !== "string" || !ids[i] || seen["$" + ids[i]]) return false;
        seen["$" + ids[i]] = true;
    }
    return true;
}
function schedulerIntervals(ids, supplied, fallback) {
    var result = {};
    for (var i = 0; i < ids.length; i++) {
        var id = ids[i], interval = supplied && supplied[id] !== undefined ? supplied[id] : fallback;
        if (!schedulerFinite(interval) || interval <= 0 || interval > 2147483647) return null;
        result[id] = interval;
    }
    return result;
}
function schedulerReset(state, now) {
    var next = {};
    for (var id in state.intervals) if (Object.prototype.hasOwnProperty.call(state.intervals, id))
        next[id] = now + state.intervals[id];
    state.deadlines = next;
    state.inFlight = null;
}
function schedulerDueId(state, now) {
    var due = Object.keys(state.deadlines).filter(function(id) { return state.deadlines[id] <= now; });
    due.sort(function(a, b) {
        var diff = state.deadlines[a] - state.deadlines[b];
        return diff || (a < b ? -1 : (a > b ? 1 : 0));
    });
    return due.length ? due[0] : null;
}

// Read-only display information; never advances or authorizes scheduling.
function nextCheck(state, now) {
    if (!state || state.phase!=="Running" || state.inFlight || !schedulerFinite(now)) return null;
    var ids=Object.keys(state.deadlines).sort(function(a,b){return state.deadlines[a]-state.deadlines[b] || a.localeCompare(b);});
    if (!ids.length || !schedulerFinite(state.deadlines[ids[0]])) return null;
    var id=ids[0],interval=state.intervals && state.intervals[id];
    if (!schedulerFinite(interval) || interval<=0) return null;
    // Fractional monotonic samples can exceed the interval by an ULP after
    // subtraction. Keep display information within its protocol bound.
    return {outputId:id,remainingSeconds:Math.max(0,Math.min(interval,state.deadlines[id]-now))/1000};
}

function transitionScheduler(input, event) {
    var state = schedulerCopy(input || createSchedulerState()), commands = [];
    if (!event || typeof event.type !== "string") return { state: state, commands: commands };
    var type = event.type, now = event.now;
    if (schedulerFinite(now) && state.lastNow !== null && now < state.lastNow) {
        state.generation++; state.phase = "Suspended"; state.deadlines = {}; state.inFlight = null;
        state.lastResult = { status:"clock-regressed", at:now };
        return { state:state, commands:[] };
    }

    if (type === "start" || type === "settingsChanged" || type === "wake" || type === "topologyChanged") {
        if (!schedulerFinite(now) || !schedulerValidIds(event.outputIds)) {
            state.generation++; state.phase="Suspended"; state.intervals={}; state.deadlines={}; state.inFlight=null;
            return { state:state, commands:[] };
        }
        var intervals = schedulerIntervals(event.outputIds, event.intervals, event.intervalMs);
        if (!intervals || Object.keys(intervals).some(function(id) { return !schedulerFinite(now + intervals[id]); })) {
            state.generation++; state.phase="Suspended"; state.intervals={}; state.deadlines={}; state.inFlight=null;
            return { state:state, commands:[] };
        }
        state.generation++;
        state.phase = type === "start" ? "Paused" : "Suspended";
        state.intervals = intervals;
        state.deadlines = {};
        state.inFlight = null;
        state.lastResult = null;
        state.lastNow = now;
        return { state: state, commands: [] };
    }
    if (type === "resume") {
        if (state.phase === "Running") return { state: state, commands: [] };
        if (!schedulerFinite(now)) return { state: state, commands: [] };
        if (!Object.keys(state.intervals).length) return { state:state, commands:[] };
        state.generation++;
        state.phase = "Running";
        schedulerReset(state, now);
        state.lastNow = now;
        return { state: state, commands: [] };
    }
    if (type === "pause" || type === "suspend" || type === "companionLost" || type === "unsafe") {
        state.generation++;
        state.phase = type === "pause" ? "Paused" : "Suspended";
        state.deadlines = {};
        state.inFlight = null;
        return { state: state, commands: [] };
    }
    if (type === "due") {
        if (state.phase !== "Running" || state.inFlight || !schedulerFinite(now)) return { state: state, commands: [] };
        state.lastNow = now;
        var dueId = schedulerDueId(state, now);
        if (dueId === null) return { state: state, commands: [] };
        state.inFlight = { outputId: dueId, generation: state.generation };
        commands.push({ type: "beginTransaction", outputId: dueId, generation: state.generation });
        return { state: state, commands: commands };
    }
    if (type === "completed" || type === "skipped") {
        if (!schedulerFinite(now) || !state.inFlight || event.outputId !== state.inFlight.outputId ||
                event.generation !== state.generation || state.inFlight.generation !== state.generation)
            return { state: state, commands: [] };
        var finishedId = state.inFlight.outputId;
        state.lastNow = now;
        state.inFlight = null;
        if (state.phase === "Running" && Object.prototype.hasOwnProperty.call(state.intervals, finishedId))
            state.deadlines[finishedId] = now + state.intervals[finishedId];
        state.lastResult = { outputId: finishedId, status: type, at: now };
        return { state: state, commands: [] };
    }
    return { state: state, commands: [] };
}

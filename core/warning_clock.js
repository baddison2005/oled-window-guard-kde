.pragma library

// Display/expiry clock for a confirmed warning. Native monotonic samples are
// independent of typing, grace state and companion status. A stale sample can
// never make a displayed countdown increase.
function start(now,duration) {
    if (!Number.isFinite(now) || now<0 || !Number.isFinite(duration) || duration<3000 || duration>60000 || !Number.isFinite(now+duration)) return null;
    return {deadline:now+duration,lastNow:now,remainingMs:duration};
}
function sample(state,now) {
    if (!state || !Number.isFinite(now) || now<0) return null;
    const current=Math.max(state.lastNow,now);
    return {deadline:state.deadline,lastNow:current,remainingMs:Math.max(0,Math.min(state.remainingMs,state.deadline-current))};
}

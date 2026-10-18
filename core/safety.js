.pragma library

// Fail-closed Phase 1 activity/session gate, shared by every transaction boundary.
var REQUIRED_ACTIVITY = ["keyboard", "pointer", "scroll", "buttonState"];

// Session readiness does not require quiet time. Warnings may be scheduled
// while the user is active; the affected output is checked at its deadline.
function evaluateReadiness(snapshot, now, maxAgeMs) {
    function no(reason) { return { safe:false, reason:reason }; }
    if (!snapshot || !finite(now) || !finite(maxAgeMs) || maxAgeMs < 0) return no("unknown-snapshot");
    if (!finite(snapshot.observedAt) || now < snapshot.observedAt || now - snapshot.observedAt > maxAgeMs) return no("stale-snapshot");
    if (snapshot.companion !== "connected") return no("companion-unavailable");
    if (snapshot.locked !== false) return no("session-locked-or-unknown");
    if (snapshot.suspended !== false) return no("session-suspended-or-unknown");
    if (snapshot.topologyStable !== true) return no("topology-unknown-or-changing");
    if (!Number.isInteger(snapshot.topologyGeneration) || snapshot.topologyGeneration < 0 ||
            !Array.isArray(snapshot.topologyOutputIds) || !snapshot.topologyOutputIds.length ||
            duplicate(snapshot.topologyOutputIds) || snapshot.topologyOutputIds.some(function(id) { return typeof id !== "string" || !id; }))
        return no("topology-identity-unknown");
    if (!Array.isArray(snapshot.outputs) || snapshot.outputs.length !== snapshot.topologyOutputIds.length ||
            duplicate(snapshot.outputs.map(function(o) { return o && o.id; })) ||
            snapshot.outputs.some(function(o) { return !o || o.known !== true || typeof o.id !== "string" || !o.id ||
                typeof o.quiet !== "boolean" || typeof o.buttonsDown !== "boolean"; }) ||
            snapshot.topologyOutputIds.some(function(id) { return !snapshot.outputs.some(function(o) { return o.id === id; }); }))
        return no("per-output-activity-unknown");
    if (!snapshot.capabilities || REQUIRED_ACTIVITY.some(function(k) { return snapshot.capabilities[k] !== true; }))
        return no("activity-coverage-incomplete");
    if (typeof snapshot.allOutputsQuiet !== "boolean" || typeof snapshot.heldButtons !== "boolean")
        return no("global-activity-unknown");
    if ((snapshot.allOutputsQuiet && snapshot.outputs.some(function(o) { return !o.quiet; })) ||
            (!snapshot.heldButtons && snapshot.outputs.some(function(o) { return o.buttonsDown; })))
        return no("activity-facts-conflict");
    return { safe:true, reason:"ready" };
}

function evaluateForOutput(snapshot, now, maxAgeMs, outputId) {
    var ready = evaluateReadiness(snapshot,now,maxAgeMs);
    if (!ready.safe) return ready;
    if (typeof outputId !== "string" || !outputId) return {safe:false, reason:"output-unknown"};
    var output = snapshot.outputs.filter(function(o) { return o.id === outputId; })[0];
    if (!output) return {safe:false, reason:"output-unknown"};
    if (output.quiet !== true || output.buttonsDown !== false) return {safe:false, reason:"output-active"};
    if (!finite(output.quietSince) || output.quietSince > now || !finite(snapshot.quietMs) ||
            snapshot.quietMs < 0 || now - output.quietSince < snapshot.quietMs)
        return {safe:false, reason:"quiet-period-incomplete"};
    return {safe:true, reason:"safe"};
}

function evaluate(snapshot, now, maxAgeMs) {
    function no(reason) { return { safe: false, reason: reason }; }
    if (!snapshot || !finite(now) || !finite(maxAgeMs) || maxAgeMs < 0) return no("unknown-snapshot");
    if (!finite(snapshot.observedAt) || now < snapshot.observedAt || now - snapshot.observedAt > maxAgeMs) return no("stale-snapshot");
    if (snapshot.companion !== "connected") return no("companion-unavailable");
    if (snapshot.locked !== false) return no("session-locked-or-unknown");
    if (snapshot.suspended !== false) return no("session-suspended-or-unknown");
    if (snapshot.topologyStable !== true) return no("topology-unknown-or-changing");
    if (!Number.isInteger(snapshot.topologyGeneration) || snapshot.topologyGeneration < 0 ||
            !Array.isArray(snapshot.topologyOutputIds) || !snapshot.topologyOutputIds.length ||
            duplicate(snapshot.topologyOutputIds) || snapshot.topologyOutputIds.some(function(id) { return typeof id !== "string" || !id; }))
        return no("topology-identity-unknown");
    if (snapshot.allOutputsQuiet !== true) return no("activity-or-unknown");
    if (snapshot.heldButtons !== false) return no("button-held-or-unknown");
    if (!Array.isArray(snapshot.outputs) || !snapshot.outputs.length ||
            snapshot.outputs.some(function(o) { return !o || o.known !== true || typeof o.id !== "string" || !o.id || o.quiet !== true || o.buttonsDown !== false; }) ||
            duplicate(snapshot.outputs.map(function(o) { return o.id; })) || snapshot.outputs.length !== snapshot.topologyOutputIds.length ||
            snapshot.topologyOutputIds.some(function(id) { return !snapshot.outputs.some(function(o) { return o.id === id; }); }))
        return no("per-output-activity-unknown");
    if (!snapshot.capabilities || REQUIRED_ACTIVITY.some(function(k) { return snapshot.capabilities[k] !== true; })) return no("activity-coverage-incomplete");
    if (!finite(snapshot.quietSince) || snapshot.quietSince > now || !finite(snapshot.quietMs) || snapshot.quietMs < 0 || now - snapshot.quietSince < snapshot.quietMs)
        return no("quiet-period-incomplete");
    return { safe: true, reason: "safe" };
}
function finite(v) { return typeof v === "number" && isFinite(v); }
function duplicate(a) { var seen = Object.create(null); for (var i = 0; i < a.length; i++) { var k = "$" + a[i]; if (seen[k]) return true; seen[k] = true; } return false; }

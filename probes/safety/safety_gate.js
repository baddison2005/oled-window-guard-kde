/*
 * Phase 1 safety capability experiment. This is a pure policy probe, not app
 * runtime code. Unknown, stale, or missing observations always suspend.
 */
(function (root) {
    "use strict";

    const REQUIRED_ACTIVITY = ["keyboard", "pointer", "scroll", "buttonState"];

    function evaluate(snapshot, now, maxAgeMs) {
        if (!snapshot || !Number.isFinite(now) || !Number.isFinite(maxAgeMs)) {
            return { safe: false, reason: "unknown-snapshot" };
        }
        if (!Number.isFinite(snapshot.observedAt) || now < snapshot.observedAt ||
            now - snapshot.observedAt > maxAgeMs) {
            return { safe: false, reason: "stale-snapshot" };
        }
        if (snapshot.companion !== "connected") return { safe: false, reason: "companion-unavailable" };
        if (snapshot.locked !== false) return { safe: false, reason: "session-locked-or-unknown" };
        if (snapshot.suspended !== false) return { safe: false, reason: "session-suspended-or-unknown" };
        if (snapshot.topologyStable !== true) return { safe: false, reason: "topology-unknown-or-changing" };
        if (!Number.isInteger(snapshot.topologyGeneration) || snapshot.topologyGeneration < 0 ||
            !Array.isArray(snapshot.topologyOutputIds) || snapshot.topologyOutputIds.length === 0 ||
            snapshot.topologyOutputIds.some(id => typeof id !== "string" || id.length === 0) ||
            new Set(snapshot.topologyOutputIds).size !== snapshot.topologyOutputIds.length) {
            return { safe: false, reason: "topology-identity-unknown" };
        }
        if (snapshot.allOutputsQuiet !== true) return { safe: false, reason: "activity-or-unknown" };
        if (snapshot.heldButtons !== false) return { safe: false, reason: "button-held-or-unknown" };
        if (!Array.isArray(snapshot.outputs) || snapshot.outputs.length === 0 ||
            snapshot.outputs.some(output => !output || output.known !== true ||
                typeof output.id !== "string" || output.id.length === 0 ||
                output.quiet !== true || output.buttonsDown !== false) ||
            new Set(snapshot.outputs.map(output => output.id)).size !== snapshot.outputs.length ||
            snapshot.outputs.length !== snapshot.topologyOutputIds.length ||
            snapshot.topologyOutputIds.some(id => !snapshot.outputs.some(output => output.id === id))) {
            return { safe: false, reason: "per-output-activity-unknown" };
        }
        if (!snapshot.capabilities || REQUIRED_ACTIVITY.some(k => snapshot.capabilities[k] !== true)) {
            return { safe: false, reason: "activity-coverage-incomplete" };
        }
        if (!Number.isFinite(snapshot.quietSince) || snapshot.quietSince > now ||
            !Number.isFinite(snapshot.quietMs) || snapshot.quietMs < 0 ||
            now - snapshot.quietSince < snapshot.quietMs) {
            return { safe: false, reason: "quiet-period-incomplete" };
        }
        return { safe: true, reason: "safe" };
    }

    // A generation token models warning/apply callbacks: cancellation makes
    // every callback issued before a safety boundary a no-op.
    function generation() {
        let current = 0;
        return {
            current: () => current,
            invalidate: () => ++current,
            accepts: token => token === current
        };
    }

    const api = { evaluate, generation, REQUIRED_ACTIVITY };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.OwgSafetyProbe = api;
})(this);

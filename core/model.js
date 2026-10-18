.pragma library

function applicationExcluded(appId, excluded, aliases) {
    function key(value) {return typeof value==="string"?value.replace(/\.desktop$/i,"").toLowerCase():"";}
    const actual=key(appId);
    return !!actual && Array.isArray(excluded) && excluded.some(function(id) {
        return key(id)===actual || (aliases && Array.isArray(aliases[id]) && aliases[id].some(function(alias){return key(alias)===actual;}));
    });
}

// Cairo-Dock's XWayland dock and auto-hide input strip can advertise NORMAL.
// Its movable preferences/dialog windows retain their compositor role.
function applicationWindowType(type, appId, movable, resizable) {
    return type === "normal" && appId === "cairo-dock" && movable === false && resizable === false ? "dock" : type;
}

// Plain-data model and logical geometry helpers. No Qt or compositor objects.
var OVERLAP_EPSILON = 0.01;
var READBACK_TOLERANCE = 0.5;

function isFiniteNumber(value) {
    return typeof value === "number" && isFinite(value);
}

function rectangle(value) {
    if (!value || !isFiniteNumber(value.x) || !isFiniteNumber(value.y) ||
            !isFiniteNumber(value.width) || !isFiniteNumber(value.height) ||
            value.width <= 0 || value.height <= 0 ||
            !isFiniteNumber(value.x + value.width) ||
            !isFiniteNumber(value.y + value.height)) return null;
    return { x: value.x, y: value.y, width: value.width, height: value.height };
}

function rectangleContains(outer, inner) {
    outer = rectangle(outer); inner = rectangle(inner);
    return !!outer && !!inner && inner.x >= outer.x && inner.y >= outer.y &&
        inner.x + inner.width <= outer.x + outer.width &&
        inner.y + inner.height <= outer.y + outer.height;
}

function rectangleOverlaps(a, b) {
    a = rectangle(a); b = rectangle(b);
    if (!a || !b) return false;
    var width = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
    var height = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
    return width > OVERLAP_EPSILON && height > OVERLAP_EPSILON;
}

// Preserve only an existing <=2 logical-pixel border contact on the same
// axis. Never create a contact with another window or deepen one.
// This tolerance belongs to movement, not output association or readback.
function roundingContactAllowed(a, b, sourceA, sourceB) {
    a=rectangle(a);b=rectangle(b);sourceA=rectangle(sourceA);sourceB=rectangle(sourceB);
    if (!a || !b || !sourceA || !sourceB || !rectangleOverlaps(sourceA,sourceB)) return false;
    function axis(position, size) {
        var depth=Math.min(sourceA[position]+sourceA[size],sourceB[position]+sourceB[size])-Math.max(sourceA[position],sourceB[position]);
        var next=Math.min(a[position]+a[size],b[position]+b[size])-Math.max(a[position],b[position]);
        return depth>0.01 && depth<=2+1e-9 && next<=depth+1e-9 &&
            Math.min(sourceA[size],sourceB[size],a[size],b[size])>32;
    }
    return axis("x","width") || axis("y","height");
}
function movementCollision(snapshot, plan, identity, rect, otherIdentity, otherRect) {
    if (!rectangleOverlaps(rect,otherRect)) return false;
    var a=null,b=null;
    snapshot.windows.forEach(function(w){if(w.identity===identity)a=w;if(w.identity===otherIdentity)b=w;});
    if (!a || !b || a.windowType!=="normal" || b.windowType!=="normal" || a.managed!==true || b.managed!==true) return true;
    var fromA=a.rect,fromB=b.rect;
    // Restore is the verified, one-shot inverse. Its destination frames are
    // the exact captured original layout, including original border contacts.
    if (plan && plan.metadata && plan.metadata.mode==="restore" && plan.metadata.restore===true)
        plan.moves.forEach(function(m){if(m.identity===identity)fromA=m.toRect;if(m.identity===otherIdentity)fromB=m.toRect;});
    return !roundingContactAllowed(rect,otherRect,fromA,fromB);
}

function rectanglesClose(a, b, tolerance) {
    a = rectangle(a); b = rectangle(b);
    if (!a || !b) return false;
    if (tolerance === undefined) tolerance = READBACK_TOLERANCE;
    if (!isFiniteNumber(tolerance) || tolerance < 0) return false;
    return Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance &&
        Math.abs(a.width - b.width) <= tolerance && Math.abs(a.height - b.height) <= tolerance;
}

// Limits are supplied by the mode validator, never trusted from plan metadata.
// Fitting may adjust a resizable client by a few logical pixels; it cannot
// move it outside its slot or waive native size-hint/collision/readback checks.
function roundingResizeAllowed(from, to, resizable, limit, minimum) {
    from=rectangle(from);to=rectangle(to);
    if (!from || !to || resizable!==true || !isFiniteNumber(limit) || limit<=0 || limit>32) return false;
    if (minimum!==undefined && (!minimum || !isFiniteNumber(minimum.width) || !isFiniteNumber(minimum.height) ||
            minimum.width<0 || minimum.height<0 || to.width<minimum.width || to.height<minimum.height)) return false;
    var dw=from.width-to.width, dh=from.height-to.height;
    return (dw!==0 || dh!==0) && Math.abs(dw)<=limit && Math.abs(dh)<=limit;
}

function copyPlain(value, ancestors) {
    if (value === null || typeof value === "string" || typeof value === "boolean") return value;
    if (typeof value === "number" && isFiniteNumber(value)) return value;
    if (!value || typeof value !== "object") throw new Error("value is not plain data");
    ancestors = ancestors || [];
    if (ancestors.indexOf(value) !== -1) throw new Error("cyclic data is not allowed");
    if (!Array.isArray(value)) {
        var prototype = Object.getPrototypeOf(value);
        var constructor = prototype && prototype.constructor;
        if (Object.prototype.toString.call(value) !== "[object Object]" ||
                (prototype !== null && (!constructor || constructor.name !== "Object")))
            throw new Error("value is not a plain object");
    }
    ancestors.push(value);
    var result, i, key;
    if (Array.isArray(value)) {
        result = [];
        for (i = 0; i < value.length; i++) result.push(copyPlain(value[i], ancestors));
    } else {
        result = {};
        for (key in value) if (Object.prototype.hasOwnProperty.call(value, key)) result[key] = copyPlain(value[key], ancestors);
    }
    ancestors.pop();
    return result;
}

function createSnapshot(value) {
    value = copyPlain(value === undefined ? {} : value);
    if (!value || Array.isArray(value) || typeof value !== "object")
        throw new Error("snapshot must be a plain object");
    return {
        outputs: value.outputs === undefined ? [] : value.outputs,
        windows: value.windows === undefined ? [] : value.windows,
        focusedIdentity: value.focusedIdentity === undefined ? null : value.focusedIdentity,
        capturedAt: value.capturedAt === undefined ? null : value.capturedAt,
        topologyRevision: value.topologyRevision === undefined ? null : value.topologyRevision
    };
}

function createPlan(value) {
    value = copyPlain(value === undefined ? {} : value);
    if (!value || Array.isArray(value) || typeof value !== "object")
        throw new Error("plan must be a plain object");
    return {
        snapshotRevision: value.snapshotRevision === undefined ? null : value.snapshotRevision,
        outputIds: value.outputIds === undefined ? [] : value.outputIds,
        moves: value.moves === undefined ? [] : value.moves,
        skipReasons: value.skipReasons === undefined ? [] : value.skipReasons,
        createdAt: value.createdAt === undefined ? null : value.createdAt,
        metadata: value.metadata === undefined ? {} : value.metadata
    };
}

function validateSnapshot(snapshot) {
    var errors = [], ids = Object.create(null), outputIdsById = Object.create(null);
    if (!snapshot || !Array.isArray(snapshot.outputs) || !Array.isArray(snapshot.windows))
        return { valid: false, errors: ["outputs and windows must be arrays"] };
    try { copyPlain(snapshot); } catch (error) { errors.push("snapshot contains non-plain or cyclic data"); }
    if (!validRevision(snapshot.topologyRevision)) errors.push("topologyRevision must be a nonempty string or finite number");
    for (var i = 0; i < snapshot.outputs.length; i++) {
        var output = snapshot.outputs[i];
        if (!output || typeof output.id !== "string" || !output.id) errors.push("output[" + i + "] has invalid id");
        else if (ids["o:" + output.id]) errors.push("duplicate output id: " + output.id);
        else {
            ids["o:" + output.id] = true;
            outputIdsById[output.id] = true;
        }
        if (!rectangle(output && output.geometry)) errors.push("output[" + i + "] has invalid geometry");
        if (!rectangle(output && output.usableRect)) errors.push("output[" + i + "] has invalid usableRect");
        if (rectangle(output && output.geometry) && rectangle(output && output.usableRect) &&
                !rectangleContains(output.geometry, output.usableRect))
            errors.push("output[" + i + "] usableRect lies outside geometry");
        if (!isFiniteNumber(output && output.scale) || output.scale <= 0) errors.push("output[" + i + "] has invalid scale");
    }
    ids = Object.create(null);
    for (var j = 0; j < snapshot.windows.length; j++) {
        var window = snapshot.windows[j];
        if (!window || typeof window.identity !== "string" || !window.identity) errors.push("window[" + j + "] has invalid identity");
        else if (ids[window.identity]) errors.push("duplicate window identity: " + window.identity);
        else ids[window.identity] = true;
        if (!rectangle(window && window.rect)) errors.push("window[" + j + "] has invalid rect");
        if (window && window.appId !== null && typeof window.appId !== "string") errors.push("window[" + j + "] appId must be a string or null");
        if (window && window.integerPosition !== undefined && window.integerPosition !== null && typeof window.integerPosition !== "boolean")
            errors.push("window[" + j + "] integerPosition must be boolean or null");
        if (window && window.minimumSize!==undefined && window.minimumSize!==null &&
                (!isFiniteNumber(window.minimumSize.width) || !isFiniteNumber(window.minimumSize.height) || window.minimumSize.width<0 || window.minimumSize.height<0))
            errors.push("window[" + j + "] minimumSize must be a non-negative size or null");
        if (window && window.outputIds !== null) {
            if (!Array.isArray(window.outputIds)) errors.push("window[" + j + "] outputIds must be an array or null");
            else {
                var associated = {};
                for (var k = 0; k < window.outputIds.length; k++) {
                    var outputId = window.outputIds[k];
                    if (typeof outputId !== "string" || !outputId) errors.push("window[" + j + "] has invalid output id");
                    else if (associated["o:" + outputId]) errors.push("window[" + j + "] has duplicate output id: " + outputId);
                    else {
                        associated["o:" + outputId] = true;
                        if (!Object.prototype.hasOwnProperty.call(outputIdsById, outputId)) errors.push("window[" + j + "] references unknown output: " + outputId);
                    }
                }
            }
        }
        var stateFields = ["visible", "onCurrentDesktop", "onCurrentActivity", "focused", "managed", "modal", "transient", "movable", "resizable", "minimized", "maximized", "fullscreen", "tiled"];
        for (var stateIndex = 0; window && stateIndex < stateFields.length; stateIndex++) {
            var state = window[stateFields[stateIndex]];
            if (state !== null && typeof state !== "boolean") errors.push("window[" + j + "] " + stateFields[stateIndex] + " must be boolean or null");
        }
        if (window && (typeof window.windowType !== "string" || !window.windowType)) errors.push("window[" + j + "] has invalid windowType");
        if (window && (!Array.isArray(window.skipReasons) || window.skipReasons.some(function(reason) { return typeof reason !== "string" || !reason; }))) errors.push("window[" + j + "] skipReasons must be an array of nonempty strings");
    }
    if (snapshot.capturedAt !== null && snapshot.capturedAt !== undefined && !isFiniteNumber(snapshot.capturedAt)) errors.push("capturedAt must be finite or null");
    return { valid: errors.length === 0, errors: errors };
}

function validatePlan(plan) {
    var errors = [], ids = Object.create(null);
    if (!plan || !Array.isArray(plan.outputIds) || !Array.isArray(plan.moves))
        return { valid: false, errors: ["outputIds and moves must be arrays"] };
    try { copyPlain(plan); } catch (error) { errors.push("plan contains non-plain or cyclic data"); }
    if (!validRevision(plan.snapshotRevision)) errors.push("snapshotRevision must be a nonempty string or finite number");
    for (var i = 0; i < plan.outputIds.length; i++) {
        if (typeof plan.outputIds[i] !== "string" || !plan.outputIds[i]) errors.push("invalid output id");
        else if (ids["o:" + plan.outputIds[i]]) errors.push("duplicate output id: " + plan.outputIds[i]);
        else ids["o:" + plan.outputIds[i]] = true;
    }
    ids = Object.create(null);
    for (var j = 0; j < plan.moves.length; j++) {
        var move = plan.moves[j];
        if (!move || typeof move.identity !== "string" || !move.identity) errors.push("move[" + j + "] has invalid identity");
        else if (ids[move.identity]) errors.push("duplicate move identity: " + move.identity);
        else ids[move.identity] = true;
        if (!rectangle(move && move.fromRect) || !rectangle(move && move.toRect)) errors.push("move[" + j + "] has invalid rectangle");
    }
    if (plan.createdAt !== null && plan.createdAt !== undefined && !isFiniteNumber(plan.createdAt)) errors.push("createdAt must be finite or null");
    return { valid: errors.length === 0, errors: errors };
}

function validRevision(value) {
    return (typeof value === "string" && value.length > 0) || isFiniteNumber(value);
}

function multiply32(a, b) {
    // Low 32 bits, using only ES5 arithmetic/bitwise operators available in QML JS.
    return (((a & 65535) * b) + (((((a >>> 16) * b) & 65535) << 16))) | 0;
}

// Mulberry32 gives reproducible streams while remaining small and QML-friendly.
function seededRandom(seed) {
    if (!isFiniteNumber(seed) || Math.floor(seed) !== seed)
        throw new Error("seed must be a finite integer");
    var state = (seed >>> 0);
    return function() {
        state = (state + 0x6D2B79F5) >>> 0;
        var value = state;
        value = multiply32(value ^ (value >>> 15), value | 1);
        value ^= value + multiply32(value ^ (value >>> 7), value | 61);
        return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
    };
}

// Clock is injected so runtime adapters own the monotonic source.
function monotonicClock(readMilliseconds) {
    if (typeof readMilliseconds !== "function") throw new Error("clock reader required");
    var previous = -Infinity;
    return { now: function() {
        var value = readMilliseconds();
        if (!isFiniteNumber(value) || value < previous) throw new Error("clock must be finite and monotonic");
        previous = value;
        return value;
    } };
}

// QRectF edge subtraction can introduce tiny dimension noise after a move.
// Snap only dimensions within 1e-9 logical pixels of an integer; retain
// positions and substantive fractional dimensions exactly.
function normalizeReadbackRectangle(value) {
    if (!rectangle(value)) return null;
    var result={x:value.x,y:value.y,width:value.width,height:value.height};
    ["width","height"].forEach(function(key) {
        var nearest=Math.round(result[key]);
        if (nearest>0 && Math.abs(result[key]-nearest)<=1e-9) result[key]=nearest;
    });
    return result;
}

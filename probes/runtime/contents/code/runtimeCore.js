.pragma library

// Shared by the KWin script and the Node test loader. Keep this module free of
// Qt/KWin globals so it can validate the protocol and snapshots headlessly.
function boundedExchangeResult(epoch, requestId, currentEpoch, currentRequestId, reply) {
    if (epoch !== currentEpoch || requestId !== currentRequestId) {
        return { accepted: false, reason: "stale" };
    }
    if (!reply || reply.ok !== true) {
        return { accepted: true, ok: false, reason: "failed" };
    }
    return { accepted: true, ok: true, payload: reply.payload };
}

function windowSnapshot(window, area) {
    function read(name) {
        try {
            const value = window[name];
            return value === undefined ? null : value;
        } catch (error) {
            return null;
        }
    }
    const geometry = read("frameGeometry");
    const output = read("output");
    const internalId = read("internalId");
    return {
        identity: internalId === null ? null : String(internalId),
        appId: read("desktopFileName") || read("resourceClass") || null,
        pid: read("pid"),
        minimized: read("minimized"),
        maximized: read("maximized"),
        fullScreen: read("fullScreen"),
        moveable: read("moveable"),
        resizeable: read("resizeable"),
        output: output ? {
            name: output.name === undefined ? null : output.name,
            geometry: rect(output.geometry),
        } : null,
        workArea: rect(area),
        geometry: rect(geometry),
    };
}

function rect(value) {
    if (!value) return null;
    return { x: value.x, y: value.y, width: value.width, height: value.height };
}

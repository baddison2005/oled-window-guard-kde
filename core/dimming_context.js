.pragma library
.import "dimming_options.js" as Options
.import "model.js" as Model

// Normalize authoritative compositor facts into the pure dimming engine.
// Unknown visibility/geometry fails bright rather than painting stale pixels.
function shellWindow(window) {
    if (!window || window.windowType==="desktop") return false;
    var app=(window.appId || "").replace(/\.desktop$/i,"").toLowerCase();
    return window.windowType==="dock" || window.windowType==="panel" ||
        app==="plasmashell" || app==="org.kde.plasmashell" ||
        (app==="cairo-dock" && window.windowType!=="normal" && window.windowType!=="dialog") ||
        (app==="oled-window-guard" && window.windowType!=="normal");
}
function pointerInteraction(snapshot, point, pointerIdentity) {
    function hit(w) {
        var r=w.rect;
        return w.visible && !w.minimized && w.onCurrentDesktop && w.onCurrentActivity && r &&
            point.x>=r.x && point.y>=r.y && point.x<r.x+r.width && point.y<r.y+r.height;
    }
    var output=snapshot.outputs.filter(function(o){var r=o.geometry;return point.x>=r.x && point.y>=r.y && point.x<r.x+r.width && point.y<r.y+r.height;})[0];
    if (!output) return {type:"reset"};
    var front=snapshot.windows.filter(function(w){return w.identity===pointerIdentity && hit(w);})[0] ||
        snapshot.windows.slice().reverse().filter(hit)[0];
    if (shellWindow(front)) return {type:"shellInteraction",displayId:output.id};
    return front && front.windowType!=="desktop" ?
        {type:"windowClick",displayId:output.id,windowId:front.identity} : {type:"desktopClick",displayId:output.id};
}
function context(snapshot, settings, now, status, aliases, previousFocusId) {
    if (!snapshot || !Array.isArray(snapshot.outputs) || !Array.isArray(snapshot.windows) ||
            !settings || !Array.isArray(settings.dimmingExcludedApps)) throw new Error("invalid-dimming-context");
    var displays = snapshot.outputs.map(function(o) {
        var resolved = Options.resolveDimmingOptions(settings,o.id);
        if (!resolved) throw new Error("invalid-dimming-options");
        return {id:o.id,frame:o.geometry,options:resolved.options};
    });
    var windows=[], focus=null, focusOutput=null;
    var reported=snapshot.windows.filter(function(w){return w.identity===snapshot.focusedIdentity;})[0];
    var focusedIdentity=shellWindow(reported)?previousFocusId:snapshot.focusedIdentity;
    // Workspace.stackingOrder is bottom-to-top; masks require front-to-back.
    snapshot.windows.slice().reverse().forEach(function(w) {
        if (w.visible===false || w.minimized===true || w.onCurrentDesktop===false || w.onCurrentActivity===false) return;
        if (w.visible!==true || w.minimized!==false || w.onCurrentDesktop!==true ||
                w.onCurrentActivity!==true || !w.rect) throw new Error("unknown-dimming-window-state");
        if (w.windowType==="desktop" || (w.windowType==="dock" && w.appId==="cairo-dock")) return;
        var allowed=w.managed===true && w.windowType==="normal" && w.modal===false &&
            w.transient===false && typeof w.appId==="string" && w.appId && w.appId!=="oled-window-guard" &&
            !Model.applicationExcluded(w.appId,settings.dimmingExcludedApps,aliases);
        windows.push({id:w.identity,frame:w.rect,dimmable:!!allowed,
            displayDimmable:w.windowType==="dock" || w.windowType==="panel"});
        if (w.identity===focusedIdentity && !shellWindow(w)) {
            focus=w;
            focusOutput=displays.filter(function(d){return w.outputIds.indexOf(d.id)>=0})[0];
        }
    });
    if (focus && !focusOutput) throw new Error("unknown-dimming-focus-output");
    return {nowMs:now,status:status,displays:displays,
        selectedOutputIds:displays.filter(function(d){return settings.dimmingDisplays.indexOf(d.id)>=0}).map(function(d){return d.id}),
        windows:windows,focusedFrame:focus?focus.rect:null,focusedWindowId:focus?focus.identity:null,
        focusedDisplayId:focusOutput?focusOutput.id:null};
}

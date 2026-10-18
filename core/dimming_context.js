.pragma library
.import "dimming_options.js" as Options
.import "model.js" as Model

// Normalize authoritative compositor facts into the pure dimming engine.
// Unknown visibility/geometry fails bright rather than painting stale pixels.
function context(snapshot, settings, now, status, aliases) {
    if (!snapshot || !Array.isArray(snapshot.outputs) || !Array.isArray(snapshot.windows) ||
            !settings || !Array.isArray(settings.dimmingExcludedApps)) throw new Error("invalid-dimming-context");
    var displays = snapshot.outputs.map(function(o) {
        var resolved = Options.resolveDimmingOptions(settings,o.id);
        if (!resolved) throw new Error("invalid-dimming-options");
        return {id:o.id,frame:o.geometry,options:resolved.options};
    });
    var windows=[], focus=null, focusOutput=null;
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
        if (w.identity===snapshot.focusedIdentity) {
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

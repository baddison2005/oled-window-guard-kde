.pragma library

// Settings are validated and default-filled by the canonical JSON Schema
// reader. This resolver applies one output's optional overrides only.
function resolveDimmingOptions(settings, outputId) {
    function numeric(value, max) { return typeof value === "number" && isFinite(value) && value >= 0 && value <= max; }
    if (!settings || typeof outputId !== "string" || !outputId ||
            !Array.isArray(settings.dimmingDisplays) ||
            typeof settings.dimWindows !== "boolean" || typeof settings.dimDisplays !== "boolean" ||
            !numeric(settings.windowDimPercent,90) || !numeric(settings.displayDimPercent,90) ||
            !numeric(settings.windowDimDelay,600) || !numeric(settings.displayDimDelay,600) ||
            !numeric(settings.windowDimFade,3) || !numeric(settings.displayDimFade,3)) return null;
    var overrides=settings.dimmingOverrides || {}, own=Object.prototype.hasOwnProperty.call(overrides,outputId);
    var override=own ? overrides[outputId] : {};
    if (!override || typeof override !== "object" || Array.isArray(override) ||
            (override.windows !== undefined && typeof override.windows !== "boolean") ||
            (override.display !== undefined && typeof override.display !== "boolean")) return null;
    var fields=[["windowDimPercent",90],["displayDimPercent",90],["windowDimDelay",600],
        ["displayDimDelay",600],["windowDimFade",3],["displayDimFade",3]];
    for (var i=0;i<fields.length;i++) if (override[fields[i][0]] !== undefined &&
            !numeric(override[fields[i][0]],fields[i][1])) return null;
    var desktopClickBehavior=override.desktopClickBehavior === undefined ? (settings.desktopClickBehavior || "stay-bright") : override.desktopClickBehavior;
    if (["stay-bright","restart-timers"].indexOf(desktopClickBehavior)<0) return null;
    function value(name) { return override[name] === undefined ? settings[name] : override[name]; }
    var selected=settings.dimmingDisplays.indexOf(outputId)>=0;
    return {selected:selected,options:{
        desktopClickBehavior:desktopClickBehavior,
        windows:selected && settings.dimWindows && override.windows !== false,
        display:selected && settings.dimDisplays && override.display !== false,
        windowAmount:value("windowDimPercent")/100,
        displayAmount:value("displayDimPercent")/100,
        windowDelayMs:value("windowDimDelay")*1000,
        displayDelayMs:value("displayDimDelay")*1000,
        windowFadeMs:value("windowDimFade")*1000,
        displayFadeMs:value("displayDimFade")*1000
    }};
}

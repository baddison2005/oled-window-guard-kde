.pragma library
.import "dimming_plan.js" as Plan
.import "dimming_timing.js" as Timing

// Pure composition. Inputs are validated, resolved display options and a
// front-to-back window list; the host owns sampling, effects and shortcuts.
function createDimmingState() { return { outputs:{}, desktopScopes:{}, forcedDisplays:{}, scope:Timing.createDimmingScopeState(),
    restore:Timing.createBrightnessRestoreState() }; }
function engineCopy(value) { return JSON.parse(JSON.stringify(value)); }
function engineFinite(value) { return typeof value === "number" && isFinite(value); }
function engineRect(rect) { return !!rect && engineFinite(rect.x) && engineFinite(rect.y) &&
    engineFinite(rect.width) && engineFinite(rect.height) && rect.width > 0 && rect.height > 0 &&
    engineFinite(rect.x + rect.width) && engineFinite(rect.y + rect.height); }
function engineValid(event) {
    if (!event || !engineFinite(event.nowMs) || !Array.isArray(event.displays) ||
            !Array.isArray(event.windows) || !event.displays.length || event.displays.length > 16 ||
            event.windows.length > 512 || !Object.prototype.hasOwnProperty.call(event,"focusedFrame") ||
            (event.focusedFrame !== null && !engineRect(event.focusedFrame))) return false;
    var ids = Object.create(null), windowIds = Object.create(null);
    for (var i=0;i<event.displays.length;i++) {
        var d=event.displays[i], o=d && d.options;
        if (!d || typeof d.id !== "string" || !d.id || ids["$"+d.id] || !engineRect(d.frame) ||
                !o || typeof o.windows !== "boolean" || typeof o.display !== "boolean" ||
                !engineFinite(o.windowAmount) || o.windowAmount < 0 || o.windowAmount > .9 ||
                !engineFinite(o.displayAmount) || o.displayAmount < 0 || o.displayAmount > .9 ||
                !engineFinite(o.windowDelayMs) || o.windowDelayMs < 0 ||
                !engineFinite(o.displayDelayMs) || o.displayDelayMs < 0 ||
                !engineFinite(o.windowFadeMs) || o.windowFadeMs < 0 ||
                !engineFinite(o.displayFadeMs) || o.displayFadeMs < 0) return false;
        ids["$"+d.id]=true;
    }
    for (var j=0;j<event.windows.length;j++) {
        var w=event.windows[j];
        if (!w || typeof w.id !== "string" || !w.id || windowIds["$"+w.id] ||
                !engineRect(w.frame) || typeof w.dimmable !== "boolean") return false;
        windowIds["$"+w.id]=true;
    }
    if (!Array.isArray(event.selectedOutputIds)) return false;
    if ((event.focusedFrame === null && (event.focusedWindowId !== null || event.focusedDisplayId !== null)) ||
            (event.focusedFrame !== null && (typeof event.focusedWindowId !== "string" ||
                !event.focusedWindowId || typeof event.focusedDisplayId !== "string" ||
                !ids["$"+event.focusedDisplayId]))) return false;
    var selected=Object.create(null);
    for (var k=0;k<event.selectedOutputIds.length;k++) {
        var id=event.selectedOutputIds[k];
        if (typeof id !== "string" || !ids["$"+id] || selected["$"+id]) return false;
        selected["$"+id]=true;
    }
    return true;
}
function engineBlank(state, reason) {
    state.outputs={};
    state.forcedDisplays={};
    return {state:state, masks:[], bright:true, reason:reason};
}
function updateDimming(input,event) {
    var state=engineCopy(input || createDimmingState());
    if (event && event.restoreBrightness === true && engineFinite(event.nowMs)) {
        var hold=5000;
        if (Array.isArray(event.displays)) for (var h=0;h<event.displays.length;h++) {
            var opt=event.displays[h] && event.displays[h].options;
            if (opt) hold=Math.max(hold,opt.windowDelayMs || 0,opt.displayDelayMs || 0);
        }
        state.restore=Timing.restoreDimmingBrightness(state.restore,event.nowMs,hold);
        return engineBlank(state,"restore-brightness");
    }
    if (!engineValid(event)) return engineBlank(state,"invalid-input");
    state.forcedDisplays=state.forcedDisplays || {};
    if (Array.isArray(event.dimNowOutputIds)) {
        // The compositor resolves the requested IDs against its active guard
        // scope. This engine repeats selection/option checks defensively.
        event.dimNowOutputIds.forEach(function(id) {
            var display=event.displays.filter(function(d){return d.id===id;})[0];
            if (display && display.options.display && display.options.displayAmount>0 &&
                    event.selectedOutputIds.indexOf(id)>=0) {
                state.forcedDisplays[id]=event.nowMs;
                delete state.desktopScopes[id];
            }
        });
    }
    state.desktopScopes=state.desktopScopes || {};
    var interaction=event.scopeEvent;
    if (interaction) {
        var target=interaction.displayId || interaction.pointerDisplayId || event.focusedDisplayId;
        if (interaction.type==="reset") state.forcedDisplays={};
        else if (target) delete state.forcedDisplays[target];
        if (interaction.type==="reset") {state.desktopScopes={};state.outputs={};}
        else if (target && (interaction.type==="desktopClick" || interaction.type==="desktopSwitch" || interaction.type==="activitySwitch")) {
            state.desktopScopes[target]={desktop:interaction.type==="desktopClick"};
            delete state.outputs[target];
        } else if (target && (interaction.type==="windowClick" || interaction.type==="windowKey" || interaction.type==="windowInteraction"))
            delete state.desktopScopes[target];
    }
    // Desktop holds belong to their display. Interacting on another screen
    // cannot release a hold or restart that screen's independent timers.
    var focusId=event.focusedWindowId, focusDisplay=event.focusedDisplayId;
    var focusScope=state.desktopScopes[focusDisplay];
    if (focusScope && focusScope.desktop) {focusId=null;focusDisplay=null;}
    else if (focusScope && focusId) delete state.desktopScopes[focusDisplay];
    var effectiveFocus=focusId ? event.focusedFrame : null;
    if (Timing.dimmingMustBeBright(event.status)) return engineBlank(state,"unavailable-or-paused");
    var restoring=Timing.isBrightnessRestoreActive(state.restore,event.nowMs);
    if (restoring && !Object.keys(state.forcedDisplays).length) return engineBlank(state,"restore-hold");
    var next={}, masks=[];
    for (var i=0;i<event.displays.length;i++) {
        var d=event.displays[i], id=d.id, o=d.options;
        var forced=Object.prototype.hasOwnProperty.call(state.forcedDisplays,id);
        if (restoring && !forced) continue;
        var hold=state.desktopScopes[id];
        if (event.selectedOutputIds.indexOf(id)<0 ||
                (hold && (!hold.desktop || o.desktopClickBehavior!=="restart-timers"))) continue;
        var prior=state.outputs[id] || {}, visible=[];
        for (var j=0;j<event.windows.length;j++) {
            var w=event.windows[j];
            if (Plan.overlaps(w.frame,d.frame)) visible.push(w);
        }
        // Geometry changes relocate masks without resetting inactivity. Only
        // interaction with that window interrupts its continuous eligibility.
        visible.forEach(function(w) {
            if (!interaction || interaction.windowId!==w.id) return;
            if (prior.windowDelay) delete prior.windowDelay.since["$"+w.id];
            if (prior.windowFade) delete prior.windowFade.starts["$"+w.id];
        });
        var protectedIds=event.protectedWindowIds || [];
        var candidateIds=o.windows ? visible.filter(function(w) { return w.dimmable && w.id!==focusId &&
            protectedIds.indexOf(w.id)<0; }).map(function(w) { return w.id; }) : [];
        var wd=Timing.updateDimmingDelay(prior.windowDelay,candidateIds,o.windowDelayMs,event.nowMs);
        var wf=Timing.updateDimmingFade(prior.windowFade,wd.ready,o.windowFadeMs,event.nowMs);
        var displayEligible=o.display && (forced || !(effectiveFocus && Plan.overlaps(effectiveFocus,d.frame)));
        var dd=Timing.updateDimmingDelay(prior.displayDelay,displayEligible ? [id] : [],forced?0:o.displayDelayMs,event.nowMs);
        var df=Timing.updateDimmingFade(prior.displayFade,dd.ready,o.displayFadeMs,event.nowMs);
        next[id]={windowDelay:wd.state,windowFade:wf.state,displayDelay:dd.state,displayFade:df.state};
        var ready=Object.create(null);
        for (var r=0;r<wd.ready.length;r++) ready["$"+wd.ready[r]]=true;
        var planned=Plan.regions({display:d.frame,windows:visible.map(function(w) { return {
            frame:w.frame,dimmable:w.dimmable,displayDimmable:w.displayDimmable===true,ready:!!ready["$"+w.id],fadeProgress:wf.progress[w.id] || 0
        }; }),focused:forced?null:effectiveFocus,
            windowAmount:o.windows ? o.windowAmount : 0,
            displayAmount:displayEligible && dd.ready.length ? o.displayAmount : 0,
            displayProgress:df.progress[id] || 0});
        for (var p=0;p<planned.length;p++) if (planned[p].amount>0)
            masks.push({outputId:id,frame:planned[p].frame,amount:planned[p].amount,wholeDisplay:displayEligible && dd.ready.length>0 && (df.progress[id] || 0)>0});
    }
    state.outputs=next;
    Object.keys(state.forcedDisplays).forEach(function(id){if (event.selectedOutputIds.indexOf(id)<0 || !event.displays.some(function(d){return d.id===id;})) delete state.forcedDisplays[id];});
    Object.keys(state.desktopScopes).forEach(function(id){if (!event.displays.some(function(d){return d.id===id;})) delete state.desktopScopes[id];});
    return {state:state,masks:masks,bright:masks.length===0,reason:masks.length ? "planned" : "no-eligible-region"};
}

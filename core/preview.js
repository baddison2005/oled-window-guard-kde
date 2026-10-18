.pragma library
.import "model.js" as Model
.import "registry.js" as Registry

// Read-only display data. Opaque window identities and executable plans stay local.
function drawing(snapshot, plan, outputId, settings, aliases) {
    var output=snapshot.outputs.filter(function(o){return o.id===outputId;})[0];
    if (!output) return null;
    var windows=snapshot.windows.filter(function(w){return w.visible && w.minimized===false && w.onCurrentDesktop && w.onCurrentActivity &&
        ["desktop","dock","panel","guard","outline","notification","osd"].indexOf(w.windowType)<0 && w.outputIds.indexOf(outputId)>=0 && Model.rectangle(w.rect);});
    var policy={sessionEpoch:"preview",selectedOutputIds:(settings && settings.movementDisplays) || [],
        allowIdleFocused:true,excludeFocused:false,excludedApps:settings?settings.excludedApps:[],
        applicationAliases:aliases || {},ownAppIds:["oled-window-guard"]};
    var classified={};
    try {Registry.reconcile(snapshot,Registry.createRegistryState("preview"),policy).windows.forEach(function(w){classified[w.identity]=w;});}
    catch(error) { /* Unproven snapshot facts cannot label a window eligible. */ }
    var shown=windows.slice(-32),moves=plan?plan.moves:[];
    return {outputId:outputId,geometry:output.geometry,totalWindows:windows.length,
        windows:shown.map(function(w){return w.rect;}),
        windowInfo:shown.map(function(w){
            var facts=classified[w.identity] || {candidate:false,skipReasons:["snapshot-unavailable"]};
            return {appId:String(w.appId || "").replace(/[\x00-\x1f\x7f-\x9f]/g,"").slice(0,128),
                eligibility:facts.skipReasons.indexOf("excluded_app")>=0?"excluded":facts.candidate?"eligible":"unavailable",
                reason:String(facts.skipReasons[0] || "").slice(0,64),moveNumber:moves.findIndex(function(m){return m.identity===w.identity;})+1};
        }),destinations:moves.map(function(m){return {from:m.fromRect,to:m.toRect};})};
}

function boundedPayload(payload, limit) {
    var wire=JSON.parse(JSON.stringify(payload));
    // ASCII escaping makes length equal UTF-8 bytes, including Unicode app IDs.
    function encoded() {return JSON.stringify(wire).replace(/[\u007f-\uffff]/g,function(c){return "\\u"+("0000"+c.charCodeAt(0).toString(16)).slice(-4);});}
    var text=encoded(),drawings=[wire.livePreview,wire.restorePreview,wire.runtime && wire.runtime.restorePreview,wire.report && wire.report.preview].filter(function(d){return d && Array.isArray(d.windows);});
    while (text.length>limit) {
        drawings.sort(function(a,b){return b.windows.length-a.windows.length;});
        if (!drawings.length || !drawings[0].windows.length) break;
        // Trim only diagram rows; preserve every proposed destination and total.
        var count=Math.min(4,drawings[0].windows.length);
        drawings[0].windows.splice(0,count);
        if (drawings[0].windowInfo) drawings[0].windowInfo.splice(0,count);
        text=encoded();
    }
    return text;
}

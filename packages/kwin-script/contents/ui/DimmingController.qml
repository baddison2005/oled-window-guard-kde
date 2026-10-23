import QtQuick
import org.kde.kwin
import "core/dimming_engine.js" as Engine
import "core/dimming_context.js" as Context
import "core/dimming_timing.js" as Timing
import "core/dimming_options.js" as Options

QtObject {
    id:dimmer
    required property var observer
    property var state:Engine.createDimmingState()
    property var surfaces:({})
    property var lastInput:null
    property var pendingScope:null
    property var protectedWindowIds:[]
    property bool healthy:true
    property string lastReason:"paused"
    property int maskCount:0
    property var shortcut:null
    property bool stopping:false
    property var pendingDimOutputIds:[]
    function status() {
        var visible=0;
        for (var id in surfaces) if (surfaces[id].confirmed && surfaces[id].plane.visible) visible+=surfaces[id].masks.length;
        return {healthy:healthy,maskCount:visible,reason:lastReason};
    }
    function isOwnSurface(window) {
        for (var id in surfaces) if (surfaces[id].isOwnSurface(window)) return true;
        return false;
    }
    function selected() {
        var config=observer.planning.configuration;
        if (!config || !healthy) return [];
        var settings=config.settings;
        if (!settings.dimWindows && !settings.dimDisplays) return [];
        return observer.outputsNow().filter(function(o){return settings.dimmingDisplays.indexOf(o.id)>=0}).map(function(o){return o.id});
    }
    function clear(reason) {
        for (var id in surfaces) surfaces[id].clear();
        state.outputs={};state.forcedDisplays={};pendingDimOutputIds=[];maskCount=0;lastReason=reason;
    }
    function retainOutputs(ids) {
        for(var id in surfaces)if(ids.indexOf(id)<0)surfaces[id].clear();
        for(var id in state.outputs)if(ids.indexOf(id)<0)delete state.outputs[id];
        for(var id in state.desktopScopes)if(ids.indexOf(id)<0)delete state.desktopScopes[id];
    }
    function fail(reason) {healthy=false;clear(reason);}
    function stop() {
        if (stopping) return;
        stopping=true;timer.stop();nativeSignals.target=null;windowSignals.target=null;warningSignals.target=null;clear("shutdown");
        if (shortcut) {shortcut.destroy();shortcut=null;}
    }
    function restoreBrightness() {
        var now=observer.runtime.schedulerNow(), delay=5000;
        var config=observer.planning.configuration;
        clear("restore-brightness");
        if (config) {
            observer.outputsNow().forEach(function(d){
                var resolved=Options.resolveDimmingOptions(config.settings,d.id);
                if (resolved) delay=Math.max(delay,resolved.options.windowDelayMs,resolved.options.displayDelayMs);
            });
        }
        state.restore=Timing.restoreDimmingBrightness(state.restore,now,delay);
    }
    function manualOutputs() {
        const config=observer.planning.configuration;
        if (!config || !healthy) return [];
        return observer.runtime.activeOutputs("brightness").filter(function(id) {
            const resolved=Options.resolveDimmingOptions(config.settings,id);
            return resolved && resolved.options.display && resolved.options.displayAmount>0;
        });
    }
    function dimNow(ids) {
        const allowed=manualOutputs();
        if (!ids.length || ids.some(function(id){return allowed.indexOf(id)<0;})) return false;
        pendingDimOutputIds=ids.slice();pendingScope=null;tick();return true;
    }
    function pointerScope(type) {
        var snapshot=observer.capture(), point=Workspace.cursorPos;
        var output=snapshot.outputs.filter(function(o){var r=o.geometry;return point.x>=r.x && point.y>=r.y && point.x<r.x+r.width && point.y<r.y+r.height;})[0];
        if (!output) return {type:"reset"};
        if (type!=="desktopClick") return {type:type,pointerDisplayId:output.id};
        var warning=observer.warningSurface, bounds=warning.targetGeometry;
        if (warning.activeGeneration>=0 && bounds && point.x>=bounds.x && point.y>=bounds.y && point.x<bounds.x+bounds.width && point.y<bounds.y+bounds.height)
            return {type:"guardInteraction",displayId:output.id};
        var front=snapshot.windows.slice().reverse().filter(function(w){var r=w.rect;return !(w.appId==="cairo-dock" && w.windowType==="dock") && w.visible && !w.minimized && w.onCurrentDesktop && w.onCurrentActivity && r && point.x>=r.x && point.y>=r.y && point.x<r.x+r.width && point.y<r.y+r.height;})[0];
        return front && front.windowType!=="desktop"?{type:"windowClick",displayId:output.id,windowId:front.identity}:{type:"desktopClick",displayId:output.id};
    }
    function inputChanged(reason) {
        if (stopping) return;
        try {
            var sample=observer.planning.nativeObservation.localSnapshot();
            if(reason==="native-topology") {lastInput=sample;return;}
            if (reason!=="native-input" && reason!=="native-focus") {
                clear(reason);pendingScope={type:"reset"};lastInput=sample;return;
            }
            var changed=reason==="native-focus";
            if (!sample.buttonsDown) protectedWindowIds=[];
            if (sample.buttonsDown && (!lastInput || sample.button!==lastInput.button)) {
                var scope=pointerScope("desktopClick");
                if (scope.windowId) protectedWindowIds=[scope.windowId];
                pendingScope=scope;changed=true;
            } else if (lastInput && sample.keyboard!==lastInput.keyboard) {
                pendingScope={type:"windowKey"};changed=true;
            } else if (lastInput && sample.axis!==lastInput.axis) {
                pendingScope=pointerScope("desktopClick");
                if (pendingScope.windowId) pendingScope.type="windowInteraction";
                else pendingScope=null; // scrolling empty desktop is not a click
                changed=true;
            }
            // Recompute fresh masks synchronously; preserve unrelated clocks.
            if (lastInput && sample.button!==lastInput.button) changed=true;
            lastInput=sample;
            // Pointer motion and drag motion use geometry notifications plus
            // the 50 ms renderer tick. Never run full captures per mouse sample.
            if (changed) tick();
        } catch(error) {clear("native-unavailable");}
    }
    function tick() {
        if (stopping || observer.shuttingDown) return;
        var config=observer.planning.configuration;
        var contactAge=Date.now()-observer.planning.lastContact;
        var wantedShortcut=!!config && config.settings.brightnessShortcutEnabled;
        if (wantedShortcut && !shortcut) shortcut=shortcutFactory.createObject(dimmer);
        if (!wantedShortcut && shortcut) {shortcut.destroy();shortcut=null;}
        if (!healthy || !config || (typeof observer.planning.lastContact==="number" && (contactAge<0 || contactAge>=500)) || !observer.runtime.brightnessEnabled || !observer.runtime.dimmingFacts().safe) {clear("unavailable-or-paused");return;}
        try {
            var event=Context.context(observer.capture(),Object.assign({},config.settings,{dimmingDisplays:observer.runtime.activeOutputs("brightness")}),observer.runtime.schedulerNow(),
                {available:true,rendererProven:healthy,paused:false,locked:false,inactive:false,sleeping:false,rendererError:false},config.applicationAliases);
            if (observer.warningSurface.activeGeneration>=0 && observer.warningSurface.targetGeometry)
                event.windows.unshift({id:"owg-warning",frame:observer.warningSurface.targetGeometry,dimmable:false,displayDimmable:true});
            event.scopeEvent=pendingScope;pendingScope=null;
            event.dimNowOutputIds=pendingDimOutputIds;pendingDimOutputIds=[];
            event.protectedWindowIds=protectedWindowIds;
            var result=Engine.updateDimming(state,event);state=result.state;
            if (result.reason==="invalid-input") throw new Error("invalid-dimming-input");
            if (result.masks.length>256) throw new Error("dimming-mask-limit");
            var connected={};
            event.displays.forEach(function(output) {
                connected[output.id]=true;
                var regions=result.masks.filter(function(m){return m.outputId===output.id;});
                if (regions.length && !surfaces[output.id]) {
                    var object=surfaceFactory.createObject(dimmer,{observer:observer});
                    if (!object) throw new Error("dimming-surface-unavailable");
                    surfaces[output.id]=object;
                }
                if (surfaces[output.id]) surfaces[output.id].present(output.frame,regions);
            });
            for (var id in surfaces) if (!connected[id]) {surfaces[id].clear();surfaces[id].destroy();delete surfaces[id];}
            maskCount=result.masks.length;lastReason=result.reason;
        } catch(error) {clear(String(error).slice(0,128));}
    }
    property Component surfaceFactory: Component {DimmingSurface {onFailed:function(reason){dimmer.fail(reason);}}}
    property Component shortcutFactory: Component {
        ShortcutHandler {name:"OLEDWindowGuardRestoreBrightness";text:"OLED Window Guard: Restore Brightness";sequence:"Meta+Shift+B";onActivated:dimmer.restoreBrightness()}
    }
    property Connections nativeSignals: Connections {
        target:observer.planning.nativeObservation
        function onActivityObserved(reason) {dimmer.inputChanged(reason);}
    }
    property Connections warningSignals: Connections {
        target:observer.warningSurface
        ignoreUnknownSignals:true
        function onShown(generation) {dimmer.tick();}
        function onHidden(generation) {dimmer.tick();}
    }
    property Connections windowSignals: Connections {
        target:observer
        function onSnapshotCaptured(snapshot,reason) {
            if (reason==="timer" || reason==="startup") return;
            // Changes to our own planes do not affect the underlying snapshot.
            var key=JSON.stringify(snapshot.windows)+JSON.stringify(snapshot.outputs);
            if (key===dimmer.lastSnapshotKey) return;
            dimmer.lastSnapshotKey=key;
            if (reason==="outputs") dimmer.retainOutputs(observer.runtime.activeOutputs("brightness"));
            if (reason==="desktop" || reason==="activity")
                dimmer.pendingScope=dimmer.pointerScope(reason==="desktop"?"desktopSwitch":"activitySwitch");
            dimmer.tick();
        }
    }
    property string lastSnapshotKey:""
    property Timer timer: Timer {interval:50;repeat:true;running:true;onTriggered:dimmer.tick()}
    Component.onDestruction: stop()
}

import QtQuick
import org.kde.kwin
import "ui"

QtObject {
    id:probe
    property var settings:null
    property var outputs:[]
    property var windows:[]
    property string focused:"front"
    property double clock:0
    property int stage:0
    property int waits:0
    property bool safe:true
    property bool enabled:true
    property bool shuttingDown:false
    signal snapshotCaptured(var snapshot,string reason)
    property QtObject planning:QtObject {
        property var configuration:({settings:probe.settings})
        property QtObject nativeObservation:QtObject {
            signal canceled(string reason)
            signal activityObserved(string reason)
            property bool immediateCancellation:false
            function localSnapshot(){return {buttonsDown:false,keyboard:0};}
        }
    }
    property QtObject runtime:QtObject {
        property bool enabled:probe.enabled
        property bool brightnessEnabled:probe.enabled
        function activeOutputs(kind){return probe.settings ? probe.settings.dimmingDisplays : [];}
        property bool busy:false
        function dimmingFacts(){return {safe:probe.safe};}
        function facts(quiet){return {safe:probe.safe};}
        function schedulerNow(){return probe.clock;}
    }
    property QtObject warningSurface:QtObject {property int activeGeneration:-1;property var targetGeometry:null}
    property DimmingController dimming:DimmingController {observer:probe}
    function outputsNow(){return outputs;}
    function capture(){return {outputs:outputs,windows:windows,focusedIdentity:focused};}
    function check(condition,reason){if(!condition)throw new Error(reason);}
    function confirmed(){for(var id in dimming.surfaces)if(dimming.surfaces[id].masks.length && !dimming.surfaces[id].confirmed)return false;return true;}
    function planes(){return (Workspace.stackingOrder || []).filter(function(w){return dimming.isOwnSurface(w) && !w.hidden;}).length;}
    function row(id,x,output){return {identity:id,appId:"probe",rect:{x:x,y:output.geometry.y+80,width:180,height:110},outputIds:[output.id],visible:true,minimized:false,onCurrentDesktop:true,onCurrentActivity:true,managed:true,modal:false,transient:false,windowType:"normal"};}
    Component.onCompleted: {
        var screens=Workspace.screens;
        check(screens.length===2,"two virtual outputs required");
        outputs=[{id:"A",geometry:screens[0].geometry},{id:"B",geometry:screens[1].geometry}];
        windows=[row("back",outputs[0].geometry.x+20,outputs[0]),row("front",outputs[0].geometry.x+240,outputs[0])];
        settings={dimmingDisplays:["A"],dimmingExcludedApps:[],dimWindows:true,dimDisplays:false,windowDimPercent:30,displayDimPercent:40,windowDimDelay:0,displayDimDelay:0,windowDimFade:0,displayDimFade:0,dimmingOverrides:{},brightnessShortcutEnabled:false};
    }
    property Timer steps:Timer {
        interval:100;repeat:true;running:true
        onTriggered: {
            probe.clock+=100;
            try {
                if (++probe.waits>150)throw new Error("probe timeout at stage "+probe.stage+" reason "+dimming.lastReason);
                if (probe.stage===0) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    check(probe.planes()===1,"selected output scope");
                    var surface=dimming.surfaces.A;
                    check((surface.plane.flags & Qt.WindowTransparentForInput)!==0 && surface.plane.outputOnly,"input transparency");
                    check((surface.plane.flags & Qt.WindowDoesNotAcceptFocus)!==0,"focus protection");
                    check(surface.plane.color.a===0,"unmasked pixels must stay transparent");
                    check(surface.masks.length===1 && surface.masks[0].frame.x===windows[0].rect.x && surface.masks[0].amount===.3,"unfocused window mask");
                    settings=Object.assign({},settings,{dimWindows:false,dimDisplays:true,dimmingDisplays:["A","B"]});
                    dimming.clear("settings-changed");probe.stage++;
                } else if (probe.stage===1) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    check(dimming.surfaces.A.masks.length===0,"focused monitor stays bright");
                    check(dimming.surfaces.B.masks.length===1 && dimming.surfaces.B.masks[0].amount===.4,"whole other monitor dimmed");
                    check(dimming.dimNow(["A"]),"manual selected display accepted");probe.stage=7;
                } else if (probe.stage===7) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    check(dimming.surfaces.A.masks.length===1 && dimming.surfaces.A.masks[0].amount===.4,"manual dim covers focused display");
                    check(dimming.surfaces.B.masks.length===1,"other display unaffected");
                    dimming.pendingScope={type:"shellInteraction",displayId:"A"};dimming.tick();
                    check(dimming.surfaces.A.masks.length===1 && dimming.surfaces.B.masks.length===1,"shell menu preserves dimming");
                    check(dimming.restorableOutputs().length===2,"both rendered monitors available to restore");
                    check(dimming.restoreBrightness(["A"]),"scoped restore accepted");
                    check(dimming.surfaces.A.masks.length===0 && dimming.surfaces.B.masks.length===1,"scoped restore retains other monitor");
                    check(dimming.dimNow(["A"]),"manual dim overrides only scoped restore hold");probe.stage=8;
                } else if (probe.stage===8) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    check(dimming.surfaces.A.masks.length===1 && dimming.surfaces.B.masks.length===1,"both monitors dim again");
                    dimming.pendingScope={type:"windowKey",displayId:"A"};dimming.tick();
                    check(dimming.surfaces.A.masks.length===0 && dimming.surfaces.B.masks.length===1,"interaction clears only selected manual dim");
                    check(dimming.dimNow(["A","B"]),"manual all guarded displays accepted");
                    dimming.restoreBrightness();check(dimming.maskCount===0 && probe.planes()===0,"synchronous brightness restore");probe.stage=2;
                } else if (probe.stage===2) {
                    check(dimming.maskCount===0,"restore hold");
                    if(probe.clock<stateUntil())return;
                    probe.stage++;
                } else if (probe.stage===3) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    probe.safe=false;dimming.inputChanged("native-session");
                    check(dimming.maskCount===0 && probe.planes()===0,"immediate session clear");probe.stage++;
                } else if (probe.stage===4) {
                    check(dimming.maskCount===0,"unavailable stays bright");
                    probe.safe=true;probe.stage++;
                } else if (probe.stage===5) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    probe.enabled=false;dimming.tick();check(probe.planes()===0,"pause clears");
                    probe.enabled=true;probe.stage++;
                } else if (probe.stage===6) {
                    if (!dimming.maskCount || !probe.confirmed())return;
                    dimming.fail("injected-renderer-failure");check(probe.planes()===0 && !dimming.healthy,"renderer failure clears");
                    print("OWG_DIMMING_RESULT PASS window monitor focus scope transparent restore hold session pause renderer-failure");stop();
                }
            } catch(error) {print("OWG_DIMMING_RESULT FAIL "+String(error));dimming.stop();stop();}
        }
    }
    function stateUntil(){return dimming.state.restore.until;}
    Component.onDestruction: {shuttingDown=true;dimming.stop();}
}

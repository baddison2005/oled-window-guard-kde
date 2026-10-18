import QtQuick
import org.kde.kwin
import org.kde.plasma.core as PlasmaCore

// One transparent, noninteractive compositor-owned plane per selected output.
QtObject {
    id: surface
    required property var observer
    property string caption: "OLED Window Guard dimming " + String(Math.random())
    property var bounds: null
    property var masks: []
    property int maskCapacity: 0
    function setMasks(regions) {
        // Keep delegates alive during geometry updates and interactive drags.
        if (JSON.stringify(masks)!==JSON.stringify(regions)) masks=regions;
        maskCapacity=Math.max(maskCapacity,regions.length);
    }
    property bool confirmed: false
    property bool healthy: true
    property bool placing: false
    property int checks: 0
    signal failed(string reason)
    function isOwnSurface(w) {
        return w && !w.deleted && String(w.caption || "")===caption &&
            Number(w.pid)===-1 && String(w.resourceClass || "")==="";
    }
    function ownWindow() {
        var stacking=Workspace.stackingOrder || [];
        for (var i=0;i<stacking.length;i++) if (isOwnSurface(stacking[i])) return stacking[i];
        return null;
    }
    function clear() {
        masks=[];confirmed=false;placing=false;placement.stop();plane.visible=false;
    }
    function fail(reason) {
        clear();healthy=false;failed(reason);
    }
    function geometryMatches(actual) {
        return actual && bounds && ["x","y","width","height"].every(function(k){return Math.abs(actual[k]-bounds[k])<=.5});
    }
    function present(frame, regions) {
        if (!healthy) return;
        if (!regions.length) {clear();return;}
        if (confirmed && geometryMatches(frame) && geometryMatches(ownWindow()?ownWindow().frameGeometry:null)) {
            setMasks(regions);positionLayer();return;
        }
        setMasks(regions);
        if (placing && geometryMatches(frame)) return;
        confirmed=false;bounds={x:Number(frame.x),y:Number(frame.y),width:Number(frame.width),height:Number(frame.height)};placing=true;checks=0;
        plane.x=frame.x;plane.y=frame.y;plane.width=frame.width;plane.height=frame.height;
        plane.visible=true;placement.start();
    }
    function positionLayer() {
        const native=observer.planning.nativeObservation;
        if (native.immediateCancellation)
            native.localObserver.positionDimmingPlane(caption,masks.some(function(m){return m.wholeDisplay===true;}));
    }
    property PlasmaCore.Dialog plane: PlasmaCore.Dialog {
        title:surface.caption
        location:PlasmaCore.Types.Floating
        backgroundHints:PlasmaCore.Types.NoBackground
        color:"transparent"
        outputOnly:true
        flags:Qt.Window | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint |
              Qt.WindowDoesNotAcceptFocus | Qt.WindowTransparentForInput
        hideOnWindowDeactivate:false
        visible:false
        mainItem: Item {
            width:plane.width;height:plane.height
            Repeater {
                model:surface.maskCapacity
                Rectangle {
                    required property int index
                    objectName:"dimmingMask"+index
                    readonly property var region:surface.masks[index] || null
                    visible:surface.confirmed && region!==null
                    x:region && surface.bounds?region.frame.x-surface.bounds.x:0;y:region && surface.bounds?region.frame.y-surface.bounds.y:0
                    width:region?region.frame.width:0;height:region?region.frame.height:0
                    color:"black";opacity:region?region.amount:0
                }
            }
        }
    }
    property Timer placement: Timer {
        interval:20;repeat:true
        onTriggered: {
          try {
            var own=surface.ownWindow();
            if (own) {
                own.keepAbove=true;own.skipTaskbar=true;own.skipPager=true;own.skipSwitcher=true;
                if (surface.geometryMatches(own.frameGeometry)) {
                    surface.confirmed=true;surface.placing=false;surface.positionLayer();stop();return;
                }
                own.frameGeometry=surface.bounds;
            }
            if (++surface.checks>=25) surface.fail("dimming-placement-unconfirmed");
          } catch(error) {surface.fail("dimming-placement-failed");}
        }
    }
    Component.onDestruction: clear()
}

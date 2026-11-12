import QtQuick
import org.kde.kwin
import "ui"

QtObject {
    id:probe
    property var cases:[]
    property int index:0
    property int stableTicks:0
    property var saved:null
    property var full:null
    property var usable:null
    property var priorFocus:null
    property bool skipped:false
    property var initialSeconds:0
    property WarningSurface presenter:WarningSurface {
        onShown:function(generation) {
            try {
                check(generation===100+probe.index,"generation");
                check(Workspace.activeWindow===probe.priorFocus,"warning stole focus");
                probe.saved=JSON.parse(JSON.stringify(targetGeometry));
                probe.initialSeconds=remainingSeconds;probe.stableTicks=0;stable.start();
            } catch(error) {probe.fail(error);}
        }
        onSkipped:function(generation) {probe.skipped=true;}
        onHidden:function(generation) {
            try {
                check(probe.skipped && surfaceCount()===0,"hide/skip confirmation");
                probe.index++;Qt.callLater(probe.next);
            } catch(error) {probe.fail(error);}
        }
        onFailed:function(generation,reason) {probe.fail(reason);}
        onExpired:function(generation) {probe.fail("unexpected expiry");}
    }
    function check(value,message) {if (!value)throw new Error(message);}
    function fail(reason) {stable.stop();print("OWG_PLACEMENT_FAIL "+String(reason));}
    function next() {
        if (index===cases.length) {print("OWG_PLACEMENT_PASS");return;}
        var c=cases[index],screen=Workspace.screens[c.output],g=screen.geometry;
        full={x:g.x,y:g.y,width:g.width,height:c.short?480:g.height};
        // Synthetic panel/work-area inset inside each real virtual output.
        usable={x:full.x+32,y:full.y+28,width:full.width-32,height:full.height-28};
        priorFocus=Workspace.activeWindow;skipped=false;
        presenter.restoring=c.restore;presenter.showForOutput(100+index,usable,3000,c.position,full);
    }
    property Timer stable:Timer {
        interval:100;repeat:true
        onTriggered: {
            try {
                check(JSON.stringify(presenter.targetGeometry)===JSON.stringify(saved),"position changed during countdown");
                check(presenter.remainingSeconds<=initialSeconds,"countdown increased");initialSeconds=presenter.remainingSeconds;
                var actual=presenter.ownWindow().frameGeometry,r=saved,c=cases[index];
                check(["x","y","width","height"].every(function(k){return Math.abs(actual[k]-r[k])<=1;}),"actual placement differs");
                check(r.x>=usable.x && r.y>=usable.y && r.x+r.width<=usable.x+usable.width && r.y+r.height<=usable.y+usable.height,"outside work area");
                if (c.position==="random") {
                    if (usable.y+r.height<=full.y+full.height/4)check(r.y+r.height<=full.y+full.height/4,"outside top quarter");
                    else check(r.y===usable.y,"short-display fallback");
                }
                if (++stableTicks<3)return;
                stop();print("OWG_PLACEMENT_SAMPLE "+JSON.stringify({output:c.output,position:c.position,restore:c.restore,short:!!c.short,frame:r,remainingSeconds:presenter.remainingSeconds}));
                presenter.onSkip();
            } catch(error) {stop();probe.fail(error);}
        }
    }
    Component.onCompleted: {
        check(Workspace.screens.length===2,"two outputs required");
        var list=[];
        for (var output=0;output<2;output++)for (var position of ["topLeft","topCenter","topRight","random"])
            for (var restore of [false,true])list.push({output:output,position:position,restore:restore});
        list.push({output:0,position:"random",restore:false,short:true});
        list.push({output:1,position:"random",restore:true,short:true});
        cases=list;Qt.callLater(next);
    }
}

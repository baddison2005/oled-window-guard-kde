import QtQuick
import org.kde.kwin

QtObject {
    id: probe
    property var owned: null
    property var target: __TARGET__
    property var outputs: __OUTPUTS__
    property int attempts: 0
    property bool ready: false
    property bool failed: false

    function fail(reason) {
        if (!failed) console.warn("OWG_DIRECT_SCOPE FAIL __INDEX__ " + reason)
        failed = true
        check.stop()
    }
    function geometryMatches(a, b) {
        return Math.abs(a.x - b.x) <= 1 && Math.abs(a.y - b.y) <= 1 &&
               Math.abs(a.width - b.width) <= 1 && Math.abs(a.height - b.height) <= 1
    }
    function verify() {
        if (!owned || owned.deleted || Workspace.activeWindow !== owned) {
            fail("owned-focus-lost")
            return
        }
        const screens = Workspace.screens || []
        if (screens.length !== outputs.length) {
            fail("output-count-changed")
            return
        }
        for (let i = 0; i < outputs.length; ++i) {
            const matching = screens.filter(screen => screen.name === outputs[i].id)
            if (matching.length !== 1 || !geometryMatches(matching[0].geometry, outputs[i]) ||
                    typeof matching[0].devicePixelRatio !== "number" ||
                    !isFinite(matching[0].devicePixelRatio) ||
                    Math.abs(matching[0].devicePixelRatio - outputs[i].scale) > 0.001) {
                fail("output-identity-geometry-scale-changed")
                return
            }
        }
        const frame = owned.frameGeometry
        if (!geometryMatches(frame, target)) {
            if (ready || ++attempts >= 30) fail("owned-frame-mismatch")
            return
        }
        if (!ready) {
            ready = true
            console.warn("OWG_DIRECT_SCOPE READY __INDEX__ " + JSON.stringify({
                frame: {x: frame.x, y: frame.y, width: frame.width, height: frame.height},
                focusOwned: true
            }))
        }
    }
    property Timer check: Timer {
        interval: 100
        repeat: true
        onTriggered: probe.verify()
    }
    Component.onCompleted: {
        owned = Workspace.activeWindow
        if (!owned || owned.deleted || owned.caption !== "OWG owned focus probe") {
            fail("owned-window-unavailable")
            return
        }
        owned.frameGeometry = target
        check.start()
    }
}

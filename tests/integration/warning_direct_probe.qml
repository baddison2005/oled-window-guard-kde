import QtQuick
import org.kde.kwin

// Test fixture only: one production surface, no transaction writer or synthetic Skip.
QtObject {
    id: probe
    property var owned: null
    property bool shownOnce: false
    property bool skippedOnce: false
    property bool finished: false
    function focusOK() {
        return owned && !owned.deleted && Workspace.activeWindow === owned
    }
    function fail(reason) {
        print("OWG_DIRECT_WARNING FAIL " + reason)
    }
    property WarningSurface presenter: WarningSurface {
        onShown: function(generation) {
            if (generation !== 17 || probe.shownOnce || !probe.focusOK() || surfaceCount() !== 1) {
                probe.fail("shown-invariant")
                return
            }
            probe.shownOnce = true
            const window = ownWindow(), frame = window.frameGeometry
            print("OWG_DIRECT_WARNING PLACEMENT_JSON " + JSON.stringify({
                x: frame.x, y: frame.y, width: frame.width, height: frame.height
            }))
            print("OWG_DIRECT_WARNING SHOWN focusUnchanged=true count=1")
        }
        onSkipped: function(generation) {
            if (generation !== 17 || !probe.shownOnce || probe.skippedOnce || !probe.focusOK()) {
                probe.fail("skip-invariant")
                return
            }
            probe.skippedOnce = true
            print("OWG_DIRECT_WARNING SKIP focusUnchanged=true")
        }
        onHidden: function(generation) {
            if (generation !== 17 || !probe.skippedOnce || !probe.focusOK() || surfaceCount() !== 0) {
                probe.fail("hidden-invariant")
                return
            }
            probe.finished = true
            print("OWG_DIRECT_WARNING HIDDEN focusUnchanged=true count=0")
        }
        onExpired: function(generation) { probe.fail("expired") }
        onFailed: function(generation, reason) { probe.fail(reason) }
    }
    property Timer monitor: Timer {
        interval: 100
        repeat: true
        running: true
        onTriggered: {
            if (!probe.shownOnce) return
            if (!probe.focusOK()) { probe.fail("focus-changed"); stop(); return }
            if (!probe.skippedOnce && (!presenter.warning.visible || presenter.surfaceCount() !== 1)) {
                probe.fail("outside-input-dismissed-warning"); stop()
            }
            if (probe.finished && presenter.surfaceCount() !== 0) {
                probe.fail("surface-reappeared"); stop()
            }
        }
    }
    property Timer start: Timer {
        interval: 800
        running: true
        onTriggered: {
            probe.owned = Workspace.activeWindow
            if (!probe.owned || String(probe.owned.caption) !== "OWG owned focus probe") {
                probe.fail("owned-client-not-focused"); return
            }
            const frame = probe.owned.frameGeometry, screens = Workspace.screens || []
            const x = frame.x + frame.width / 2, y = frame.y + frame.height / 2
            for (var i = 0; i < screens.length; i++) {
                const bounds = screens[i].geometry
                if (x >= bounds.x && x < bounds.x + bounds.width &&
                        y >= bounds.y && y < bounds.y + bounds.height) {
                    if (bounds.width < 420 || bounds.height < 320 ||
                            frame.y + frame.height < bounds.y + 250) {
                        probe.fail("owned-area-too-small"); return
                    }
                    print("OWG_DIRECT_WARNING OUTPUT geometry=" + String(bounds) +
                        " scale=" + String(screens[i].devicePixelRatio))
                    print("OWG_DIRECT_WARNING OUTPUT_JSON " + JSON.stringify({
                        id: screens[i].name, x: bounds.x, y: bounds.y,
                        width: bounds.width, height: bounds.height, scale: screens[i].devicePixelRatio
                    }))
                    presenter.showForOutput(17, bounds, 60000)
                    return
                }
            }
            probe.fail("owned-output-missing")
        }
    }
}

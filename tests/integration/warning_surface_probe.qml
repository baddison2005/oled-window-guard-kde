import QtQuick
import org.kde.kwin
import "../code/transaction.js" as Transaction

QtObject {
    id: probe
    property var focusAtStart: null
    property var outputBounds: null
    property bool positionOwnedOnTarget: false
    property var ownedTarget: null
    property int ownedPlacementAttempts: 0
    property bool retryExpected: false
    property string lastStackState: ""
    property Timer stackMonitor: Timer {
        interval: 100
        repeat: true
        running: true
        onTriggered: {
            if (presenter.activeGeneration !== 17) return
            var windows = Workspace.stackingOrder || [], order = []
            for (var i = 0; i < windows.length; i++) {
                var window = windows[i]
                if (window && !window.deleted)
                    order.push(String(window.caption || "") + ":" + String(window.frameGeometry)
                        + ":managed=" + String(window.managed)
                        + ":class=" + String(window.resourceClass || "")
                        + ":pid=" + String(window.pid)
                        + ":above=" + String(window.keepAbove)
                        + ":taskbar=" + String(window.skipTaskbar)
                        + ":pager=" + String(window.skipPager)
                        + ":switcher=" + String(window.skipSwitcher))
            }
            var state = "visible=" + presenter.warning.visible +
                " count=" + presenter.surfaceCount() +
                " focus=" + String(Workspace.activeWindow ? Workspace.activeWindow.caption : "none") +
                " stack=" + order.join(" | ")
            if (state !== probe.lastStackState) {
                probe.lastStackState = state
                print("owg-warning-surface: STACK " + state)
            }
        }
    }
    function ownedFocusUnchanged() {
        return focusAtStart !== null && Workspace.activeWindow === focusAtStart &&
            String(focusAtStart.caption || "").indexOf("OWG owned focus probe") >= 0
    }
    property WarningSurface presenter: WarningSurface {
        onShown: function(generation) {
            var sameFocus = probe.ownedFocusUnchanged()
            var count = presenter.surfaceCount()
            print("owg-warning-surface: shown generation=" + generation +
                " count=" + count + " focusUnchanged=" + sameFocus)
            if (count !== 1 || !sameFocus) {
                print("owg-warning-surface: RESULT FAIL shown-invariant")
                return
            }
            if (generation === 17) {
                if (presenter.remainingSeconds !== 6 || presenter.heading !== "Moving a window") {
                    print("owg-warning-surface: RESULT FAIL initial-countdown"); return
                }
                countdownCheck.start()
            }
            else if (generation === 18) {
                if (!presenter.restoring || presenter.heading !== "Restoring a window" || presenter.remainingSeconds !== 6) {
                    print("owg-warning-surface: RESULT FAIL restore-countdown"); return
                }
                print("owg-warning-surface: restore countdown=6 heading=Restoring a window")
                sabotage.start()
                presenter.hideForGeneration(18)
            } else print("owg-warning-surface: RESULT FAIL unexpected-generation")
        }
        onSkipped: function(generation) {
            print("owg-warning-surface: skip callback generation=" + generation +
                " pointerSkipDelivered=unverified")
        }
        onExpired: function(generation) {
            print("owg-warning-surface: expired generation=" + generation)
        }
        onHidden: function(generation) {
            var count = presenter.surfaceCount()
            var sameFocus = probe.ownedFocusUnchanged()
            print("owg-warning-surface: hidden generation=" + generation +
                " count=" + count + " focusUnchanged=" + sameFocus)
            if (count !== 0 || !sameFocus) {
                print("owg-warning-surface: RESULT FAIL hide-invariant")
                return
            }
            if (generation === 17) Qt.callLater(function() { presenter.restoring=true; presenter.showForOutput(18, probe.outputBounds, 6000) })
            else if (generation === 18 && probe.retryExpected)
                print("owg-warning-surface: RESULT PASS")
            else print("owg-warning-surface: RESULT FAIL unexpected-hide")
        }
        onFailed: function(generation, reason) {
            if (generation === 18 && reason === "warning-hide-unconfirmed" && presenter.activeGeneration === 18) {
                sabotage.stop()
                probe.retryExpected = true
                print("owg-warning-surface: hide-timeout generation=18 retryable=true")
                presenter.hideForGeneration(18)
            } else print("owg-warning-surface: RESULT FAIL generation=" + generation + " reason=" + reason)
        }
    }
    property Timer countdownCheck: Timer {
        interval: 1250; repeat: false
        onTriggered: {
            if (presenter.remainingSeconds < 4 || presenter.remainingSeconds > 5) {
                print("owg-warning-surface: RESULT FAIL live-countdown"); return
            }
            var children=presenter.warning.mainItem.children, found=false
            for (var i=0;i<children.length;i++) if(children[i].objectName === "warningCountdown")
                found=children[i].text === presenter.remainingSeconds+" s"
            if (!found) {print("owg-warning-surface: RESULT FAIL countdown-text"); return}
            print("owg-warning-surface: live countdown="+presenter.remainingSeconds+" visibleText=true")
            presenter.onSkip()
        }
    }
    property Timer sabotage: Timer {
        interval: 10
        repeat: true
        onTriggered: if (presenter.activeGeneration === 18) presenter.warning.visible = true
    }
    property Timer ownedPlacement: Timer {
        interval: 100
        repeat: true
        onTriggered: {
            if (!probe.focusAtStart || probe.focusAtStart.deleted ||
                    Workspace.activeWindow !== probe.focusAtStart) {
                print("owg-warning-surface: RESULT FAIL owned-focus-lost-during-placement")
                stop()
                return
            }
            const actual = probe.focusAtStart.frameGeometry, target = probe.ownedTarget
            if (Math.abs(actual.x - target.x) <= 1 && Math.abs(actual.y - target.y) <= 1 &&
                    Math.abs(actual.width - target.width) <= 1 &&
                    Math.abs(actual.height - target.height) <= 1) {
                print("owg-warning-surface: owned-client-on-target frame=" + String(actual))
                stop()
                presenter.showForOutput(17, probe.outputBounds, 6000)
                return
            }
            if (++probe.ownedPlacementAttempts >= 20) {
                print("owg-warning-surface: RESULT FAIL owned-placement-unconfirmed frame=" + String(actual))
                stop()
                return
            }
            probe.focusAtStart.frameGeometry = target
        }
    }
    property Timer startProbe: Timer {
        interval: 800
        repeat: false
        onTriggered: {
            var screens = Workspace.screens || []
            if (!screens.length || !screens[0].geometry) {
                print("owg-warning-surface: RESULT FAIL virtual-output-unavailable")
                return
            }
            probe.focusAtStart = Workspace.activeWindow
            probe.outputBounds = screens[0].geometry
            print("owg-warning-surface: target-output geometry=" + String(probe.outputBounds)
                + " scale=" + String(screens[0].devicePixelRatio))
            if (!probe.focusAtStart || String(probe.focusAtStart.caption || "").indexOf("OWG owned focus probe") < 0) {
                print("owg-warning-surface: RESULT FAIL owned-focus-fixture-not-active")
                return
            }
            if (probe.positionOwnedOnTarget) {
                const bounds = probe.outputBounds
                probe.ownedTarget = {x: bounds.x + 20, y: bounds.y + 150,
                    width: bounds.width - 40, height: bounds.height - 170}
                probe.ownedPlacementAttempts = 0
                probe.focusAtStart.setMaximize(false, false)
                probe.focusAtStart.frameGeometry = probe.ownedTarget
                ownedPlacement.start()
                return
            }
            presenter.showForOutput(17, probe.outputBounds, 6000)
        }
    }
    Component.onCompleted: {
        var initial = Transaction.createTransactionState()
        if (initial.phase !== "Paused" || initial.generation !== 0) {
            print("owg-warning-surface: RESULT FAIL transaction-module-import")
            return
        }
        startProbe.start()
    }
}

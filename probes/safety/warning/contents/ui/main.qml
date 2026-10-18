import QtQuick
import org.kde.kwin
import org.kde.plasma.core as PlasmaCore

QtObject {
    id: probe

    readonly property string warningCaption: "OLED Window Guard safety probe"
    readonly property string ownedCaption: "OLED Window Guard disposable probe window"
    property var activeBeforeShow: null
    property bool completed: false
    property bool passed: false
    property bool warningSurfaceObserved: false
    property int shownManagedWarningWindows: 0
    property bool focusStable: false
    property bool ownedWindowPrecondition: false
    property bool singleOutputPrecondition: false

    function activeWindowIdentity() {
        const active = Workspace.activeWindow
        return active ? String(active.internalId || active.caption || "active-window") : "none"
    }

    function visibleProbeWindows() {
        const windows = Workspace.stackingOrder || []
        return windows.filter(window => window && !window.deleted
            && String(window.caption || "") === warningCaption
            && !window.hidden && !window.minimized).length
    }

    function isOwnedManagedWindow(window) {
        const windows = Workspace.stackingOrder || []
        return !!window && windows.indexOf(window) >= 0
            && String(window.caption || "") === ownedCaption
    }

    function reportFocus(stage) {
        const current = Workspace.activeWindow
        const unchangedOwned = !!activeBeforeShow && current === activeBeforeShow
            && isOwnedManagedWindow(activeBeforeShow) && isOwnedManagedWindow(current)
        focusStable = focusStable && unchangedOwned
        print("owg-warning-probe:", stage,
            "focusSameOwned=" + String(unchangedOwned),
            "focusStable=" + String(focusStable),
            "focusBefore=" + activeWindowIdentityFor(activeBeforeShow),
            "focusNow=" + activeWindowIdentity())
        return unchangedOwned
    }

    function activeWindowIdentityFor(window) {
        return window ? String(window.internalId || window.caption || "active-window") : "none"
    }

    function skipHandler(source) {
        if (!warning.visible) return
        print("owg-warning-probe:", "skip-handler", source)
        warning.visible = false
        Qt.callLater(function () {
            reportFocus("after-skip")
            verificationTimer.start()
        })
    }

    property PlasmaCore.Dialog warning: PlasmaCore.Dialog {
        id: warning
        title: probe.warningCaption
        location: PlasmaCore.Types.Floating
        backgroundHints: PlasmaCore.Types.NoBackground
        flags: Qt.BypassWindowManagerHint
            | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus
            | Qt.Tool
        hideOnWindowDeactivate: false
        visible: false
        x: 20
        y: 20
        width: 390
        height: 126

        mainItem: Rectangle {
            width: 390
            height: 126
            radius: 10
            color: "#f01d2935"
            border.width: 2
            border.color: "#3daee9"

            Text {
                x: 18
                y: 14
                width: parent.width - 36
                text: "OLED Window Guard will move windows soon."
                color: "white"
                font.pixelSize: 15
                wrapMode: Text.WordWrap
            }

            Rectangle {
                x: 246
                y: 70
                width: 124
                height: 40
                radius: 6
                color: "#3daee9"
                Text {
                    anchors.centerIn: parent
                    text: "Skip"
                    color: "#101820"
                    font.pixelSize: 14
                    font.bold: true
                }
                MouseArea {
                    anchors.fill: parent
                    onClicked: probe.skipHandler("mouse-area-click")
                }
            }
        }
    }

    property Timer showCheck: Timer {
        interval: 300
        repeat: false
        onTriggered: {
            const unchanged = probe.reportFocus("during-warning")
            const visibleCount = probe.visibleProbeWindows()
            probe.shownManagedWarningWindows = visibleCount
            probe.warningSurfaceObserved = warning.visible && visibleCount === 1
            print("owg-warning-probe:", "warning-surface",
                "windowVisible=" + String(warning.visible),
                "visibleWarningWindows=" + visibleCount)
            if (!unchanged) {
                print("owg-warning-probe:", "FAIL warning changed active window")
            }
            if (!warning.visible) {
                print("owg-warning-probe:", "FAIL warning surface did not become visible")
            }
            // This calls the exact same teardown callback as the Skip MouseArea.
            // It verifies lifecycle and focus behavior without pretending to
            // synthesize a real Wayland pointer event.
            probe.skipHandler("harness-callback-not-pointer-input")
        }
    }

    property Timer verificationTimer: Timer {
        interval: 300
        repeat: false
        onTriggered: {
            const focusSame = probe.reportFocus("after-surface-teardown")
            const visibleCount = probe.visibleProbeWindows()
            const teardown = !warning.visible && visibleCount === 0
            probe.passed = probe.warningSurfaceObserved && focusSame && teardown
                && probe.focusStable && probe.ownedWindowPrecondition
                && probe.singleOutputPrecondition
            probe.completed = true
            print("owg-warning-probe:", "RESULT",
                probe.passed ? "PASS" : "FAIL",
                "focusStable=" + String(probe.focusStable),
                "ownedWindowPrecondition=" + String(probe.ownedWindowPrecondition),
                "singleOutputPrecondition=" + String(probe.singleOutputPrecondition),
                "warningSurfaceObserved=" + String(probe.warningSurfaceObserved),
                "shownManagedWarningWindows=" + probe.shownManagedWarningWindows,
                "dialogVisible=" + String(warning.visible),
                "visibleWarningWindows=" + visibleCount,
                "pointerSkipDelivered=unverified")
        }
    }

    Component.onCompleted: {
        activeBeforeShow = Workspace.activeWindow
        ownedWindowPrecondition = isOwnedManagedWindow(activeBeforeShow)
        const screens = Workspace.screens || []
        singleOutputPrecondition = screens.length === 1
        focusStable = ownedWindowPrecondition
        print("owg-warning-probe:", "show",
            "activeOwnedManaged=" + String(ownedWindowPrecondition),
            "outputCount=" + screens.length,
            "focusBefore=" + activeWindowIdentityFor(activeBeforeShow),
            "caption=" + warningCaption,
            "flags=BypassWindowManager|Frameless|WindowDoesNotAcceptFocus|Tool")
        warning.visible = true
        showCheck.start()
    }

    Component.onDestruction: {
        warning.visible = false
        showCheck.stop()
        verificationTimer.stop()
        print("owg-warning-probe:", "script-destroyed")
    }
}

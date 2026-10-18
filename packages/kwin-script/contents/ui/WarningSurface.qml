import QtQuick
import org.kde.kwin
import org.kde.plasma.core as PlasmaCore
import "core/warning_clock.js" as Clock

// Presentation adapter; writers require confirmed warning removal before assignment.
QtObject {
    id: presenter
    signal shown(int generation)
    signal hidden(int generation)
    signal skipped(int generation)
    signal expired(int generation)
    signal failed(int generation, string reason)

    readonly property string ownCaption: "OLED Window Guard warning"
    property int activeGeneration: -1
    property var focusBefore: null
    property double expiresAt: 0
    property int warningDuration: 0
    property int windowCount: 1
    property var inputPolicy: null
    readonly property int graceRemainingSeconds: countdownNow>0 && inputPolicy ? Math.max(0,Math.ceil(Math.min(inputPolicy.graceDurationMs || warningDuration,inputPolicy.graceUntilMs-countdownNow)/1000)) : 0
    property bool restoring: false
    property double countdownNow: 0
    property var clockState: null
    property bool placementConfirmed: false
    readonly property double remainingMs: activeGeneration>=0 ? (clockState?clockState.remainingMs:warningDuration) : 0
    readonly property int remainingSeconds: Math.ceil(remainingMs/1000)
    function clockNow() {return inputPolicy ? inputPolicy.monotonicNow() : Date.now();}
    function sampleClock() {
        const fresh=Clock.sample(clockState,clockNow());
        if (!fresh) throw new Error("warning-clock-unavailable");
        const changed=!clockState || Math.ceil(fresh.remainingMs/1000)!==Math.ceil(clockState.remainingMs/1000);
        clockState=fresh;countdownNow=fresh.lastNow;
        if (changed) warning.update();
    }
    readonly property string heading: (restoring ? "Restoring " : "Moving ") + (windowCount===0 ? "windows" : windowCount===1 ? "a window" : windowCount+" windows")
    property int hideChecks: 0
    property bool pendingHide: false
    property var targetGeometry: null
    property int placementChecks: 0

    function isOwnSurface(window) {
        // KWin's in-process Plasma dialog has no client PID or resource class.
        // Never assign frameGeometry to a client that merely copied our title.
        return window && !window.deleted && String(window.caption || "") === ownCaption
            && Number(window.pid) === -1 && String(window.resourceClass || "") === ""
    }
    function ownWindow() {
        var windows = Workspace.stackingOrder || []
        for (var i = 0; i < windows.length; i++) {
            var window = windows[i]
            if (isOwnSurface(window))
                return window
        }
        return null
    }

    function surfaceCount() {
        var windows = Workspace.stackingOrder || [], count = 0
        for (var i = 0; i < windows.length; i++) {
            var window = windows[i]
            if (isOwnSurface(window) && !window.hidden && !window.minimized) count++
        }
        return count
    }
    function showForOutput(generation, bounds, durationMs) {
        if (activeGeneration >= 0 || !bounds || !isFinite(bounds.x) || !isFinite(bounds.y)
                || !isFinite(bounds.width) || !isFinite(bounds.height) || bounds.width < 264
                || bounds.height < 150 || !isFinite(durationMs) || durationMs < 3000 || durationMs > 60000) {
            failed(generation, "invalid-or-busy-warning")
            return
        }
        warningDuration = durationMs
        clockState=null;placementConfirmed=false;countdownNow=0
        activeGeneration = generation
        expiresAt = Date.now()+durationMs
        focusBefore = Workspace.activeWindow
        targetGeometry = {
            x: Math.round(bounds.x + (bounds.width - Math.min(390, bounds.width - 24)) / 2),
            y: Math.round(bounds.y + 12),
            width: Math.min(390, bounds.width - 24), height: 126
        }
        placementChecks = 0
        warning.width = targetGeometry.width
        warning.height = targetGeometry.height
        warning.x = targetGeometry.x
        warning.y = targetGeometry.y
        warning.visible = true
        showCheck.start()
    }
    function hideForGeneration(generation) {
        if (generation !== activeGeneration || activeGeneration < 0) return
        expiry.stop()
        showCheck.stop()
        pendingHide = true
        hideChecks = 0
        warning.visible = false
        hideCheck.start()
    }
    function onSkip() {
        if (activeGeneration < 0 || !warning.visible) return
        var generation = activeGeneration
        skipped(generation)
        hideForGeneration(generation)
    }

    property PlasmaCore.Dialog warning: PlasmaCore.Dialog {
        id: warning
        title: presenter.ownCaption
        location: PlasmaCore.Types.Floating
        backgroundHints: PlasmaCore.Types.NoBackground
        // Qt.Tool was dismissed by KWin on an outside click and swallowed that
        // click. A normal top-level role keeps only this 390x126 input surface.
        // KWin initially centers it, so showCheck positions our own window.
        flags: Qt.Window | Qt.FramelessWindowHint
            | Qt.WindowDoesNotAcceptFocus | Qt.WindowStaysOnTopHint
        hideOnWindowDeactivate: false
        visible: false
        onVisibleChanged: {
            if (visible || presenter.activeGeneration < 0 || presenter.pendingHide) return
            var generation = presenter.activeGeneration
            presenter.expiry.stop()
            presenter.showCheck.stop()
            presenter.activeGeneration = -1
            presenter.focusBefore = null
            presenter.failed(generation, "warning-unexpectedly-hidden")
        }
        width: 390
        height: 126
        mainItem: Rectangle {
            // Composite the entire card when labels change, avoiding stale glyph damage.
            layer.enabled: true
            width: warning.width
            height: 126
            radius: 10
            color: "#f01d2935"
            border.width: 2
            border.color: "#3daee9"
            Text {
                x: 18
                y: 14
                width: parent.width - 36
                text: presenter.heading
                font.bold: true
                color: "white"
                font.pixelSize: 15
                wrapMode: Text.WordWrap
            }
            Text {
                x: 18; y: 42; width: parent.width - 36
                text: presenter.graceRemainingSeconds>0 ? "Grace period: finish interacting within " + presenter.graceRemainingSeconds + " s." : "Click, scroll, drag or type on this display to cancel."
                color: "#dce7ef"; font.pixelSize: 12; wrapMode: Text.WordWrap
            }
            Text {
                objectName: "warningCountdown"
                renderType: Text.NativeRendering
                textFormat: Text.PlainText
                height: 32
                x: 18; y: 77; width: parent.width - 178
                text: presenter.remainingSeconds + " s"
                color: "white"; font.pixelSize: 23; font.bold: true
                Accessible.name: presenter.heading + " in " + presenter.remainingSeconds + " seconds"
            }
            Rectangle {
                x: parent.width - 142
                y: 70
                width: 124
                height: 40
                radius: 6
                color: "#3daee9"
                Text { anchors.centerIn: parent; text: "Skip"; color: "#101820"; font.pixelSize: 14; font.bold: true }
                MouseArea { anchors.fill: parent; onClicked: presenter.onSkip() }
            }
        }
    }
    property Timer showCheck: Timer {
        interval: 100
        repeat: true
        onTriggered: {
            if (presenter.activeGeneration < 0) return
            // Another app may legitimately gain focus on another display.
            // The surface must never take focus itself; target focus is guarded
            // independently by native tokens and each geometry assignment.
            if (!warning.visible || presenter.isOwnSurface(Workspace.activeWindow)) {
                var generation = presenter.activeGeneration
                presenter.hideForGeneration(generation)
                presenter.failed(generation, "warning-not-visible-or-owned-focus")
                return
            }
            var own = presenter.ownWindow()
            if (!own || presenter.surfaceCount() !== 1) {
                if (++presenter.placementChecks < 20) return
                var missingGeneration = presenter.activeGeneration
                presenter.hideForGeneration(missingGeneration)
                presenter.failed(missingGeneration, "warning-surface-missing-or-duplicate")
                return
            }
            own.keepAbove = true
            own.skipTaskbar = true
            own.skipPager = true
            own.skipSwitcher = true
            var desired = presenter.targetGeometry, actual = own.frameGeometry
            if (!actual || Math.abs(actual.x - desired.x) > 1 || Math.abs(actual.y - desired.y) > 1
                    || Math.abs(actual.width - desired.width) > 1 || Math.abs(actual.height - desired.height) > 1) {
                own.frameGeometry = desired
                if (++presenter.placementChecks < 20) return
                var placementGeneration = presenter.activeGeneration
                presenter.hideForGeneration(placementGeneration)
                presenter.failed(placementGeneration, "warning-placement-unconfirmed")
                return
            }
            stop()
            // Use the same confirmed start for the visible countdown and deadline.
            presenter.clockState=Clock.start(presenter.clockNow(),presenter.warningDuration)
            if (!presenter.clockState) {presenter.failed(presenter.activeGeneration,"warning-clock-unavailable");presenter.hideForGeneration(presenter.activeGeneration);return}
            presenter.countdownNow=presenter.clockState.lastNow
            presenter.placementConfirmed=true
            presenter.expiresAt = Date.now() + presenter.warningDuration
            expiry.interval = presenter.warningDuration
            expiry.start()
            presenter.shown(presenter.activeGeneration)
        }
    }
    property Timer countdown: Timer {
        interval: 100; repeat: true
        running: warning.visible && presenter.activeGeneration >= 0 && presenter.placementConfirmed
        onTriggered: {
            try {presenter.sampleClock()}
            catch(error) {presenter.failed(presenter.activeGeneration,"warning-clock-unavailable");presenter.hideForGeneration(presenter.activeGeneration)}
        }
    }
    property Timer expiry: Timer {
        repeat: false
        onTriggered: {
            if (presenter.activeGeneration < 0) return
            try {presenter.sampleClock()}
            catch(error) {presenter.failed(presenter.activeGeneration,"warning-clock-unavailable");presenter.hideForGeneration(presenter.activeGeneration);return}
            const remaining=presenter.remainingMs
            if (remaining>0) {interval=Math.max(1,Math.ceil(remaining));restart();return}
            presenter.expired(presenter.activeGeneration)
        }
    }
    property Timer hideCheck: Timer {
        interval: 50
        repeat: true
        onTriggered: {
            if (!presenter.pendingHide) { stop(); return }
            presenter.hideChecks++
            if (!warning.visible && presenter.surfaceCount() === 0) {
                var generation = presenter.activeGeneration
                presenter.pendingHide = false
                presenter.activeGeneration = -1
                presenter.focusBefore = null
                stop()
                presenter.hidden(generation)
            } else if (presenter.hideChecks >= 20) {
                var failedGeneration = presenter.activeGeneration
                presenter.pendingHide = false
                stop()
                // Keep ownership while the surface is unconfirmed. A caller can
                // retry hideForGeneration or unload the script; never orphan it.
                presenter.failed(failedGeneration, "warning-hide-unconfirmed")
            }
        }
    }
}

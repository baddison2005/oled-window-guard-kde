import QtQuick
import org.kde.kwin
import "../code/runtimeCore.js" as Core

QtObject {
    id: probe

    readonly property string epoch: String(Date.now())
    readonly property int timerIntervalMs: 250
    readonly property int exchangePeriodMs: 2000
    readonly property int exchangeTimeoutMs: 750
    readonly property int expiredCallRetentionMs: 5000
    readonly property int maxExpiredCalls: 3
    property int requestId: 0
    property int pendingRequestId: 0
    property int pendingElapsedMs: 0
    property int exchangeElapsedMs: 2000
    property bool exchangePending: false
    property var pendingCall: null
    property var expiredCalls: ({})
    property var watchedWindows: []
    property var windowConnections: []
    property var geometryTestWindow: null
    property var geometryOriginal: null
    property var geometryTarget: null
    property string geometryTestState: "idle"
    property string geometryTestIdentity: ""
    property string focusAtGeometryStart: ""
    property string desktopAtGeometryStart: ""
    property int geometryTestStartedAt: 0
    property bool geometryCommandHandled: false
    property int reconcileTicks: 0

    function log(message) { print("oled-window-guard-probe:", message) }

    function workAreaFor(window) {
        if (!window || !window.output) return null
        try {
            const desktop = Workspace.currentDesktopForScreen(window.output) || Workspace.currentDesktop
            return Workspace.clientArea(KWin.MaximizeArea, window.output, desktop)
        } catch (error) {
            log("workarea unavailable: " + String(error))
            return null
        }
    }

    function reportWindow(window, eventName) {
        if (!window || window.deleted) return
        const snapshot = Core.windowSnapshot(window, workAreaFor(window))
        log("window " + eventName + " " + JSON.stringify(snapshot))
    }

    function rectToken(rect) {
        return rect ? [rect.x, rect.y, rect.width, rect.height].join(",") : "unknown"
    }

    function sameRect(left, right, tolerance) {
        return left && right
            && Math.abs(left.x - right.x) <= tolerance
            && Math.abs(left.y - right.y) <= tolerance
            && Math.abs(left.width - right.width) <= tolerance
            && Math.abs(left.height - right.height) <= tolerance
    }

    function activeIdentity() {
        const active = Workspace.activeWindow
        if (!active || active.deleted) return "none"
        try { return String(active.internalId) } catch (error) { return "unknown" }
    }

    function desktopIdentity() {
        try { return String(Workspace.currentDesktop.id) } catch (error) { return "unknown" }
    }

    function beginOwnedGeometryProbe() {
        const candidates = (Workspace.stackingOrder || []).filter(function (window) {
            if (!window || window.deleted || !window.moveable || !window.resizeable) return false
            const snapshot = Core.windowSnapshot(window, workAreaFor(window))
            const rect = snapshot.geometry
            return snapshot.appId === "oled-window-guard-runtime-test"
                && rect && rect.width >= 100 && rect.height >= 100
        })
        if (candidates.length !== 1) {
            log("owned geometry probe refused candidates=" + candidates.length)
            return
        }
        const window = candidates[0]
        const snapshot = Core.windowSnapshot(window, workAreaFor(window))
        const original = snapshot.geometry
        const bounds = snapshot.workArea
        const targetX = original.x + 12 <= bounds.x + bounds.width - original.width
            ? original.x + 12 : original.x - 12
        const targetY = original.y + 8 <= bounds.y + bounds.height - original.height
            ? original.y + 8 : original.y - 8
        if (targetX < bounds.x || targetY < bounds.y
                || targetX + original.width > bounds.x + bounds.width
                || targetY + original.height > bounds.y + bounds.height) {
            log("owned geometry probe refused: no contained position-only target")
            return
        }
        geometryTestWindow = window
        geometryTestIdentity = snapshot.identity
        geometryOriginal = original
        geometryTarget = { x: targetX, y: targetY, width: original.width, height: original.height }
        focusAtGeometryStart = activeIdentity()
        desktopAtGeometryStart = desktopIdentity()
        geometryTestStartedAt = Date.now()
        geometryTestState = "await-target"
        log("owned move requested identity=" + geometryTestIdentity
            + " from=" + rectToken(geometryOriginal) + " to=" + rectToken(geometryTarget))
        window.frameGeometry = geometryTarget
    }

    function advanceOwnedGeometryProbe(source) {
        if (geometryTestState !== "await-target" && geometryTestState !== "await-restore"
                && geometryTestState !== "await-resize" && geometryTestState !== "await-resize-restore") return
        if (!geometryTestWindow || geometryTestWindow.deleted
                || String(geometryTestWindow.internalId) !== geometryTestIdentity) {
            geometryTestState = "failed"
            log("owned geometry probe failed: identity disappeared")
            return
        }
        const snapshot = Core.windowSnapshot(geometryTestWindow, workAreaFor(geometryTestWindow))
        const actual = snapshot.geometry
        if (geometryTestState === "await-target" && sameRect(actual, geometryTarget, 0.5)) {
            geometryTestState = "await-restore"
            geometryTestStartedAt = Date.now()
            log("owned move readback accepted source=" + source + " frame=" + rectToken(actual))
            // Restore only while this exact test window still has the geometry
            // written above. Never overwrite an intervening change.
            geometryTestWindow.frameGeometry = geometryOriginal
            return
        }
        if (geometryTestState === "await-restore" && sameRect(actual, geometryOriginal, 0.5)) {
            geometryTarget = {
                x: geometryOriginal.x,
                y: geometryOriginal.y,
                width: geometryOriginal.width + 16,
                height: geometryOriginal.height + 12,
            }
            if (!snapshot.workArea
                    || geometryTarget.x + geometryTarget.width > snapshot.workArea.x + snapshot.workArea.width
                    || geometryTarget.y + geometryTarget.height > snapshot.workArea.y + snapshot.workArea.height) {
                geometryTestState = "failed"
                log("owned resize probe refused: no contained size-only target")
                return
            }
            geometryTestState = "await-resize"
            geometryTestStartedAt = Date.now()
            log("owned resize requested identity=" + geometryTestIdentity
                + " from=" + rectToken(geometryOriginal) + " to=" + rectToken(geometryTarget))
            geometryTestWindow.frameGeometry = geometryTarget
            return
        }
        if (geometryTestState === "await-resize" && sameRect(actual, geometryTarget, 0.5)) {
            geometryTestState = "await-resize-restore"
            geometryTestStartedAt = Date.now()
            log("owned resize readback accepted source=" + source + " frame=" + rectToken(actual))
            log("owned resize restore requested identity=" + geometryTestIdentity
                + " to=" + rectToken(geometryOriginal))
            geometryTestWindow.frameGeometry = geometryOriginal
            return
        }
        if (geometryTestState === "await-resize-restore" && sameRect(actual, geometryOriginal, 0.5)) {
            const focusOk = activeIdentity() === focusAtGeometryStart
            const desktopOk = desktopIdentity() === desktopAtGeometryStart
            geometryTestState = focusOk && desktopOk ? "passed" : "failed-invariant"
            log("owned restore readback " + (geometryTestState === "passed" ? "accepted" : "failed")
                + " source=" + source + " frame=" + rectToken(actual)
                + " focusInvariant=" + (focusOk ? "passed" : "failed")
                + " desktopInvariant=" + (desktopOk ? "passed" : "failed"))
            return
        }
        if (Date.now() - geometryTestStartedAt >= 2500) {
            const failedPhase = geometryTestState.indexOf("resize") >= 0 ? "resize" : "move"
            geometryTestState = "failed"
            log("owned geometry probe failed state=" + geometryTestState
                + " phase=" + failedPhase
                + " actual=" + rectToken(actual))
        }
    }

    function watchWindow(window) {
        if (!window || watchedWindows.indexOf(window) >= 0) return
        watchedWindows.push(window)
        const connection = windowConnectionComponent.createObject(probe, { observedWindow: window })
        windowConnections.push(connection)
        reportWindow(window, "observed")
    }

    function reconcile() {
        const windows = Workspace.stackingOrder || []
        for (let index = 0; index < windows.length; index += 1) {
            watchWindow(windows[index])
            reportWindow(windows[index], "timer-reconcile")
        }
    }

    function beginExchange() {
        requestId += 1
        pendingRequestId = requestId
        pendingElapsedMs = 0
        exchangeElapsedMs = 0
        exchangePending = true
        pendingCall = exchangeComponent.createObject(probe, { requestId: pendingRequestId })
        if (!pendingCall) {
            exchangePending = false
            log("exchange failed: DBusCall creation failed")
            return
        }
        pendingCall.arguments = [epoch, pendingRequestId, 0]
        pendingCall.call()
        log("exchange sent request=" + pendingRequestId)
    }

    function handleExchange(callbackRequestId, reply, failed) {
        if (!exchangePending || callbackRequestId !== requestId) {
            log("exchange stale reply request=" + callbackRequestId)
            const staleEntry = expiredCalls[String(callbackRequestId)]
            if (staleEntry) {
                delete expiredCalls[String(callbackRequestId)]
                staleEntry.call.destroy()
            }
            return
        }
        exchangePending = false
        const finishedCall = pendingCall
        pendingCall = null
        if (finishedCall) finishedCall.destroy()
        const result = Core.boundedExchangeResult(epoch, callbackRequestId, epoch, requestId,
            failed ? null : { ok: true, payload: reply })
        log("exchange result " + JSON.stringify(result))
        if (!geometryCommandHandled && (reply === "move-owned-test"
                || (reply && reply[0] === "move-owned-test"))) {
            geometryCommandHandled = true
            beginOwnedGeometryProbe()
        }
    }

    function onTimer() {
        const expiredIds = Object.keys(expiredCalls)
        for (let index = 0; index < expiredIds.length; index += 1) {
            const id = expiredIds[index]
            const entry = expiredCalls[id]
            if (Date.now() - entry.expiredAt >= expiredCallRetentionMs) {
                delete expiredCalls[id]
                entry.call.destroy()
                log("expired exchange released request=" + id)
            }
        }
        reconcileTicks += 1
        if (reconcileTicks % 8 === 0) reconcile()
        advanceOwnedGeometryProbe("timer")
        if (exchangePending) pendingElapsedMs += timerIntervalMs
        else exchangeElapsedMs += timerIntervalMs
        if (exchangePending && pendingElapsedMs >= exchangeTimeoutMs) {
            const timedOutId = pendingRequestId
            exchangePending = false
            const expiredCall = pendingCall
            pendingCall = null
            if (expiredCall) {
                expiredCalls[String(timedOutId)] = { call: expiredCall, expiredAt: Date.now() }
                const retainedIds = Object.keys(expiredCalls)
                if (retainedIds.length > maxExpiredCalls) {
                    const oldestId = retainedIds[0]
                    const oldest = expiredCalls[oldestId]
                    delete expiredCalls[oldestId]
                    oldest.call.destroy()
                    log("expired exchange cap released request=" + oldestId)
                }
            }
            log("exchange timeout request=" + timedOutId)
        }
        if (!exchangePending && exchangeElapsedMs >= exchangePeriodMs) beginExchange()
    }

    function registerShortcutProbe() {
        try {
            if (typeof KWin.registerShortcut === "function") {
                const registered = KWin.registerShortcut(
                    "OLED Window Guard probe ping", "Passive capability probe", "",
                    function () { probe.log("shortcut invoked (passive)") }
                )
                log("shortcut registration=" + registered)
            } else {
                log("shortcut registration unavailable on declarative KWin wrapper")
            }
        } catch (error) {
            log("shortcut registration failed: " + String(error))
        }
    }

    property Timer reconcileTimer: Timer {
        interval: probe.timerIntervalMs
        repeat: true
        running: true
        onTriggered: probe.onTimer()
    }

    property Connections workspaceSignals: Connections {
        target: Workspace
        ignoreUnknownSignals: true
        function onWindowAdded(window) { probe.watchWindow(window) }
        function onWindowRemoved(window) {
            const index = probe.watchedWindows.indexOf(window)
            if (index >= 0) {
                probe.watchedWindows.splice(index, 1)
                const connection = probe.windowConnections.splice(index, 1)[0]
                if (connection) connection.destroy()
            }
            probe.log("window closed")
        }
        function onWindowActivated(window) { probe.reportWindow(window, "activated-observed") }
        function onScreensChanged() { probe.log("outputs changed") }
    }

    property Component windowConnectionComponent: Component {
        Connections {
            required property var observedWindow
            target: observedWindow
            ignoreUnknownSignals: true
            function onFrameGeometryChanged() {
                probe.reportWindow(observedWindow, "ack:frameGeometryChanged")
                if (String(observedWindow.internalId) === probe.geometryTestIdentity)
                    probe.advanceOwnedGeometryProbe("frameGeometryChanged")
            }
            function onOutputChanged() { probe.reportWindow(observedWindow, "ack:outputChanged") }
            function onMinimizedChanged() { probe.reportWindow(observedWindow, "state:minimized") }
            function onMaximizedChanged() { probe.reportWindow(observedWindow, "state:maximized") }
            function onFullScreenChanged() { probe.reportWindow(observedWindow, "state:fullScreen") }
            function onMoveableChanged() { probe.reportWindow(observedWindow, "state:moveable") }
            function onResizeableChanged() { probe.reportWindow(observedWindow, "state:resizeable") }
            function onDesktopsChanged() { probe.reportWindow(observedWindow, "state:desktops") }
        }
    }

    property Component exchangeComponent: Component {
        DBusCall {
            required property int requestId
            service: "org.oledwindowguard.ProbeCompanion"
            path: "/org/oledwindowguard/ProbeCompanion"
            dbusInterface: "org.oledwindowguard.ProbeCompanion"
            method: "Exchange"
            onFinished: function (returnValue) { probe.handleExchange(requestId, returnValue, false) }
            onFailed: probe.handleExchange(requestId, [], true)
        }
    }

    Component.onCompleted: {
        const windows = Workspace.stackingOrder || []
        for (let index = 0; index < windows.length; index += 1) watchWindow(windows[index])
        registerShortcutProbe()
        log("started epoch=" + epoch + " timerCount=1 windows=" + watchedWindows.length)
    }

}

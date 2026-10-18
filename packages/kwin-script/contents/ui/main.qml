import QtQuick
import org.kde.kwin
import "core/model.js" as Model

QtObject {
    id: adapter
    // Hidden until the guarded controller explicitly requests a warning.
    property WarningSurface warningSurface: WarningSurface {inputPolicy:adapter.planning.nativeObservation}
    readonly property string sessionEpoch: String(Date.now()) + ":" + String(Math.random())
    signal snapshotCaptured(var snapshot, string reason)
    property PlanningController planning: PlanningController { observer: adapter }
    property RuntimeController runtime: RuntimeController {observer: adapter;planning: adapter.planning}
    property DimmingController dimming: DimmingController {observer:adapter}
    property GuardedWriter writer: GuardedWriter { observer: adapter; planning: adapter.planning; warning: adapter.warningSurface;enabled: adapter.runtime.movementEnabled }
    property OrderedGuardedWriter orderedWriter: OrderedGuardedWriter {
        observer: adapter; planning: adapter.planning; warning: adapter.warningSurface; singleWriter: adapter.writer;enabled: adapter.runtime.movementEnabled
    }
    property int libraryRequestSerial: 0
    // Explicit diagnostic opt-in; routine journals must not contain desktop snapshots.
    readonly property bool diagnosticSnapshots: KWin.readConfig("DiagnosticSnapshots", false) === true
    property int sequence: 0
    property int topologyGeneration: 0
    property string topologyFingerprint: ""
    property var watchedWindows: []
    property var windowConnections: []
    property int anonymousIdentityCounter: 0
    property var anonymousWindows: []
    property var anonymousIdentities: []
    readonly property int reconcileIntervalMs: 1000
    property bool shuttingDown: false

    function prepareShutdown() {
        if (shuttingDown) return
        shuttingDown = true
        // Hiding the in-process warning synchronously emits windowRemoved.
        // During QML destruction its context is already being dismantled:
        // disconnect before any child cancellation can hide that dialog.
        workspaceSignals.target = null
        reconcileTimer.stop()
        for (var i = 0; i < windowConnections.length; ++i) {
            if (windowConnections[i]) windowConnections[i].target = null
        }
        dimming.stop()
    }

    function read(object, name, fallback) {
        try {
            var value = object[name]
            return value === undefined ? fallback : value
        } catch (error) { return fallback }
    }
    function rect(value) {
        if (!value) return null
        var result = { x: Number(value.x), y: Number(value.y),
                       width: Number(value.width), height: Number(value.height) }
        if (!isFinite(result.x) || !isFinite(result.y) || !isFinite(result.width)
                || !isFinite(result.height) || result.width <= 0 || result.height <= 0) return null
        return Model.normalizeReadbackRectangle(result)
    }
    function identity(window) {
        var id = read(window, "internalId", null)
        if (id !== null && id !== "") return sessionEpoch + ":" + String(id)
        var index = anonymousWindows.indexOf(window)
        if (index >= 0) return anonymousIdentities[index]
        anonymousWindows.push(window)
        anonymousIdentityCounter += 1
        var fallback = sessionEpoch + ":anonymous:" + anonymousIdentityCounter
        anonymousIdentities.push(fallback)
        return fallback
    }
    function nullableBoolean(object, name) {
        var value = read(object, name, null)
        return typeof value === "boolean" ? value : null
    }
    function membership(object, propertyName, current, allProperty) {
        var all = nullableBoolean(object, allProperty)
        if (all === true) return true
        var values = read(object, propertyName, null)
        if (!values) return null
        if (values.length === 0 && propertyName === "activities") return true
        if (current === null || current === undefined) return null
        for (var i = 0; i < values.length; ++i) {
            var value = values[i]
            if (String(read(value, "id", value)) === String(read(current, "id", current))) return true
        }
        return values.length > 0 || all === false ? false : null
    }
    function desktopMembership(window) {
        if (nullableBoolean(window,"onAllDesktops")===true) return true
        // currentDesktop refers to the active screen. Other screens can show
        // different desktops, so evaluate this window on its own output.
        const output=read(window,"output",null)
        if (!output) return null
        let current=null
        try {current=Workspace.currentDesktopForScreen(output)} catch(error) {return null}
        return membership(window,"desktops",current,"onAllDesktops")
    }
    function outputsNow() {
        var screens = read(Workspace, "screens", []) || []
        var outputs = [], fingerprint = []
        for (var i = 0; i < screens.length; ++i) {
            var screen = screens[i]
            var name = String(read(screen, "name", ""))
            var geometry = rect(read(screen, "geometry", null))
            if (!name || !geometry) continue
            var usable = null
            try {
                var d = Workspace.currentDesktopForScreen(screen) || Workspace.currentDesktop
                usable = rect(Workspace.clientArea(KWin.MaximizeArea, screen, d))
            } catch (error) {}
            var rawScale = read(screen, "devicePixelRatio", null)
            var scale = typeof rawScale === "number" && isFinite(rawScale) ? rawScale : null
            var manufacturer = String(read(screen, "manufacturer", ""))
            var model = String(read(screen, "model", ""))
            var serial = String(read(screen, "serialNumber", ""))
            var id = JSON.stringify([name, manufacturer, model, serial])
            outputs.push({ id: id, connector: name, manufacturer: manufacturer,
                           model: model, serial: serial, geometry: geometry,
                           usableRect: usable, scale: scale })
            fingerprint.push(id + ":" + JSON.stringify(geometry) + ":" + JSON.stringify(usable) + ":" + scale)
        }
        fingerprint.sort()
        var key = fingerprint.join(";")
        if (key !== topologyFingerprint) {
            topologyFingerprint = key
            topologyGeneration += 1
        }
        return outputs
    }
    function windowRecord(window, outputs, focused) {
        var id = identity(window)
        var frame = rect(read(window, "frameGeometry", null))
        var minimumSize=null
        try {
            var minimum=planning.nativeObservation.localObserver.frameSizeBounds(String(window.internalId)).minimum
            if (minimum && typeof minimum.width==="number" && typeof minimum.height==="number" &&
                    isFinite(minimum.width) && isFinite(minimum.height) && minimum.width>=0 && minimum.height>=0)
                minimumSize={width:minimum.width,height:minimum.height}
        } catch(error) { /* Unavailable resize facts permit no fitting resize. */ }
        var outputIds = []
        if (frame) for (var i = 0; i < outputs.length; ++i) {
            var outputRect = outputs[i].geometry
            var overlapWidth = Math.min(frame.x + frame.width, outputRect.x + outputRect.width)
                - Math.max(frame.x, outputRect.x)
            var overlapHeight = Math.min(frame.y + frame.height, outputRect.y + outputRect.height)
                - Math.max(frame.y, outputRect.y)
            if (overlapWidth > 0 && overlapHeight > 0) outputIds.push(outputs[i].id)
        }
        var skip = []
        if (!frame) skip.push("geometry-unavailable")
        var integerPosition=null
        try {integerPosition=planning.nativeObservation.localObserver.placementUsesIntegerPosition(String(window.internalId))}
        catch(error) { /* An unavailable native classifier grants no move. */ }
        if (typeof integerPosition!=="boolean") {integerPosition=null;skip.push("placement-classification-unavailable")}
        var managed = nullableBoolean(window, "managed")
        var modal = nullableBoolean(window, "modal")
        var transient = nullableBoolean(window, "transient")
        var appId = read(window, "desktopFileName", null) || read(window, "resourceClass", null)
        if (typeof appId !== "string") appId = null
        var currentActivity = read(Workspace, "currentActivity", null)
        var windowType = "unknown"
        var typeProperties = [["notification", "notification"], ["criticalNotification", "notification"], ["onScreenDisplay", "osd"],
            ["dialog", "dialog"], ["dock", "dock"], ["desktopWindow", "desktop"],
            ["specialWindow", "special"], ["normalWindow", "normal"]]
        for (var typeIndex = 0; typeIndex < typeProperties.length; ++typeIndex) {
            if (nullableBoolean(window, typeProperties[typeIndex][0]) === true) {
                windowType = typeProperties[typeIndex][1]
                break
            }
        }
        var maximizeMode = read(window, "maximizeMode", null)
        var maximized = typeof maximizeMode === "number" ? maximizeMode !== 0
            : nullableBoolean(window, "maximized")
        var tile = read(window, "tile", undefined)
        var tiled = null
        if (tile !== undefined) tiled = tile !== null
        return {
            identity: id,
            appId: appId,
            integerPosition: integerPosition,
            minimumSize: minimumSize,
            rect: frame,
            outputIds: outputIds,
            visible: nullableBoolean(window, "hidden") === null
                ? nullableBoolean(window, "visible") : !nullableBoolean(window, "hidden"),
            onCurrentDesktop: desktopMembership(window),
            onCurrentActivity: membership(window, "activities", currentActivity, "onAllActivities"),
            focused: focused,
            managed: managed,
            modal: modal,
            transient: transient,
            movable: nullableBoolean(window, "moveable"),
            resizable: nullableBoolean(window, "resizeable"),
            minimized: nullableBoolean(window, "minimized"),
            maximized: maximized,
            fullscreen: nullableBoolean(window, "fullScreen"),
            tiled: tiled,
            windowType: Model.applicationWindowType(windowType,appId,nullableBoolean(window,"moveable"),nullableBoolean(window,"resizeable")),
            skipReasons: skip
        }
    }
    function isPassiveVisual(window) {
        if (read(window,"outline",false)===true) return true;
        if (Number(read(window,"pid",0))!==-1 || String(read(window,"resourceClass",""))!=="") return false;
        try {return planning.nativeObservation.immediateCancellation && planning.nativeObservation.localObserver.isPassiveOverlay(String(window.internalId));}
        catch(error) {return false;}
    }
    function capture() {
        var outputs = outputsNow(), windows = []
        var active = read(Workspace, "activeWindow", null)
        var stacking = read(Workspace, "stackingOrder", []) || []
        for (var i = 0; i < stacking.length; ++i) {
            var window = stacking[i]
            if (!window || read(window, "deleted", false) || isPassiveVisual(window) || warningSurface.isOwnSurface(window) ||
                    (typeof dimming!=="undefined" && dimming.isOwnSurface(window))) continue
            windows.push(windowRecord(window, outputs, active ? window === active : null))
        }
        var result = { outputs: outputs, windows: windows,
            focusedIdentity: active ? identity(active) : null,
            capturedAt: Date.now(), topologyRevision: sessionEpoch + ":" + topologyGeneration }
        return result
    }
    function snapshot(reason) {
        ++sequence
        var result=capture()
        snapshotCaptured(result,reason)
        if (diagnosticSnapshots) print("OLED_WINDOW_GUARD_SNAPSHOT " + JSON.stringify({ sequence: sequence,
            reason: reason, snapshot: result }))
    }
    function watch(window) {
        if (!window || isPassiveVisual(window) || warningSurface.isOwnSurface(window) || dimming.isOwnSurface(window) || watchedWindows.indexOf(window) >= 0) return
        watchedWindows.push(window)
        windowConnections.push(windowConnection.createObject(adapter, { observedWindow: window }))
    }
    function reconcile(reason) {
        if (shuttingDown) return
        var stacking = read(Workspace, "stackingOrder", []) || []
        for (var i = 0; i < stacking.length; ++i) watch(stacking[i])
        for (var stale = watchedWindows.length - 1; stale >= 0; --stale) {
            if (stacking.indexOf(watchedWindows[stale]) >= 0) continue
            watchedWindows.splice(stale, 1)
            var connection = windowConnections.splice(stale, 1)[0]
            if (connection) connection.destroy()
        }
        for (var orphan = anonymousWindows.length - 1; orphan >= 0; --orphan) {
            if (stacking.indexOf(anonymousWindows[orphan]) >= 0) continue
            anonymousWindows.splice(orphan, 1)
            anonymousIdentities.splice(orphan, 1)
        }
        snapshot(reason)
    }
    property Timer reconcileTimer: Timer {
        interval: adapter.reconcileIntervalMs
        repeat: true
        running: true
        onTriggered: adapter.reconcile("timer")
    }
    property Connections workspaceSignals: Connections {
        target: Workspace
        ignoreUnknownSignals: true
        function onWindowAdded(window) { adapter.watch(window); adapter.reconcile("window-added") }
        function onWindowRemoved(window) {
            var i = adapter.watchedWindows.indexOf(window)
            if (i >= 0) {
                adapter.watchedWindows.splice(i, 1)
                var connection = adapter.windowConnections.splice(i, 1)[0]
                if (connection) connection.destroy()
            }
            adapter.reconcile("window-removed")
        }
        function onWindowActivated() { adapter.reconcile("focus") }
        function onScreensChanged() { adapter.reconcile("outputs") }
        function onCurrentDesktopChanged() { adapter.reconcile("desktop") }
        function onCurrentActivityChanged() { adapter.reconcile("activity") }
    }
    property Component windowConnection: Component {
        Connections {
            required property var observedWindow
            target: observedWindow
            ignoreUnknownSignals: true
            function onFrameGeometryChanged() { adapter.reconcile("geometry") }
            function onOutputChanged() { adapter.reconcile("window-output") }
            function onMinimizedChanged() { adapter.reconcile("state-minimized") }
            function onMaximizedChanged() { adapter.reconcile("state-maximized") }
            function onFullScreenChanged() { adapter.reconcile("state-fullscreen") }
            function onMoveableChanged() { adapter.reconcile("state-movable") }
            function onResizeableChanged() { adapter.reconcile("state-resizable") }
            function onDesktopsChanged() { adapter.reconcile("state-desktops") }
            function onActivitiesChanged() { adapter.reconcile("state-activities") }
            function onTileChanged() { adapter.reconcile("state-tile") }
            function onMaximizeModeChanged() { adapter.reconcile("state-maximize-mode") }
            function onHiddenChanged() { adapter.reconcile("state-hidden") }
        }
    }
    Component.onCompleted: reconcile("startup")
    Component.onDestruction: prepareShutdown()
}

import QtQuick
import org.kde.kwin
import "core/native_observation.js" as Native
import "core/interaction_policy.js" as Policy

QtObject {
    id: consumer
    property var state: Native.createObservationState()
    property var request: null
    property int requestId: 0
    property int completedId: 0
    property double startedAt: 0
    property var localObserver: null
    readonly property bool immediateCancellation: !!localObserver && localObserver.available && localObserver.protocol===1
    property double lastImmediateGeneration: -1
    readonly property string source: immediateCancellation?"compositor-local":state.available?"diagnostic-probe":"unavailable"
    property string loadError: ""
    signal canceled(string reason)
    signal sampled()
    signal activityObserved(string reason)
    property var outputConnectors: []
    property double graceUntilMs: 0
    property double graceDurationMs: 0
    function policyOptions() {return {ignoreFocus:true,graceUntilMs:graceUntilMs};}
    function startWarningGrace(milliseconds) {
        const sample=localSnapshot();
        if (sample.graceProtocol!==1 || !Number.isSafeInteger(sample.monotonicMs) ||
                !Number.isFinite(milliseconds) || milliseconds<0 || milliseconds>60000) throw new Error("warning-clock-unavailable");
        graceDurationMs=Math.floor(milliseconds);graceUntilMs=sample.monotonicMs+graceDurationMs;
    }
    function inWarningGrace() {
        return immediateCancellation && graceUntilMs>0 && localSnapshot().monotonicMs<graceUntilMs;
    }
    function monotonicNow() {return localSnapshot().monotonicMs;}

    function localSnapshot() {
        // QVariant lists exposed by QML are not plain JavaScript arrays.
        return JSON.parse(JSON.stringify(localObserver.snapshot()))
    }
    function apply(result) {
        const old=state.sample
        state=result.state
        if (result.cancel && (!immediateCancellation || Policy.shouldCancel(old,state.sample,outputConnectors,result.cancel,policyOptions()))) canceled(result.cancel)
    }
    function refresh() {
        if (localObserver) {
            requestId++
            if (immediateCancellation) {
                localObserver.refreshAfterUnlock()
                apply(Native.consumeObservation(state,localSnapshot(),Date.now()))
            }
            else apply(Native.unavailable(state,"native-unavailable"))
            completedId=requestId;sampled()
            return requestId
        }
        if (request) return requestId
        requestId++;startedAt=Date.now()
        request=factory.createObject(consumer,{callId:requestId})
        if (!request) {
            apply(Native.unavailable(state,"native-unavailable"));completedId=requestId;sampled()
        } else request.call()
        return requestId
    }
    function localInvalidated(reason) {
        // This handler runs inside InputEventSpy's callback, before the input
        // dispatcher returns. No timer, worker reply or D-Bus delivery is used.
        lastImmediateGeneration=localObserver.cancellationGeneration
        const old=state.sample
        const result=immediateCancellation?Native.consumeObservation(state,localSnapshot(),Date.now()):
            Native.unavailable(state,"native-unavailable")
        state=result.state
        activityObserved(reason)
        if (!state.available || Policy.shouldCancel(old,state.sample,outputConnectors,reason,policyOptions())) canceled(reason)
    }
    function cancellationToken() {
        if (!immediateCancellation) return null
        const sample=localSnapshot()
        return {epoch:sample.epoch,generation:sample.cancellationGeneration,policy:Policy.token(sample,outputConnectors,policyOptions())}
    }
    function tokenCurrent(token) {
        return !!token && immediateCancellation && Policy.current(token.policy,localSnapshot())
    }
    property Connections localSignals: Connections {
        target: consumer.localObserver
        function onInvalidated(reason) { consumer.localInvalidated(reason) }
    }
    function completed(callId,values,failed) {
        if (!request || callId!==requestId) return
        const done=request;request=null;done.destroy()
        try {
            if (failed || Date.now()-startedAt>=500 || Date.now()<startedAt) throw new Error("unavailable")
            if (!values || typeof values[0]!=="string" || values[0].length>16384) throw new Error("invalid")
            apply(Native.consumeObservation(state,JSON.parse(values[0]),Date.now()))
        } catch(error) { apply(Native.unavailable(state,"native-unavailable")) }
        completedId=callId;sampled()
    }
    property Component factory: Component {
        DBusCall {
            required property int callId
            service:"org.kde.KWin"
            path:"/org/oledwindowguard/InputProbe"
            dbusInterface:"org.oledwindowguard.InputProbe1"
            method:"Snapshot"
            onFinished: function(values) { consumer.completed(callId,values,false) }
            onFailed: consumer.completed(callId,[],true)
        }
    }
    property Timer poll: Timer {
        interval:100;repeat:true;running:true
        onTriggered: {
            consumer.apply(Native.expireObservation(consumer.state,Date.now(),500))
            if (consumer.request && Date.now()-consumer.startedAt>=500) {
                const expired=consumer.request;consumer.request=null;expired.destroy()
                consumer.apply(Native.unavailable(consumer.state,"native-timeout"))
                consumer.completedId=consumer.requestId;consumer.sampled()
            }
            consumer.refresh()
        }
    }
    Component.onCompleted: {
        // Optional native module: missing/mismatched builds preserve diagnostic
        // previews while never supplying the immediate cancellation capability.
        try {
            try {
                localObserver=Qt.createQmlObject('import "./native/org/oledwindowguard/Native" as Local; Local.NativeObserver {}',consumer,Qt.resolvedUrl("NativeInstance.qml"))
            } catch(packagedError) {
                localObserver=Qt.createQmlObject('import org.oledwindowguard.Native 1.0; NativeObserver {}',consumer)
            }
        } catch(error) { localObserver=null;loadError=String(error) }
        refresh()
    }
}

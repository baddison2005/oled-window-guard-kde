import QtQuick
import org.kde.kwin

// Each boundary gets a new off-thread provider read. No executable cache and
// no IPC between the final synchronous geometry checks and assignment.
QtObject {
    id: layouts
    required property var observer
    property var call: null
    property var callback: null
    property int serial: 0
    property string requestId: ""
    property double started: 0
    readonly property bool busy: !!callback
    function read(done) {
        if (busy) return false
        requestId=observer.sessionEpoch+":"+(++observer.libraryRequestSerial)
        started=Date.now();callback=done;poll()
        return true
    }
    function finish(value) {
        const done=callback;callback=null
        if (call) {const old=call;call=null;old.destroy()}
        if (done) done(value)
    }
    function cancel() {callback=null;if(call){const old=call;call=null;old.destroy()}}
    function poll() {
        if (!busy || call) return
        if (Date.now()<started || Date.now()-started>=2500) {finish(null);return}
        call=factory.createObject(layouts,{requestId:requestId})
        if (!call) {finish(null);return}
        call.arguments=[observer.sessionEpoch,requestId];call.call()
    }
    function received(id,values,failed) {
        if (!call || id!==requestId) return
        const old=call;call=null;old.destroy()
        try {
            if (failed || !values || typeof values[0]!=="string" || values[0].length>65536 ||
                    Date.now()<started || Date.now()-started>=2500) throw Error("provider-unavailable")
            const reply=JSON.parse(values[0])
            if (!reply.ok || reply.epoch!==observer.sessionEpoch || reply.id!==requestId) throw Error("stale-provider-reply")
            if (reply.pending===true) return
            finish(reply.library)
        } catch(error) {finish(null)}
    }
    property Component factory: Component {
        DBusCall {
            required property string requestId
            service:"org.oledwindowguard.Controller1"
            path:"/org/oledwindowguard/Controller1"
            dbusInterface:"org.oledwindowguard.Controller1"
            method:"LayoutSnapshot"
            onFinished: function(values){layouts.received(requestId,values,false)}
            onFailed: layouts.received(requestId,[],true)
        }
    }
    property Timer retry: Timer {interval:25;repeat:true;running:layouts.busy;onTriggered:layouts.poll()}
}

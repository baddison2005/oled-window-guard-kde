import QtQuick
import org.kde.kwin
import "core/model.js" as Model
import "core/registry.js" as Registry
import "core/restore.js" as Restore
import "core/transaction.js" as Transaction
import "core/native_safety.js" as NativeSafety
import "core/guarded_write.js" as WriteGate

// Single ordinary-shift transaction adapter. Capability activation is separate
// from diagnostic preview availability and defaults to false.
QtObject {
    id: writer
    required property var observer
    required property var planning
    required property var warning
    property bool enabled: false
    property var state: Transaction.createTransactionState()
    property var undoRecord: null
    property var nativeToken: null
    property var configuration: null
    property var registry: null
    property int observations: 0
    property bool assigning: false
    property string lastFailure: "startup"
    property int writeCount: 0
    property var lastWrite: null
    readonly property bool busy: !!state.transaction

    function captureContext() {
        const now=Date.now(), config=planning.configuration
        const full=observer.capture(), desktop=planning.scoped(full)
        const freshRegistry=configuration?Registry.reconcile(desktop,registry,
            {sessionEpoch:observer.sessionEpoch,selectedOutputIds:configuration.settings.movementDisplays,
             excludeFocused:false,allowIdleFocused:true,excludedApps:configuration.settings.excludedApps,applicationAliases:planning.configuration?planning.configuration.applicationAliases:null,ownAppIds:["oled-window-guard"]}):null
        const connected=!!config && planning.lastContact>0 && now>=planning.lastContact && now-planning.lastContact<500
        const normalized=NativeSafety.normalize(planning.nativeObservation.localSnapshot(),full,now,
            {immediateCancellation:planning.nativeObservation.immediateCancellation,
             companionConnected:connected,topologyStable:!!configuration && desktop.topologyRevision===registry.topologyRevision,
             interactionOnly:true,outputIds:planning.movementOutputIds,
             quietMs:0,allowUnfocusedDesktop:true,ignoreKnownHolds:state.phase==="Warning" && planning.nativeObservation.inWarningGrace()})
        return {now:now,snapshot:desktop,registry:freshRegistry,safety:normalized.snapshot,safetyMaxAgeMs:500,
            currentSettingsRevision:config?config.revision:null,settingsRevision:config?config.revision:null,
            enabled:enabled,paused:config?config.settings.paused:null,
            nativeTokenCurrent:planning.nativeObservation.tokenCurrent(nativeToken)}
    }
    function begin(proposal) {
        if (busy || observer.orderedWriter.busy || !enabled || !proposal || proposal!==planning.proposal || !proposal.token || !proposal.token.context ||
                !proposal.result || !proposal.result.plan || !proposal.result.plan.metadata ||
                proposal.result.plan.metadata.mode!=="ordinary_shift" || proposal.result.plan.moves.length!==1 ||
                proposal.result.steps.length || !planning.configuration || proposal.token.context.settingsRevision!==planning.configuration.revision ||
                planning.configuration.settings.paused!==false ||
                !planning.nativeObservation.immediateCancellation) {lastFailure="single-writer-preconditions";return false}
        undoRecord=null;observer.orderedWriter.forgetUndo()
        lastFailure=""
        configuration=JSON.parse(JSON.stringify(planning.configuration))
        registry=JSON.parse(JSON.stringify(planning.registry))
        planning.setScope(proposal.result.plan.outputIds)
        nativeToken=planning.nativeObservation.cancellationToken()
        try {
            const context=captureContext()
            const next=Transaction.transition(state,{type:"resume"});state=next.state
            feed(Object.assign({},context,{type:"begin",generation:state.generation,
                plan:proposal.result.plan,settingsRevision:configuration.revision,
                warningAvailable:true,warningMs:configuration.settings.warningSeconds*1000}))
            if (!busy) lastFailure=state.lastResult?state.lastResult.reason:"single-writer-begin-refused"
            return busy
        } catch(error) { cancel("writer-begin-failed");return false }
    }
    function feed(event) {
        const prior=state.transaction
        const next=Transaction.transition(state,event)
        if (prior && !next.state.transaction) {
            undoRecord=Restore.captureSingleUndo({transaction:prior,result:next.state.lastResult,
                snapshot:event.snapshot,sessionEpoch:observer.sessionEpoch})
        }
        state=next.state
        for (const command of next.commands) handle(command)
    }
    function cancel(reason) {
        if (!busy) return
        lastFailure=reason
        feed({type:"invalidate",reason:reason})
        nativeToken=null
    }
    function acknowledge(type,fields) {
        const pending=state.pendingCommand
        if (!pending) return
        feed(Object.assign({type:type,generation:state.generation,commandId:pending.id,now:Date.now()},fields || {}))
    }
    function handle(command) {
        if (command.type==="showWarning") {
            const output=observer.outputsNow().filter(function(o){return o.id===command.outputId})[0]
            if (!output) {cancel("warning-output-lost");return}
            warning.restoring=false
            warning.windowCount=1
            planning.nativeObservation.startWarningGrace(Math.min(configuration.settings.quietSeconds,configuration.settings.warningSeconds)*1000)
            nativeToken=planning.nativeObservation.cancellationToken()
            warning.showForOutput(state.generation,output.usableRect,command.durationMs)
        } else if (command.type==="hideWarning") {
            warning.hideForGeneration(warning.activeGeneration)
        } else if (command.type==="requestSnapshot") {
            try { acknowledge("snapshotReady",captureContext()) }
            catch(error) {cancel("fresh-context-unavailable")}
        } else if (command.type==="writeGeometry") {
            write(command)
        }
    }
    function write(command) {
        try {
            // Everything up to and including assignment runs in one compositor
            // turn. No timers, IPC or asynchronous callbacks between these checks.
            const context=captureContext(), checked=WriteGate.validate(command,state,context)
            if (!checked.valid) {cancel(checked.reason);return}
            const matches=[]
            for (const window of Workspace.stackingOrder) {
                if (window && !window.deleted && observer.identity(window)===command.identity) matches.push(window)
            }
            if (matches.length!==1 || !planning.nativeObservation.tokenCurrent(nativeToken)) {cancel("write-target-or-token-lost");return}
            const window=matches[0], frame=observer.rect(window.frameGeometry)
            if (!frame || !Model.rectanglesClose(frame,command.expectedFrom,0) || (Workspace.activeWindow===window && !context.registry.windows.some(function(w){return w.identity===command.identity && w.allowIdleFocused===true;}))) {
                cancel("write-target-changed");return
            }
            assigning=true
            const destination=command.destination
            window.frameGeometry=Qt.rect(destination.x,destination.y,destination.width,destination.height)
            assigning=false
            writeCount++;lastWrite={commandId:command.id,generation:command.generation,rollback:!!command.rollback,identity:command.identity,from:command.expectedFrom,to:destination}
            // Assignment acceptance is not commitment; two delayed independent
            // compositor readbacks and collision revalidation establish success.
            acknowledge("writeAck",{accepted:true})
        } catch(error) {assigning=false;cancel("geometry-assignment-failed")}
    }
    property Connections inputEvents: Connections {
        target: writer.planning.nativeObservation
        function onCanceled(reason) {
            if (reason!=="native-input" && reason!=="native-focus") writer.undoRecord=null
            writer.cancel(reason)
        }
    }
    property Connections warningEvents: Connections {
        target: writer.warning
        function onShown(generation) {
            if (writer.busy && generation===writer.state.generation) {
                writer.planning.nativeObservation.startWarningGrace(Math.min(writer.configuration.settings.quietSeconds,writer.configuration.settings.warningSeconds)*1000)
                writer.nativeToken=writer.planning.nativeObservation.cancellationToken()
                writer.acknowledge("warningShown",{shown:true,now:writer.warning.expiresAt-writer.warning.warningDuration})
            }
        }
        function onHidden(generation) {
            if (generation===writer.state.generation) writer.acknowledge("warningHidden",{hidden:true})
        }
        function onExpired(generation) {
            if (generation===writer.state.generation) writer.acknowledge("warningDeadline")
        }
        function onSkipped(generation) { writer.cancel("user-skip") }
        function onFailed(generation,reason) { writer.cancel(reason) }
    }
    property Connections desktopEvents: Connections {
        target: writer.observer
        function onSnapshotCaptured(snapshot,reason) {
            snapshot=writer.planning.scoped(snapshot)
            if (!writer.busy || writer.assigning) return
            // Expected settlement geometry is validated by the transaction
            // readback rules. Other desktop changes are checked on the next tick.
            if (["Warning","Revalidating"].indexOf(writer.state.phase)>=0 &&
                    Registry.snapshotSignature(snapshot)!==Registry.snapshotSignature(writer.state.transaction.snapshot))
                writer.cancel("writer-desktop-changed")
        }
    }
    property Timer verification: Timer {
        interval:25;repeat:true;running:writer.busy
        onTriggered: {
            try {
                const context=writer.captureContext()
                if (!context.enabled || context.paused!==false || !context.nativeTokenCurrent ||
                        context.currentSettingsRevision!==writer.configuration.revision ||
                        !context.safety || !context.safety.allOutputsQuiet) {writer.cancel("writer-authority-lost");return}
                if (writer.state.phase==="Settling" || (writer.state.phase==="RollingBack" && writer.state.transaction.rollbackAck)) {
                    const identity=writer.state.transaction.move.identity
                    const window=context.snapshot.windows.filter(function(w){return w.identity===identity})[0]
                    const tx=writer.state.transaction
                    const settling=writer.state.phase==="Settling"
                    const waiting=window && Model.rectanglesClose(window.rect,settling?tx.move.fromRect:tx.rollbackPlan.moves[0].fromRect,0)
                    if (waiting) {
                        const expected=Model.copyPlain(tx.snapshot)
                        if (!settling) expected.windows.filter(function(w){return w.identity===identity})[0].rect=Model.copyPlain(tx.rollbackPlan.moves[0].fromRect)
                        if (Registry.snapshotSignature(context.snapshot)!==Registry.snapshotSignature(expected)) {
                            writer.cancel("external-desktop-change-during-configure");return
                        }
                    } else writer.feed(Object.assign({},context,{type:"geometryObserved",generation:writer.state.generation,accepted:!!window,
                        geometry:window?window.rect:null,observationId:"read-"+(++writer.observations)}))
                }
                if (writer.state.transaction && ["Applying","Settling","RollingBack"].indexOf(writer.state.phase)>=0) {
                    const tx=writer.state.transaction
                    writer.feed({type:"settlementDeadline",generation:writer.state.generation,now:Date.now(),
                        commandId:writer.state.phase==="RollingBack"?tx.rollbackCommandId:tx.writeCommandId})
                }
            } catch(error) {writer.cancel("writer-context-unavailable")}
        }
    }
    onEnabledChanged: if (!enabled) cancel("writer-disabled")
    Component.onDestruction: {
        observer.prepareShutdown()
        cancel("writer-unloaded")
    }
}

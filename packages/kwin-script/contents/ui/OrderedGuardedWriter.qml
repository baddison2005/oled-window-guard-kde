import QtQuick
import org.kde.kwin
import "core/model.js" as Model
import "core/registry.js" as Registry
import "core/groups.js" as Groups
import "core/safety.js" as Safety
import "core/native_safety.js" as NativeSafety
import "core/ordered_executor.js" as Ordered
import "core/swap_steps.js" as Steps
import "core/ordered_write.js" as WriteGate

// Default-off adapter for validated ordered and built-in group routes,
// including one-shot inverse transactions. Product activation is separate.
QtObject {
    id: writer
    required property var observer
    required property var planning
    required property var warning
    required property var singleWriter
    property bool enabled: false
    property var state: Ordered.createOrderedState()
    property var warningProposal: null
    property var warningSnapshot: null
    property bool warningShown: false
    property bool warningExpired: false
    property var configuration: null
    property var registry: null
    property var nativeToken: null
    property string proposalSignature: ""
    property ImportedLayouts layouts: ImportedLayouts {observer: writer.observer}
    property var currentLibrary: null
    property bool libraryTurn: false
    property int operationGeneration: 0
    property var activeGroup: null
    property var activeCommand: null
    property var readbacks: []
    property int observations: 0
    property bool assigning: false
    property string lastFailure: "startup"
    property int writeCount: 0
    property var lastWrite: null
    readonly property bool busy: !!warningProposal || !!state.tx || layouts.busy

    function captureContext() {
        const now=Date.now(), config=planning.configuration, full=observer.capture(), desktop=planning.scoped(full)
        const freshRegistry=configuration?Registry.reconcile(desktop,registry,
            {sessionEpoch:observer.sessionEpoch,selectedOutputIds:configuration.settings.movementDisplays,
             excludeFocused:false,allowIdleFocused:true,excludedApps:configuration.settings.excludedApps,applicationAliases:config?config.applicationAliases:null,ownAppIds:["oled-window-guard"]}):null
        const connected=!!config && planning.lastContact>0 && now>=planning.lastContact && now-planning.lastContact<500
        const facts=NativeSafety.normalize(planning.nativeObservation.localSnapshot(),full,now,
            {immediateCancellation:planning.nativeObservation.immediateCancellation,companionConnected:connected,
             topologyStable:!!registry && desktop.topologyRevision===registry.topologyRevision,
             interactionOnly:true,outputIds:planning.movementOutputIds,
             quietMs:0,allowUnfocusedDesktop:true,ignoreKnownHolds:state.phase==="Warning" && planning.nativeObservation.inWarningGrace()})
        return {now:now,snapshot:desktop,registry:freshRegistry,safety:facts.snapshot,
            currentSettingsRevision:config?config.revision:null,settingsRevision:config?config.revision:null,
            enabled:enabled,paused:config?config.settings.paused:null,
            nativeTokenCurrent:planning.nativeObservation.tokenCurrent(nativeToken),
            currentLibrary:currentLibrary,library:currentLibrary,libraryFresh:libraryTurn}
    }
    function authority(context) {
        return context.enabled && context.paused===false && context.nativeTokenCurrent &&
            context.settingsRevision===configuration.revision && !!context.safety &&
            Safety.evaluate(context.safety,context.now,500).safe
    }
    function freshLibrary(revision,done) {
        if (!revision) {currentLibrary=null;done();return true}
        const generation=operationGeneration
        return layouts.read(function(library){
            if (generation!==operationGeneration) return
            if (!library || library.status!=="available" || library.executionReady!==true || library.revision!==revision) {
                lastFailure="library-changed-or-unavailable"
                if (busy) cancel(lastFailure)
                return
            }
            currentLibrary=library;libraryTurn=true
            try {done()} catch(error) {cancel("imported-boundary-failed")}
            finally {libraryTurn=false}
        })
    }
    function begin(proposal) {
        if (busy || singleWriter.busy || !enabled || !proposal || proposal!==planning.proposal ||
                !proposal.token || !proposal.token.context || !proposal.result || !proposal.result.plan ||
                !proposal.result.plan.metadata || ["ordinary_shift","swap","coordinated_shift","group-drift","group-rotation"].indexOf(proposal.result.plan.metadata.mode)<0 ||
                !proposal.result.steps || !proposal.result.steps.length || !planning.configuration ||
                proposal.token.context.settingsRevision!==planning.configuration.revision ||
                planning.configuration.settings.paused!==false || !planning.nativeObservation.immediateCancellation) {lastFailure="ordered-writer-preconditions";return false}
        try {
            configuration=Model.copyPlain(planning.configuration);registry=Model.copyPlain(planning.registry)
            if (Registry.snapshotSignature(planning.capture())!==proposal.token.context.snapshotSignature) {lastFailure="proposal-desktop-changed";return false}
            const plan=proposal.result.plan
            planning.setScope(plan.outputIds)
            proposalSignature=Registry.snapshotSignature(planning.capture())
            const receipt=observer.runtime.movementReceipt(proposal,true)
            if (observer.runtime.pending && observer.runtime.pending.action==="MoveNow" && !receipt) {lastFailure="warning-receipt-invalid";return false}
            nativeToken=receipt?receipt.nativeToken:planning.nativeObservation.cancellationToken()
            state=Object.assign({},state,{lastResult:null});lastFailure=""
            operationGeneration++
            freshLibrary(plan.metadata.libraryRevision,function(){beginReady(proposal)})
            return busy
        } catch(error){cancel("ordered-begin-failed");return false}
    }
    function beginReady(proposal) {
        try {
            const plan=proposal.result.plan
            activeGroup=null
            if (plan.metadata.mode.indexOf("group-")===0) {
                const effective=Object.assign({},configuration.settings,(configuration.settings.movementOverrides || {})[plan.outputIds[0]] || {})
                const catalog=plan.metadata.libraryRevision?Groups.groupsFromLibrary(currentLibrary):Groups.builtinGroups(0)
                activeGroup=catalog.filter(function(g){return g.id===effective.groupId && g.id===plan.metadata.groupId})[0] || null
                if (!activeGroup) {lastFailure="group-unavailable";return}
            }
            const context=captureContext()
            if (proposal!==planning.proposal || Registry.snapshotSignature(context.snapshot)!==proposalSignature ||
                    !authority(context) || !Steps.validateSwapSteps(context.snapshot,context.registry,
                    plan,proposal.result.steps,{group:activeGroup,library:currentLibrary}).valid) {lastFailure="stale-or-unsafe-proposal";return}
            forgetUndo();singleWriter.undoRecord=null
            state=Ordered.transitionOrdered(state,{type:"resume"}).state
            showWarning(proposal.result,context)
        } catch(error){cancel("ordered-begin-failed")}
    }
    function forgetUndo() { state=Object.assign({},state,{undoRecord:null}) }
    function showWarning(proposal,context,restoring) {
        if (!restoring && observer.runtime.pending && observer.runtime.pending.action==="MoveNow") {
            if (!observer.runtime.movementReceipt(planning.proposal)) {lastFailure="warning-receipt-invalid";return}
            state=Object.assign({},state,{phase:"Waiting"})
            feed(Object.assign({},context,{type:"begin",generation:state.generation,warningCleared:true,
                plan:proposal.plan,steps:proposal.steps,group:activeGroup,library:currentLibrary,settingsRevision:configuration.revision}))
            return
        }
        warning.restoring=restoring===true
        warningSnapshot=Model.copyPlain(context.snapshot);warningProposal=Model.copyPlain(proposal)
        warningShown=false;warningExpired=false;lastFailure="";activeCommand=null;readbacks=[]
        const output=context.snapshot.outputs.filter(function(o){return o.id===warningProposal.plan.outputIds[0];})[0]
        state=Object.assign({},state,{phase:"Warning"})
        warning.windowCount=warningProposal.plan.moves.length
        planning.nativeObservation.startWarningGrace(Math.min(configuration.settings.quietSeconds,configuration.settings.warningSeconds)*1000)
        nativeToken=planning.nativeObservation.cancellationToken()
        warning.showForOutput(state.generation,output.usableRect,configuration.settings.warningSeconds*1000)
    }
    function restore() {
        // Consume at request time, including refused requests. No skip, stale
        // callback, failure or later Resume can revive this record.
        return restoreConsumed(takeUndo())
    }
    function takeUndo() {
        const record=state.undoRecord || singleWriter.undoRecord
        forgetUndo();singleWriter.undoRecord=null
        return record
    }
    function restoreConsumed(record) {
        if (busy || singleWriter.busy) {lastFailure="restore-busy";return false}
        if (!record) {
            lastFailure="undo-unavailable"
            state=Object.assign({},state,{lastResult:{status:"restore-skipped",reason:lastFailure,alteredFrames:[],unverifiedFrames:[]}})
            return false
        }
        if (!enabled || !planning.configuration || !planning.nativeObservation.immediateCancellation) {lastFailure="restore-unavailable";return false}
        try {
            configuration=Model.copyPlain(planning.configuration);registry=Model.copyPlain(planning.registry)
            planning.setScope(record.plan.outputIds)
            nativeToken=planning.nativeObservation.cancellationToken();activeGroup=null
            state=Object.assign({},state,{lastResult:null});lastFailure=""
            operationGeneration++
            freshLibrary(record.libraryRevision,function(){restoreReady(record)})
            return busy
        } catch(error){cancel("restore-context-unavailable");return false}
    }
    function restoreReady(record) {
        try {
            const context=captureContext()
            if (!authority(context)) {
                lastFailure="unsafe-state"
                state=Object.assign({},state,{lastResult:{status:"restore-skipped",reason:lastFailure,alteredFrames:[],unverifiedFrames:[]}})
                return false
            }
            state=Ordered.transitionOrdered(state,{type:"resume"}).state
            const queued=Ordered.transitionOrdered(Object.assign({},state,{undoRecord:record}),
                Object.assign({},context,{type:"queueRestore",generation:state.generation,
                    settingsRevision:configuration.revision,sessionEpoch:observer.sessionEpoch}))
            state=queued.state
            if (!queued.commands.length) {lastFailure=state.lastResult.reason;return false}
            showWarning(queued.commands[0],context,true)
            return busy
        } catch(error){cancel("restore-context-unavailable");return false}
    }
    function feed(event) {
        const next=Ordered.transitionOrdered(state,event)
        state=next.state
        if (!state.tx) {activeCommand=null;readbacks=[]}
        for (const command of next.commands) handle(command)
    }
    function cancel(reason) {
        if (!busy) return
        operationGeneration++;layouts.cancel()
        lastFailure=reason
        warningProposal=null;warningSnapshot=null;nativeToken=null;activeCommand=null;readbacks=[]
        feed({type:"activity"})
        warning.hideForGeneration(warning.activeGeneration)
    }
    function acknowledge(fields) {
        const pending=state.pending
        if (pending) feed(Object.assign({generation:state.generation,commandId:pending.id},fields))
    }
    function handle(command) {
        if (command.type==="requestSnapshot") {
            if (layouts.busy) {operationGeneration++;layouts.cancel()}
            const generation=state.generation
            freshLibrary(state.tx.libraryRevision,function(){
                if (!state.pending || state.pending.id!==command.id || state.generation!==generation) return
                acknowledge(Object.assign({},captureContext(),{type:"snapshotReady"}))
            })
        } else if (command.type==="writeGeometry") write(command)
    }
    function write(command) {
        try {
            const context=captureContext(), checked=WriteGate.validate(command,state,context)
            if (!checked.valid) {cancel(checked.reason);return}
            const matches=[]
            for (const window of Workspace.stackingOrder)
                if (window && !window.deleted && observer.identity(window)===command.identity) matches.push(window)
            if (matches.length!==1 || !planning.nativeObservation.tokenCurrent(nativeToken)) {cancel("ordered-target-or-token-lost");return}
            const window=matches[0]
            if (!Model.rectanglesClose(observer.rect(window.frameGeometry),command.expectedFrom,0) || (Workspace.activeWindow===window && !context.registry.windows.some(function(w){return w.identity===command.identity && w.allowIdleFocused===true;}))) {
                cancel("ordered-target-changed");return
            }
            if (command.destination.width!==command.expectedFrom.width || command.destination.height!==command.expectedFrom.height) {
                // Read-only native validation accounts for current client
                // minima/maxima, resize increments and stable decorations.
                // The frame setter alone may bypass these client constraints.
                if (!planning.nativeObservation.localObserver.frameSizeSupported(String(window.internalId),command.destination.width,command.destination.height)) {
                    acknowledge({type:"writeResult",accepted:false,reason:"client-size-constraint"});return
                }
            }
            activeCommand=Model.copyPlain(command);readbacks=[]
            assigning=true
            const destination=command.destination
            window.frameGeometry=Qt.rect(destination.x,destination.y,destination.width,destination.height)
            assigning=false
            writeCount++;lastWrite={commandId:command.id,generation:command.generation,stepIndex:command.stepIndex,
                rollback:command.rollback,identity:command.identity,from:command.expectedFrom,to:command.destination}
        } catch(error) {
            assigning=false
            // A setter may throw after changing the frame. Reconcile before
            // deciding whether any verified predecessor can safely be reversed.
            acknowledge({type:"writeResult",accepted:false})
        }
    }
    property Connections inputEvents: Connections {
        target: writer.planning.nativeObservation
        function onCanceled(reason){
            if (reason!=="native-input" && reason!=="native-focus") {writer.forgetUndo();writer.singleWriter.undoRecord=null}
            writer.cancel(reason)
        }
    }
    property Connections warningEvents: Connections {
        target: writer.warning
        function onShown(generation) {
            if (writer.warningProposal && generation===writer.state.generation) {
                writer.planning.nativeObservation.startWarningGrace(Math.min(writer.configuration.settings.quietSeconds,writer.configuration.settings.warningSeconds)*1000)
                writer.nativeToken=writer.planning.nativeObservation.cancellationToken()
                writer.warningShown=true
            }
        }
        function onExpired(generation) {
            if (writer.warningProposal && generation===writer.state.generation)
                { writer.warningExpired=true;writer.warning.hideForGeneration(generation) }
        }
        function onHidden(generation) {
            if (!writer.warningProposal || generation!==writer.state.generation) return
            const proposal=writer.warningProposal
            writer.freshLibrary(proposal.plan.metadata.libraryRevision,function(){writer.warningHidden(generation)})
        }
        function onSkipped(generation){if (writer.busy && generation===writer.state.generation) writer.cancel("user-skip")}
        function onFailed(generation,reason){if (writer.busy && generation===writer.state.generation) writer.cancel(reason)}
    }
    function warningHidden(generation) {
            try {
                if (!writer.warningProposal || generation!==writer.state.generation) return
                const context=writer.captureContext(), proposal=writer.warningProposal
                if (!writer.warningShown || !writer.warningExpired || !writer.authority(context) || Registry.snapshotSignature(context.snapshot)!==Registry.snapshotSignature(writer.warningSnapshot)) {
                    writer.cancel("ordered-warning-context-changed");return
                }
                writer.warningProposal=null;writer.warningSnapshot=null
                writer.state=Object.assign({},writer.state,{phase:"Waiting"})
                writer.feed(Object.assign({},context,{type:"begin",generation:writer.state.generation,
                    warningCleared:true,plan:proposal.plan,steps:proposal.steps,group:writer.activeGroup,library:writer.currentLibrary,settingsRevision:writer.configuration.revision}))
            } catch(error){writer.cancel("ordered-warning-context-unavailable")}
    }
    property Connections desktopEvents: Connections {
        target: writer.observer
        function onSnapshotCaptured(snapshot,reason) {
            snapshot=writer.planning.scoped(snapshot)
            if (writer.warningProposal && Registry.snapshotSignature(snapshot)!==Registry.snapshotSignature(writer.warningSnapshot))
                writer.cancel("ordered-warning-desktop-changed")
        }
    }
    function verify(command) {
        try {
            if (!state.tx || state.phase!=="Applying" || !state.pending || state.pending.id!==command.id ||
                    !activeCommand || activeCommand.id!==command.id) return
            const context=captureContext(), tx=state.tx
            if (!authority(context)) {cancel("ordered-authority-lost");return}
            const window=Ordered.orderedWindow(context.snapshot,command.identity)
            const expected=Model.copyPlain(tx.expected)
            if (window && Ordered.orderedSame(window.rect,command.expectedFrom)) {
                if (!Ordered.orderedSnapshotMatches(tx,context.snapshot,expected)) {cancel("ordered-external-desktop-change");return}
            } else {
                // An xdg configure/ack can expose an intermediate frame for
                // several compositor samples. Observe without another write;
                // preserve enough time for two readbacks before the deadline.
                if (!Ordered.orderedSame(window.rect,command.destination) && context.now<tx.writeDeadline-60) {
                    expected[command.identity]=window.rect;
                    if (!Ordered.orderedSnapshotMatches(tx,context.snapshot,expected)) {cancel("ordered-external-desktop-change");return;}
                    readbacks=[];return;
                }
                // Timer callbacks are not evidence of elapsed wall-clock time.
                // Keep sampling until the executor's minimum separation holds,
                // including when an asynchronous library read completes near
                // the next timer callback. The existing deadline still applies.
                if (readbacks.length && context.now-readbacks[readbacks.length-1].now<16) return
                readbacks=readbacks.concat([Object.assign({},context,{id:"ordered-read-"+(++observations)})])
                if (readbacks.length>=2) {
                    const observations=readbacks
                    activeCommand=null;readbacks=[]
                    acknowledge({type:"writeResult",accepted:true,observations:observations})
                }
            }
        } catch(error) {cancel("ordered-context-unavailable")}
    }
    property Timer verification: Timer {
        interval:25;repeat:true;running:writer.busy
        onTriggered: {
            try {
                const context=writer.captureContext()
                if (!writer.authority(context)){writer.cancel("ordered-authority-lost");return}
                const tx=writer.state.tx, command=writer.activeCommand
                if (!tx || writer.state.phase!=="Applying" || !command) return
                if (!writer.layouts.busy) writer.freshLibrary(tx.libraryRevision,function(){writer.verify(command)})
                if (writer.state.tx && writer.state.pending && writer.state.pending.id===command.id)
                    writer.feed({type:"stepTimeout",generation:writer.state.generation,commandId:command.id,now:Date.now()})
            } catch(error){writer.cancel("ordered-context-unavailable")}
        }
    }
    onEnabledChanged: if (!enabled) cancel("ordered-disabled")
    Component.onDestruction: {
        observer.prepareShutdown()
        cancel("ordered-unloaded")
    }
}

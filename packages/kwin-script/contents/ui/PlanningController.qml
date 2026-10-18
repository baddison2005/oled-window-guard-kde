import QtQuick
import QtQml.WorkerScript
import org.kde.kwin
import "core/model.js" as Model
import "core/registry.js" as Registry
import "core/planner.js" as Planner
import "core/swap_steps.js" as Steps
import "core/planning_dispatch.js" as Dispatch
import "core/groups.js" as Groups
import "core/group_safety.js" as GroupSafety
import "core/preview.js" as Preview

// Read-only production integration. Proposals never issue geometry writes.
QtObject {
    id: controller
    required property var observer
    property var state: Dispatch.createPlanningState()
    property var worker: null
    property int workerNumber: 0
    property var configuration: null
    property var registry: null
    property var movementOutputIds: []
    property var active: null
    property var pendingReply: null
    property int replyExchangeMin: 0
    property int replyNativeMin: 0
    property NativeObservation nativeObservation: NativeObservation {
        onCanceled: (reason)=>controller.invalidate(reason)
        onSampled: controller.accept()
    }
    property ImportedLayouts layouts: ImportedLayouts { observer: controller.observer }
    property var currentLibrary: null
    property bool finalLibraryRead: false
    property var proposal: null
    property var report: null
    property var prepared: ({})
    property var exchange: null
    property double exchangeStarted: 0
    property double lastContact: 0
    property int exchangeId: 0
    property string lastCommand: ""
    property string lastSignature: ""
    property string lastTopology: ""
    property string lastStatus: "unavailable"
    property string lastExchangeFailure: ""
    property double lastExchangeFailureAt: 0

    function exchangeFailed(reason) {
        const now=Date.now(), bounded=String(reason).slice(0,256)
        if (bounded!==lastExchangeFailure || now-lastExchangeFailureAt>=5000) {
            console.warn("OWG_COMPANION_EXCHANGE_FAILED",bounded,"roundTripMs",now-exchangeStarted)
            lastExchangeFailure=bounded;lastExchangeFailureAt=now
        }
    }

    function setScope(ids) {
        if (JSON.stringify(ids)===JSON.stringify(movementOutputIds)) return
        movementOutputIds=ids.slice();lastSignature=""
        nativeObservation.outputConnectors=observer.outputsNow().filter(function(o){return ids.indexOf(o.id)>=0;}).map(function(o){return o.connector;})
    }
    function scoped(snapshot) { return Registry.scopeSnapshot(snapshot,movementOutputIds) }
    function capture() { return scoped(observer.capture()) }
    function dispatch(event) {
        const next=Dispatch.transitionPlanning(state,event)
        state=next.state
        return next.commands
    }
    function invalidate(reason) {
        const reading=layouts.busy
        layouts.cancel();finalLibraryRead=false
        const oldProposal=proposal, purpose=report?report.purpose:null
        const drained=pendingReply
        dispatch({type:"invalidate",reason:reason})
        if (drained) dispatch(Object.assign({},drained,{type:"reply",now:Date.now(),currentContext:drained.token.context}))
        proposal=null;pendingReply=null
        if (reading && report && report.status==="planning") report={id:report.id,status:"discarded",reason:reason,moves:0,purpose:purpose}
        else if (active) report={id:active.id,status:"discarded",reason:reason,moves:0,purpose:purpose}
        else if (oldProposal && report) report={id:report.id,status:"discarded",reason:reason,moves:0,purpose:purpose}
        active=null;lastStatus=reason
    }
    function observed(snapshot,reason) {
        snapshot=scoped(snapshot)
        if (snapshot.windows.length>256 || snapshot.outputs.length>16 || !Model.validateSnapshot(snapshot).valid) {
            invalidate("invalid-or-excess-desktop");lastSignature="";return
        }
        const signature=Registry.snapshotSignature(snapshot)
        if ((lastSignature && lastSignature!==signature) || (lastTopology && lastTopology!==snapshot.topologyRevision))
            invalidate(reason || "desktop-changed")
        lastSignature=signature;lastTopology=snapshot.topologyRevision
    }
    function context(snapshot) {
        return {sessionEpoch:observer.sessionEpoch,settingsRevision:configuration.revision,
            topologyRevision:snapshot.topologyRevision,snapshotSignature:Registry.snapshotSignature(snapshot),libraryRevision:currentLibrary?currentLibrary.revision:null}
    }
    function replaceWorker() {
        if (worker) worker.destroy()
        worker=workerFactory.createObject(controller,{source:Qt.resolvedUrl("planning_worker.js")})
        workerNumber++
    }
    property Component workerFactory: Component {
        WorkerScript { onMessage: (messageObject)=>controller.received(messageObject) }
    }
    function received(reply) {
        if (reply.ok===false) console.warn("OWG_PLANNING_WORKER_FAILED",String(reply.error))
        // Re-read companion settings before accepting the result, even when the
        // worker finishes between two heartbeat exchanges.
        if (!state.occupied || !Dispatch.samePlanningToken(reply.token,state.occupied.token)) return
        if (!active) {
            dispatch(Object.assign({},reply,{type:"reply",now:Date.now(),currentContext:reply.token.context}))
            return
        }
        pendingReply=reply;finalLibraryRead=false;replyExchangeMin=exchangeId+1
        // Require a request started after this reply. An already pending poll
        // may have sampled input before the worker completed.
        replyNativeMin=nativeObservation.requestId+1
        if (!nativeObservation.request) nativeObservation.refresh()
        if (!exchange) contact()
    }
    function issue(command) {
        if (!command || command.id===lastCommand || layouts.busy) return
        const config=configuration, effective=config?Object.assign({},config.settings,(config.settings.movementOverrides || {})[command.outputId] || {}):null
        if (effective && effective.mode==="group" && effective.groupId.indexOf("kde:")===0) {
            const generation=state.generation, token=nativeObservation.cancellationToken()
            report={id:command.id,status:"planning",reason:null,moves:0}
            if (!layouts.read(function(library){
                if (generation!==state.generation || (!configuration || configuration.revision!==config.revision || configuration.controlGeneration!==config.controlGeneration) || (token && !nativeObservation.tokenCurrent(token))) return
                currentLibrary=library;issueReady(command)
            })) report={id:command.id,status:"rejected",reason:"provider-busy",moves:0}
        } else {currentLibrary=null;issueReady(command)}
    }
    function issueReady(command) {
        if (!command || typeof command.id!=="string" || command.id===lastCommand) return
        lastCommand=command.id
        try {
            if (command.revision!==configuration.revision || state.phase!=="Idle") throw new Error("stale-or-busy-request")
            const settings=configuration.settings,outputId=command.outputId
            if (settings.movementDisplays.indexOf(outputId)<0) throw new Error("output-not-selected")
            setScope([command.outputId])
            const snapshot=capture()
            observed(snapshot,"request-desktop-changed")
            if (!lastSignature) throw new Error("invalid-or-excess-desktop")
            const effective=Object.assign({},settings,(settings.movementOverrides || {})[outputId] || {})
            const options=Object.assign({},effective,{outputId:outputId,seed:command.seed,
                deadlineMs:30,maxEvaluations:2400})
            // Registry policy is always applied before worker planning.
            registry=Registry.reconcile(snapshot,registry || Registry.createRegistryState(observer.sessionEpoch),
                {sessionEpoch:observer.sessionEpoch,selectedOutputIds:settings.movementDisplays,
                 excludeFocused:false,allowIdleFocused:true,excludedApps:settings.excludedApps,applicationAliases:configuration.applicationAliases,
                 ownAppIds:["oled-window-guard"]})
            if (registry.selectedOutputIds.indexOf(outputId)<0) throw new Error("output-selection-unverified")
            let mode=effective.mode==="drift"?(effective.shiftHorizontalOrder || effective.shiftVerticalOrder?"coordinated":"shift"):effective.mode
            const job={mode:mode,snapshot:snapshot,registry:registry,options:options}
            if (mode==="group") {
                job.group=Groups.builtinGroups(0).filter(function(g){return g.id===effective.groupId})[0]
                if (!job.group) {
                    job.group=Groups.groupsFromLibrary(currentLibrary).filter(function(g){return g.id===effective.groupId})[0]
                    job.library=currentLibrary
                }
                if (!job.group) throw new Error("imported-or-missing-group-unavailable")
            }
            const commands=dispatch({type:"request",now:Date.now(),timeoutMs:2000,context:context(snapshot),job:job})
            if (commands.length!==1) throw new Error("planning-request-rejected")
            job.kind=command.kind || "preview";job.seed=command.seed
            active={id:command.id,job:job,token:commands[0].token,nativeToken:nativeObservation.cancellationToken()}
            report={id:command.id,status:"planning",reason:null,moves:0}
            lastStatus="planning"
            worker.sendMessage(commands[0])
        } catch(error) {
            report={id:command.id,status:"rejected",reason:String(error),moves:0};lastStatus="rejected"
        }
    }
    function accept() {
        if (!pendingReply || !active || !configuration || exchangeId<replyExchangeMin ||
                nativeObservation.completedId<replyNativeMin) return
        if (active.job.library && !finalLibraryRead) {
            if (layouts.busy) return
            const expected=active
            layouts.read(function(library){
                if (active!==expected) return
                currentLibrary=library;finalLibraryRead=true;accept()
            })
            return
        }
        const reply=pendingReply,request=active,snapshot=capture()
        if (request.nativeToken && !nativeObservation.tokenCurrent(request.nativeToken)) {
            invalidate("native-token-changed");return
        }
        observed(snapshot,"reply-desktop-changed")
        if (!active) return
        // Notification/OSD rows may change without changing application-plan
        // context. Reconcile those rows before full final-plan validation.
        registry=Registry.reconcile(snapshot,registry || Registry.createRegistryState(observer.sessionEpoch),
            {sessionEpoch:observer.sessionEpoch,selectedOutputIds:configuration.settings.movementDisplays,
             excludeFocused:false,allowIdleFocused:true,excludedApps:configuration.settings.excludedApps,
             applicationAliases:configuration.applicationAliases,ownAppIds:["oled-window-guard"]})
        pendingReply=null
        const commands=dispatch(Object.assign({},reply,{type:"reply",now:Date.now(),currentContext:context(snapshot)}))
        active=null
        if (commands.length!==1) {
            report={id:request.id,status:"discarded",reason:state.lastResult.reason,moves:0};lastStatus="discarded";return
        }
        const result=commands[0].result,plan=result.plan
        if (plan) {
            const checked=plan.metadata.mode==="ordinary_shift"?Planner.validateFinalPlan(snapshot,registry,plan):
                request.job.mode==="group"?GroupSafety.validateGroupFinalPlan(snapshot,registry,plan,request.job.group,currentLibrary):
                request.job.mode==="coordinated"?Planner.validateCoordinatedFinalPlan(snapshot,registry,plan):Planner.validateSwapFinalPlan(snapshot,registry,plan)
            const replay=!result.steps.length || Steps.validateSwapSteps(snapshot,registry,plan,result.steps,{group:request.job.group,library:currentLibrary}).valid
            if (!checked.valid || !replay) {
                invalidate("fresh-plan-validation-failed")
                report={id:request.id,status:"rejected",reason:"fresh-plan-validation-failed",moves:0};return
            }
        }
        proposal=plan?{token:commands[0].token,result:result,library:currentLibrary}:null
        report={id:request.id,status:plan?"ready":"no-plan",reason:result.reason || result.stats.stopReason || null,
            moves:plan?plan.moves.length:0,purpose:request.job.kind,preview:previewDrawing(snapshot,plan,request.job.options.outputId)}
        if (plan) prepared[request.job.options.outputId]={result:Model.copyPlain(result),library:currentLibrary,
            signature:Registry.snapshotSignature(snapshot),settingsKey:movementSettingsKey(request.job.options.outputId)}
        lastStatus=report.status
        observer.runtime.proposed()
    }
    function movementSettingsKey(outputId) {
        if (!configuration) return "";
        const settings=Model.copyPlain(configuration.settings);delete settings.paused;
        return JSON.stringify(settings)+JSON.stringify(configuration.applicationAliases || {});
    }
    function reusePrepared(id,outputId,purpose) {
        const cached=prepared[outputId];
        if (!cached || !configuration || active || layouts.busy || state.phase!=="Idle" ||
                cached.settingsKey!==movementSettingsKey(outputId)) return false;
        setScope([outputId]);const snapshot=capture();
        if (cached.signature!==Registry.snapshotSignature(snapshot)) return false;
        registry=Registry.reconcile(snapshot,registry || Registry.createRegistryState(observer.sessionEpoch),
            {sessionEpoch:observer.sessionEpoch,selectedOutputIds:configuration.settings.movementDisplays,
             excludeFocused:false,allowIdleFocused:true,excludedApps:configuration.settings.excludedApps,
             applicationAliases:configuration.applicationAliases,ownAppIds:["oled-window-guard"]});
        currentLibrary=cached.library;
        // Only a local candidate is retained. Final native tokens, settings,
        // geometry, library and write gates are freshly checked by the writer.
        proposal={token:{context:context(snapshot)},result:Model.copyPlain(cached.result),library:currentLibrary};
        report={id:id,status:"ready",purpose:purpose,moves:proposal.result.plan.moves.length,
            preview:previewDrawing(snapshot,proposal.result.plan,outputId)};
        return true;
    }
    function prepareWarning(id,outputId,seed) {
        if (active || state.occupied || layouts.busy || state.phase!=="Idle") return false;
        if (!reusePrepared(id,outputId,"warning-preview")) issue({id:id,outputId:outputId,
            revision:configuration.revision,seed:seed,kind:"warning-preview"});
        return true;
    }
    function hasEligibleWindows(outputId) {
        if(!configuration)return false
        const snapshot=Registry.scopeSnapshot(observer.capture(),[outputId])
        const output=snapshot.outputs.filter(function(o){return o.id===outputId})[0]
        if(!output)return false
        const state=Registry.reconcile(snapshot,Registry.createRegistryState(observer.sessionEpoch),
            {sessionEpoch:observer.sessionEpoch,selectedOutputIds:configuration.settings.movementDisplays,
             excludeFocused:false,allowIdleFocused:true,excludedApps:configuration.settings.excludedApps,
             applicationAliases:configuration.applicationAliases,ownAppIds:["oled-window-guard"]})
        return snapshot.windows.some(function(w){return Planner.eligible(w,snapshot,output,state.windows.filter(function(r){return r.identity===w.identity})[0])})
    }
    function previewDrawing(snapshot,plan,outputId) {
        return Preview.drawing(snapshot,plan,outputId,configuration?configuration.settings:null,
            configuration?configuration.applicationAliases:null)
    }
    function contact() {
        if (exchange) return
        exchangeId++;exchangeStarted=Date.now()
        exchange=exchangeFactory.createObject(controller,{callId:exchangeId})
        if (!exchange) { invalidate("companion-unavailable");return }
        const outputs=observer.outputsNow().map(function(o){return {id:o.id,connector:o.connector,geometry:o.geometry,scale:o.scale}})
        exchange.arguments=[observer.sessionEpoch,exchangeId,Preview.boundedPayload({adapterSource:Qt.resolvedUrl("main.qml").toString(),outputs:outputs,planning:lastStatus,available:state.phase==="Idle" && !!lastSignature,report:report,runtime:observer.runtime.status(),
            livePreview:previewDrawing(observer.capture(),null,configuration && configuration.previewOutputId || (configuration && configuration.settings.movementDisplays[0]) || (observer.outputsNow()[0] || {}).id),
            native:{source:nativeObservation.source,immediateCancellation:nativeObservation.immediateCancellation,available:nativeObservation.state.available}},16384)]
        exchange.call()
    }
    function exchanged(callId,values,failed) {
        if (!exchange || callId!==exchangeId) return
        const completed=exchange;exchange=null;completed.destroy()
        try {
            if (failed) throw new Error("companion-unavailable")
            if (!values || typeof values[0]!=="string" || values[0].length>65536) throw new Error("oversized-companion-reply")
            const next=JSON.parse(values[0])
            if (next.ok===false) throw new Error("companion-rejected:"+String(next.reason).slice(0,128))
            if (next.ok!==true || next.epoch!==observer.sessionEpoch || next.sequence!==callId ||
                    typeof next.revision!=="string" || !next.settings ||
                    typeof next.controllerEpoch!=="string" || !next.controllerEpoch ||
                    !Number.isSafeInteger(next.controlGeneration) || next.controlGeneration<0 ||
                    !Number.isFinite(next.monotonicMs) || next.monotonicMs<0) throw new Error("invalid-companion-reply")
            if (configuration && (configuration.revision!==next.revision || configuration.controlGeneration!==next.controlGeneration)) invalidate("settings-changed")
            configuration=next;lastContact=Date.now()
            observer.runtime.synchronize(next)
            accept()
            if (next.runtimeCommand) observer.runtime.command(next.runtimeCommand)
            if (next.command && !observer.runtime.busy) issue(next.command)
        } catch(error) { exchangeFailed(error);configuration=null;invalidate("companion-unavailable") }
    }
    property Component exchangeFactory: Component {
        DBusCall {
            required property int callId
            service: "org.oledwindowguard.Controller1"
            path: "/org/oledwindowguard/Controller1"
            dbusInterface: "org.oledwindowguard.Controller1"
            method: "ObserverExchange"
            onFinished: function(returnValue) { controller.exchanged(callId,returnValue,false) }
            onFailed: controller.exchanged(callId,[],true)
        }
    }
    property Connections observerEvents: Connections {
        target: controller.observer
        function onSnapshotCaptured(snapshot,reason) { controller.observed(snapshot,reason) }
    }
    property Timer heartbeat: Timer {
        interval:100;repeat:true;running:true
        onTriggered: {
            const now=Date.now()
            if (controller.worker && controller.worker.ready && !controller.state.workerEpoch)
                controller.dispatch({type:"workerReady",workerEpoch:controller.observer.sessionEpoch+":"+controller.workerNumber})
            if (controller.state.occupied && now>=controller.state.occupied.deadline) {
                controller.dispatch({type:"timeout",now:now,token:controller.state.occupied.token})
                controller.invalidate("worker-timeout")
            }
            if (controller.state.phase==="Unavailable" && controller.worker && controller.worker.ready)
                controller.replaceWorker()
            if (controller.exchange && now-controller.exchangeStarted>=1000) {
                controller.exchangeFailed("companion-timeout")
                const expired=controller.exchange;controller.exchange=null;expired.destroy()
                controller.configuration=null;controller.invalidate("companion-timeout")
            }
            if (controller.lastContact && now-controller.lastContact>=1000 && controller.configuration) {
                controller.configuration=null;controller.invalidate("companion-stale")
            }
            if (!controller.exchange) controller.contact()
        }
    }
    Component.onCompleted: replaceWorker()
}

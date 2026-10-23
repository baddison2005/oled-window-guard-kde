import QtQuick
import "core/native_safety.js" as NativeSafety
import "core/scheduler.js" as Scheduler
import "core/guarding.js" as Guarding
import "core/swap_steps.js" as Steps

QtObject {
    id: runtime
    required property var observer
    required property var planning
    property var guards: Guarding.createGuardingState()
    readonly property bool armed: guards.movement || guards.brightness
    readonly property bool movementEnabled: enabled && guards.movement
    readonly property bool brightnessEnabled: enabled && guards.brightness
    property string controllerEpoch: ""
    property string configKey: ""
    property string settingsRevision: ""
    property bool sessionResumeAllowed: false
    property double resumeContactUntil: 0
    property string lastCommand: ""
    property var pending: null
    property bool executing: false
    property var result: null
    property var commandReport: null
    // Bounded diagnostics preserve first cancellation and later topology disarm
    // separately. They never participate in write authorization.
    property var cancellationTrace: []
    property int scheduledSerial: 0
    property double startingUntil: 0
    property int warningSerial: 1000000000
    property var scheduler: Scheduler.createSchedulerState()
    readonly property bool enabled: armed && !!planning.configuration && planning.configuration.settings.paused===false &&
        Date.now()-planning.lastContact<500
    readonly property bool busy: !!pending || observer.writer.busy || observer.orderedWriter.busy
    function schedulerNow() {
        // Latest companion heartbeat supplies an OS monotonic sample. Scheduling
        // can wait for the next heartbeat; wall-clock changes never advance it.
        return planning.configuration?planning.configuration.monotonicMs:(scheduler.lastNow || 0)
    }
    // Starting a warning may overlap known interaction during its grace period.
    // Planning and every write continue to use strict facts without this flag.
    function facts(quiet,outputIds,forWarning) {
        try {
            const config=planning.configuration, snapshot=observer.capture()
            if (!config || !planning.nativeObservation.immediateCancellation) return {safe:false,reason:"native-unavailable"}
            const ids=outputIds || (pending && pending.outputId?[pending.outputId]:[])
            const options={immediateCancellation:true,companionConnected:Date.now()-planning.lastContact<500,
                 topologyStable:true,quietMs:0,interactionOnly:true,outputIds:ids,allowUnfocusedDesktop:true,ignoreKnownHolds:forWarning===true}
            if (!ids.length) {
                const candidates=selected().concat(observer.dimming.selected()).filter(function(id,index,all){return all.indexOf(id)===index;})
                for (const id of candidates) {
                    const result=NativeSafety.normalize(planning.nativeObservation.localSnapshot(),snapshot,Date.now(),Object.assign({},options,{outputIds:[id]}))
                    if (result.safe) return result
                }
            }
            return NativeSafety.normalize(planning.nativeObservation.localSnapshot(),snapshot,Date.now(),
                options)
        } catch(error){return {safe:false,reason:"native-unavailable"}}
    }
    function dimmingFacts() {
        try {
            const config=planning.configuration
            const normalized=NativeSafety.normalize(planning.nativeObservation.localSnapshot(),observer.capture(),Date.now(),
                {immediateCancellation:planning.nativeObservation.immediateCancellation,companionConnected:!!config && Date.now()-planning.lastContact<500,
                 topologyStable:true,quietMs:0,interactionOnly:true,outputIds:observer.dimming.selected(),allowUnfocusedDesktop:true})
            // Dimming continuously protects focus and the pressed window. A
            // known held button is not a reason to brighten unrelated windows.
            return Object.assign({},normalized,{safe:!!normalized.snapshot})
        } catch(error) {return {safe:false,reason:"native-unavailable"}}
    }
    function selected() {
        const config=planning.configuration
        if (!config) return []
        const connected=observer.outputsNow().map(function(o){return o.id})
        return config.settings.movementDisplays.filter(function(id){return connected.indexOf(id)>=0})
    }
    function activeOutputs(kind) {
        const configured=kind==="movement"?selected():observer.dimming.selected()
        return guards[kind]?(guards[kind+"Outputs"] || []).map(function(o){return o.id}).filter(function(id){return configured.indexOf(id)>=0}):[]
    }
    function configuredOutputs(ids) {return observer.outputsNow().filter(function(o){return ids.indexOf(o.id)>=0})}
    function rebuildScheduler(preserve) {
        const previous=scheduler,ids=guards.movement?activeOutputs("movement"):selected(),intervals={}
        const config=planning.configuration
        ids.forEach(function(id){const effective=Object.assign({},config.settings,(config.settings.movementOverrides || {})[id] || {});intervals[id]=effective.intervalMinutes*60000})
        scheduler=Scheduler.transitionScheduler(scheduler,{type:"settingsChanged",now:schedulerNow(),outputIds:ids,intervals:intervals}).state
        if(movementEnabled) {
            scheduler=Scheduler.transitionScheduler(scheduler,{type:"resume",now:schedulerNow()}).state
            if(preserve) {
                const deadlines=Object.assign({},scheduler.deadlines)
                ids.forEach(function(id){if(Number.isFinite(previous.deadlines[id]))deadlines[id]=previous.deadlines[id]})
                scheduler=Object.assign({},scheduler,{deadlines:deadlines})
            }
        }
    }
    function topologyChanged() {
        guards=Guarding.topology(guards,observer.outputsNow())
        cancel("native-topology",false)
        observer.writer.undoRecord=null;observer.orderedWriter.forgetUndo()
        observer.dimming.retainOutputs(activeOutputs("brightness"))
        if(planning.configuration)rebuildScheduler(true)
    }
    function resumeScopes(kind) {
        const ticket=guards.unlock, ids=kind==="movement"?selected():observer.dimming.selected()
        return ticket?configuredOutputs(ids).filter(function(o){return (ticket[kind+"Outputs"] || []).some(function(r){return r.id===o.id})}):[]
    }
    function waitingForSessionContact() {
        if(!guards.unlock || !sessionResumeAllowed || !planning.nativeObservation.immediateCancellation)return false
        const sample=planning.nativeObservation.localSnapshot()
        if(!Guarding.sameSession(guards.unlock,sample))return false
        if(Guarding.dormant(sample)) {resumeContactUntil=0;return true}
        const now=planning.nativeObservation.monotonicNow()
        if(!resumeContactUntil)resumeContactUntil=now+5000
        return now<resumeContactUntil
    }
    function status() {
        const safety=facts(false,null,true), hasOutput=selected().length>0
        const dimmingOutput=observer.dimming.selected().length>0
        const dimmingSafety=dimmingFacts()
        const ready=(safety.safe && hasOutput) || (dimmingSafety.safe && dimmingOutput)
        const warning=observer.warningSurface
        const undo=observer.orderedWriter.state.undoRecord || observer.writer.undoRecord
        const restoring=pending && pending.action==="Restore" && observer.orderedWriter.state.phase==="Warning" && observer.orderedWriter.warningProposal
        const restorePreview=restoring?planning.previewDrawing(observer.orderedWriter.warningSnapshot,
            observer.orderedWriter.warningProposal.plan,pending.outputId):null
        return {protocol:1,controllerEpoch:controllerEpoch,ready:ready,reason:!safety.safe?safety.reason:!hasOutput && !dimmingOutput?"no-selected-output":null,armed:armed,movementArmed:guards.movement,brightnessArmed:guards.brightness,
            movementResumePending:!!guards.unlock && guards.unlock.movement,brightnessResumePending:!!guards.unlock && guards.unlock.brightness,
            movementResumeReady:safety.safe && hasOutput,brightnessResumeReady:dimmingSafety.safe && dimmingOutput,
            movementReady:safety.safe && activeOutputs("movement").length>0 && movementEnabled,
            movementActiveDisplays:activeOutputs("movement"),brightnessActiveDisplays:activeOutputs("brightness"),
            dimmingReady:dimmingSafety.safe && dimmingOutput && brightnessEnabled,restoreBrightness:observer.dimming.healthy && brightnessEnabled,
            dimming:observer.dimming.status(),
            dimDisplayOutputs:brightnessEnabled && dimmingSafety.safe?observer.dimming.manualOutputs():[],
            nextMoveCheck:movementEnabled && !busy?Scheduler.nextCheck(scheduler,schedulerNow()):null,
            busy:busy,skipAvailable:warning.activeGeneration>=0,restoreAvailable:ready && movementEnabled && !busy && !!undo,
            restoreOutputId:undo?undo.plan.outputIds[0]:null,
            warningRemainingSeconds:warning.remainingMs/1000,
            result:result,commandReport:commandReport,cancellationTrace:cancellationTrace,restorePreview:restorePreview}
    }
    function cancel(reason,disarm,preserveGuards) {
        if (disarm && !preserveGuards) guards=Guarding.createGuardingState()
        if (disarm || reason==="paused" || reason==="settings-changed") observer.dimming.clear(reason)
        if (pending && pending.warningGeneration===observer.warningSurface.activeGeneration)
            observer.warningSurface.hideForGeneration(pending.warningGeneration)
        planning.invalidate(reason)
        observer.writer.cancel(reason);observer.orderedWriter.cancel(reason)
        if (pending) complete("cancelled",reason)
        if (disarm) scheduler=Scheduler.transitionScheduler(scheduler,{type:"suspend",now:schedulerNow()}).state
    }
    function synchronize(config) {
        const key=config.revision+":"+config.controlGeneration
        if (controllerEpoch!==config.controllerEpoch) {
            controllerEpoch=config.controllerEpoch;cancel("controller-replaced",true)
            observer.writer.undoRecord=null;observer.orderedWriter.forgetUndo()
        }
        if (configKey!==key) {
            const waiting=guards.unlock && settingsRevision===config.revision && !config.settings.paused && config.settings.resumeAfterUnlock===true
            guards=Object.assign({},guards,{unlock:waiting?guards.unlock:null})
            configKey=key;cancel("settings-changed",config.settings.paused)
            guards=Object.assign({},guards,{movement:guards.movement && selected().length>0,brightness:guards.brightness && observer.dimming.selected().length>0})
            if(!waiting && (guards.movement || guards.brightness))
                guards=Guarding.start(guards,"all",guards.movement && selected().length>0,guards.brightness && observer.dimming.selected().length>0,
                    configuredOutputs(selected()),configuredOutputs(observer.dimming.selected()))
            rebuildScheduler(false)
        }
        settingsRevision=config.revision
        sessionResumeAllowed=config.settings.resumeAfterUnlock===true && config.settings.paused===false
        if (movementEnabled && scheduler.phase!=="Running") scheduler=Scheduler.transitionScheduler(scheduler,{type:"resume",now:schedulerNow()}).state
    }
    function command(command) {
        if (!command || typeof command.id!=="string" || command.id===lastCommand) return
        lastCommand=command.id
        if (["Resume","ResumeMovement","ResumeBrightness"].indexOf(command.action)>=0) {
            const scope=command.action==="ResumeMovement"?"movement":command.action==="ResumeBrightness"?"brightness":"all"
            const movementReady=facts(false,null,true).safe && selected().length>0
            const brightnessReady=dimmingFacts().safe && observer.dimming.selected().length>0
            const permitted=scope==="movement"?movementReady:scope==="brightness"?brightnessReady:movementReady || brightnessReady
            if(permitted) {
                startingUntil=planning.nativeObservation.monotonicNow()+2500
                guards=Guarding.start(guards,scope,movementReady,brightnessReady,configuredOutputs(selected()),configuredOutputs(observer.dimming.selected()))
                if(scope!=="brightness")rebuildScheduler(true)
            }
            commandReport={id:command.id,status:permitted?"completed":"rejected",reason:permitted?null:"runtime-unavailable"}
            return
        }
        if(command.action==="PauseMovement") {
            guards=Guarding.pause(guards,"movement");cancel("movement-paused",false)
            scheduler=Scheduler.transitionScheduler(scheduler,{type:"pause",now:schedulerNow()}).state
            commandReport={id:command.id,status:"completed",reason:null};return
        }
        if(command.action==="PauseBrightness") {
            guards=Guarding.pause(guards,"brightness");observer.dimming.clear("brightness-paused")
            commandReport={id:command.id,status:"completed",reason:null};return
        }
        if (command.action==="RestoreBrightness") {
            observer.dimming.restoreBrightness()
            commandReport={id:command.id,status:"completed",reason:null};return
        }
        if (command.action==="DimDisplayNow" || command.action==="DimAllDisplaysNow") {
            const ids=command.action==="DimDisplayNow"?[command.outputId]:observer.dimming.manualOutputs();
            const permitted=brightnessEnabled && dimmingFacts().safe && observer.dimming.dimNow(ids);
            commandReport={id:command.id,status:permitted?"completed":"rejected",reason:permitted?null:"dimming-unavailable"};return;
        }
        if (command.action==="Skip") {
            observer.warningSurface.onSkip()
            complete("cancelled","user-skip")
            commandReport={id:command.id,status:"completed",reason:null};return
        }
        if (!movementEnabled || (command.outputId && activeOutputs("movement").indexOf(command.outputId)<0) || busy || planning.active || planning.layouts.busy || planning.state.phase!=="Idle" || !facts(false,command.outputId?[command.outputId]:null,command.action==="MoveNow").safe) {
            commandReport={id:command.id,status:"rejected",reason:"runtime-unavailable-or-busy"}
            if (command.scheduled) scheduler=Scheduler.transitionScheduler(scheduler,{type:"skipped",now:schedulerNow(),
                outputId:command.outputId,generation:command.schedulerGeneration}).state
            return
        }
        if (command.action==="Restore" && command.outputId) {
            const undo=observer.orderedWriter.state.undoRecord || observer.writer.undoRecord
            if (!undo || undo.plan.outputIds[0]!==command.outputId) {
                commandReport={id:command.id,status:"rejected",reason:"restore-output-mismatch"};return
            }
        }
        pending=command.action==="Restore"?Object.assign({},command,{record:observer.orderedWriter.takeUndo()}):command
        if (pending.action==="Restore" && !pending.outputId && pending.record) pending=Object.assign({},pending,{outputId:pending.record.plan.outputIds[0]})
        planning.setScope(pending.outputId?[pending.outputId]:[])
        if (pending.action==="MoveNow") pending=Object.assign({},pending,{stage:"warning-pending",seed:Number.isInteger(command.seed)?command.seed:Math.floor(Math.random()*4294967296)})
        executing=false
        commandReport={id:command.id,status:"queued",reason:null}
    }
    function complete(status,reason,details) {
        if (!pending) return
        result={status:status,reason:reason || null,committedWindows:details && details.committedWindows || 0,plannedWindows:details && details.plannedWindows || 0,
            failureBoundary:details && details.failureBoundary || null,
            unverifiedWindows:details && details.unverifiedWindows || 0,
            geometryFailure:details && details.geometryFailure || null,commandId:pending.id}
        const uncertain=status==="failed-uncertain" || status==="cancelled-write-outcome-unknown"
        commandReport={id:pending.id,status:"completed",reason:reason || null}
        if (pending.scheduled) scheduler=Scheduler.transitionScheduler(scheduler,{type:"completed",now:schedulerNow(),
            outputId:pending.outputId,generation:pending.schedulerGeneration}).state
        if (!pending.scheduled && pending.action==="MoveNow" && scheduler.phase==="Running" && scheduler.intervals[pending.outputId]) {
            const deadlines=Object.assign({},scheduler.deadlines)
            deadlines[pending.outputId]=schedulerNow()+scheduler.intervals[pending.outputId]
            scheduler=Object.assign({},scheduler,{deadlines:deadlines})
        }
        pending=null;executing=false;planning.nativeObservation.graceUntilMs=0;planning.setScope([])
        // Do not schedule a new move over an unresolved physical layout, or
        // overwrite its result. Resume requires an explicit user action.
        if (uncertain) {
            guards=Guarding.pause(guards,"movement")
            scheduler=Scheduler.transitionScheduler(scheduler,{type:"suspend",now:schedulerNow()}).state
            // Brightness guarding has independent authority.
        }
    }
    function proposed() {
        if (!pending || pending.stage!=="planning" || executing || !planning.report || planning.report.id!==pending.id) return
        if (planning.report.status==="planning") return
        if (planning.report.status!=="ready") {complete(planning.report.status,planning.report.reason);return}
        const proposal=planning.proposal
        // Product movement executes after its one confirmed warning, with a
        // fresh plan. The same ordered safeguards handle one or many windows.
        if (proposal.result.plan.metadata.mode==="ordinary_shift" && !proposal.result.steps.length)
            proposal.result.steps=Steps.orderOrdinarySteps(planning.capture(),planning.registry,proposal.result.plan)
        const writer=observer.orderedWriter
        pending=Object.assign({},pending,{ordered:writer===observer.orderedWriter})
        executing=writer.begin(proposal)
        if (!executing) complete("rejected",writer.lastFailure || "writer-refused")
    }
    function movementReceipt(proposal,claim) {
        if (!pending || pending.action!=="MoveNow" || pending.stage!=="planning" ||
                !pending.receipt || !proposal || !planning.report || planning.report.id!==pending.id) return null
        const receipt=pending.receipt, now=planning.nativeObservation.monotonicNow()
        if (!receipt.hidden || now<receipt.deadline || now-receipt.deadline>2000 ||
                !planning.nativeObservation.tokenCurrent(receipt.nativeToken)) return null
        if (claim && receipt.claimed) return null
        if (claim) pending=Object.assign({},pending,{receipt:Object.assign({},receipt,{claimed:true})})
        return receipt
    }
    function startMovementWarning() {
        const output=observer.outputsNow().filter(function(o){return o.id===pending.outputId})[0]
        if (!output) {complete("rejected","warning-output-lost");return}
        const settings=planning.configuration.settings, generation=++warningSerial
        pending=Object.assign({},pending,{stage:"warning",warningGeneration:generation,issued:true})
        planning.nativeObservation.startWarningGrace(Math.min(settings.quietSeconds,settings.warningSeconds)*1000)
        observer.warningSurface.restoring=false;observer.warningSurface.windowCount=0
        observer.warningSurface.showForOutput(generation,output.usableRect,settings.warningSeconds*1000)
        planning.prepareWarning(pending.id+":warning",pending.outputId,pending.seed)
    }
    property Connections movementWarning: Connections {
        target:runtime.observer.warningSurface
        function onShown(generation) {
            if (!runtime.pending || runtime.pending.warningGeneration!==generation) return
            try {
                const native=runtime.planning.nativeObservation, settings=runtime.planning.configuration.settings
                native.startWarningGrace(Math.min(settings.quietSeconds,settings.warningSeconds)*1000)
                const clock=runtime.observer.warningSurface.clockState
                runtime.pending=Object.assign({},runtime.pending,{receipt:{startedAt:clock.lastNow,
                    deadline:clock.deadline,nativeToken:native.cancellationToken(),hidden:false}})
            } catch(error) {runtime.cancel("warning-clock-unavailable",true)}
        }
        function onExpired(generation) {
            if (!runtime.pending || runtime.pending.warningGeneration!==generation) return
            runtime.pending=Object.assign({},runtime.pending,{warningExpired:true})
            runtime.observer.warningSurface.hideForGeneration(generation)
        }
        function onHidden(generation) {
            if (!runtime.pending || runtime.pending.warningGeneration!==generation) return
            if (!runtime.pending.warningExpired || !runtime.pending.receipt) {runtime.cancel("warning-unconfirmed",false);return}
            runtime.pending=Object.assign({},runtime.pending,{stage:"planning",issued:false,
                receipt:Object.assign({},runtime.pending.receipt,{hidden:true})})
        }
        function onSkipped(generation) {if(runtime.pending && runtime.pending.warningGeneration===generation)runtime.cancel("user-skip",false)}
        function onFailed(generation,reason) {if(runtime.pending && runtime.pending.warningGeneration===generation)runtime.cancel(reason,false)}
    }
    function tick() {
        if (!planning.configuration || Date.now()-planning.lastContact>=500) {
            // Cold renderer creation can delay the first companion exchange.
            // Wait briefly during arming only; no writer can pass its existing
            // 500 ms authority check, and native session/input loss still wins.
            if (armed && !executing && planning.nativeObservation.immediateCancellation && planning.nativeObservation.monotonicNow()<startingUntil) return;
            if(guards.unlock && waitingForSessionContact())return;
            if(armed || pending || guards.unlock) {
                console.warn("OWG_COMPANION_AUTHORITY_EXPIRED","ageMs",Date.now()-planning.lastContact,
                    "pending",!!pending,"executing",executing,"configuration",!!planning.configuration)
                cancel("companion-unavailable",true)
            }
            return;
        }
        if(guards.unlock) {
            const before=armed,beforeMovement=JSON.stringify(activeOutputs("movement")), movement=resumeScopes("movement"),brightness=resumeScopes("brightness")
            guards=Guarding.afterUnlock(guards,planning.nativeObservation.localSnapshot(),
                movement.length>0 && facts(false,movement.map(function(o){return o.id}),false).safe,
                brightness.length>0 && facts(false,brightness.map(function(o){return o.id}),false).safe,
                sessionResumeAllowed,{movement:movement,brightness:brightness})
            if(!before && armed)startingUntil=planning.nativeObservation.monotonicNow()+2500
            if(beforeMovement!==JSON.stringify(activeOutputs("movement")))rebuildScheduler(true)
            if(!guards.unlock)resumeContactUntil=0
        }
        if (pending) {
            if (!movementEnabled) {cancel("paused",false);return}
            if (executing) {
                if (!observer.writer.busy && !observer.orderedWriter.busy) {
                    const last=observer.orderedWriter.state.lastResult
                    const single=observer.writer.state.lastResult
                    const chosen=pending.action==="Restore" || pending.ordered?last:single
                    complete(chosen?chosen.status:"rejected",chosen?chosen.reason:observer.orderedWriter.lastFailure,chosen)
                }
                return
            }
            if (pending.stage==="warning") {
                if (planning.report && planning.report.purpose==="warning-preview" && planning.report.status==="ready") observer.warningSurface.windowCount=planning.report.moves;
                if (!planning.nativeObservation.inWarningGrace()) {
                    if (!facts(false).safe) {cancel("held-input-after-grace",false);return;}
                    if (!pending.graceChecked && planning.prepareWarning(pending.id+":grace",pending.outputId,pending.seed))
                        pending=Object.assign({},pending,{graceChecked:true});
                }
                return
            }
            if (pending.stage==="warning-pending") {
                if (!facts(false,null,true).safe) return
                try {
                    if(!planning.hasEligibleWindows(pending.outputId)){complete("skipped","no-eligible-windows");return;}
                    startMovementWarning()
                } catch(error) {cancel("warning-unavailable",true)}
                return
            }
            if (!facts(false).safe) return
            if (pending.action==="Restore") {
                executing=observer.orderedWriter.restoreConsumed(pending.record)
                if (!executing) complete("restore-skipped",observer.orderedWriter.lastFailure)
            } else if (!pending.issued) {
                if (planning.active || planning.state.occupied || planning.layouts.busy) return;
                if (planning.reusePrepared(pending.id,pending.outputId,"movement")) {
                    pending=Object.assign({},pending,{issued:true});proposed();
                } else {
                    // Never substitute unseen destinations after grace.
                    const report=planning.report;
                    if (report && report.purpose==="warning-preview" && report.status==="no-plan") complete("no-plan",report.reason);
                    else complete("cancelled","reviewed-plan-changed");
                }
            } else proposed()
            return
        }
        if (movementEnabled && facts(true,null,true).safe && !observer.writer.busy && !observer.orderedWriter.busy && !planning.active &&
                !planning.layouts.busy && planning.state.phase==="Idle") {
            if (scheduler.phase!=="Running") scheduler=Scheduler.transitionScheduler(scheduler,{type:"resume",now:schedulerNow()}).state
            const next=Scheduler.transitionScheduler(scheduler,{type:"due",now:schedulerNow()});scheduler=next.state
            if (next.commands.length) {
                const job=next.commands[0]
                command({id:"scheduled:"+(++scheduledSerial)+":"+job.generation,action:"MoveNow",outputId:job.outputId,scheduled:true,schedulerGeneration:job.generation})
            }
        }
    }
    property Connections nativeEvents: Connections {
        target: runtime.planning.nativeObservation
        function onCanceled(reason) {
            if (runtime.armed || runtime.pending || runtime.guards.unlock || reason==="native-topology") {
                try {
                const sample=runtime.planning.nativeObservation.immediateCancellation?
                    runtime.planning.nativeObservation.localSnapshot():null
                runtime.cancellationTrace=runtime.cancellationTrace.concat([{
                    reason:reason,at:Date.now(),armed:runtime.armed,
                    commandId:runtime.pending?runtime.pending.id:null,
                    warningActive:runtime.observer.warningSurface.activeGeneration>=0,
                    epoch:sample?sample.epoch:null,
                    generation:sample?sample.cancellationGeneration:null,
                    topologyGeneration:sample?sample.topologyGeneration:null,
                    sequence:sample?sample.sequence:null,
                    pointer:sample?sample.pointer:null,button:sample?sample.button:null,
                    axis:sample?sample.axis:null,keyboard:sample?sample.keyboard:null
                }]).slice(-16)
                } catch(error) { /* Diagnostics must never prevent cancellation. */ }
            }
            if(reason==="native-topology") {
                try {runtime.topologyChanged()}catch(error){runtime.cancel("native-topology-unavailable",true)}
            } else if (reason!=="native-input" && reason!=="native-focus") {
                let sample=null
                try {sample=runtime.planning.nativeObservation.localSnapshot()}catch(error){}
                if(Guarding.dormant(sample))runtime.resumeContactUntil=0
                runtime.guards=Guarding.boundary(runtime.guards,reason,sample,
                    runtime.sessionResumeAllowed)
                runtime.cancel(reason,true,true)
            }
            else if (runtime.pending && (runtime.pending.action==="Restore" || runtime.pending.issued || runtime.executing)) runtime.cancel(reason,false)
        }
    }
    property Timer timer: Timer {interval:100;repeat:true;running:true;onTriggered:runtime.tick()}
}

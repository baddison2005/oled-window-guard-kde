import QtQuick
import QtQml.WorkerScript
import "core/model.js" as Model
import "core/registry.js" as Registry
import "core/planner.js" as Planner
import "core/swap_steps.js" as Steps
import "core/planning_dispatch.js" as Dispatch
import "core/groups.js" as Groups
import "core/group_safety.js" as GroupSafety

QtObject {
    id: probe
    property var state: Dispatch.createPlanningState()
    property var workerObject: null
    property int epochCount: 0
    readonly property string instanceEpoch: String(Date.now())+":"+String(Math.random())
    property bool readyNotified: false
    property var fixtures: []
    property var samples: []
    property var workerControls: []
    property var baseline: []
    property var heartbeatGaps: []
    property double lastTick: 0
    property int heartbeat: 0
    property int warmup: 0
    property int runIndex: 0
    property int controlIndex: 0
    property var active: null
    property var finishing: null
    property var savedReply: null
    property var controls: []
    property bool replacementPending: false
    property string failure: ""
    property Component workerFactory: Component {
        WorkerScript { onMessage: (messageObject)=> probe.receive(messageObject) }
    }
    function require(condition,message) { if (!condition) throw new Error(message) }
    function send(event) { const r=Dispatch.transitionPlanning(state,event);state=r.state;return r.commands }
    function createWorker() {
        if (workerObject) workerObject.destroy()
        workerObject=workerFactory.createObject(probe,{source:Qt.resolvedUrl("worker.js")})
        epochCount++;readyNotified=false;replacementPending=false
    }
    function fixture(count, obstacleCount) {
        const area = {x:0, y:0, width:1920, height:1080}
        const windows = []
        for (let i=0; i<count+obstacleCount; i++) windows.push({
            identity:String(i), appId:"planner-fixture", outputIds:["DP-1"],
            rect:{x:(i%17)*110, y:Math.floor(i/17)*65, width:100, height:60},
            visible:true, onCurrentDesktop:true, onCurrentActivity:true, focused:false,
            managed:true, modal:false, transient:false, movable:i<count, resizable:true,
            minimized:false, maximized:false, fullscreen:false, tiled:false,
            windowType:"normal", skipReasons:[]
        })
        const snapshot = {outputs:[{id:"DP-1",connector:"DP-1",edid:"fixture",name:"fixture",
            geometry:area,usableRect:area,scale:1}],windows:windows,focusedIdentity:null,topologyRevision:"bench"}
        const registry = Registry.reconcile(snapshot,Registry.createRegistryState("bench"),
            {sessionEpoch:"bench",selectedOutputIds:["DP-1"]})
        return {snapshot:snapshot,registry:registry,count:count,obstacles:obstacleCount}
    }
    function contextFor(f) {
        return {sessionEpoch:f.registry.sessionEpoch,settingsRevision:"s1",topologyRevision:f.snapshot.topologyRevision,
            snapshotSignature:Registry.snapshotSignature(f.snapshot),libraryRevision:null}
    }
    function optionsFor(f) {
        return {outputId:"DP-1",maximumWindows:f.count,shiftRangePercent:100,sizeTolerance:.1,
            allowTransientOverlap:false,keepSimilarWindowsTogether:false,seed:7}
    }
    function issue(mode,f,control) {
        const context=contextFor(f),options=optionsFor(f)
        if (control && control.kind==="deadline") options.deadlineMs=0
        if (control && control.kind==="budget") options.maxEvaluations=1
        if (mode==="group") options.rotateGroup=control.rotate
        if (mode==="coordinated") {options.shiftHorizontalOrder=true;options.shiftVerticalOrder=false}
        const job={mode:control && control.kind==="error"?"bad-mode":mode,
            snapshot:f.snapshot,registry:f.registry,options:options}
        if (mode==="group") job.group=f.group
        const start=Date.now(),commands=send({type:"request",now:start,timeoutMs:control && control.kind==="timeout"?50:2000,
            context:context,job:job})
        require(commands.length===1,"missing worker request")
        const command=commands[0]
        active={mode:mode,candidates:f.count,obstacles:f.obstacles,run:runIndex%30,context:context,
            fixture:f,start:start,token:command.token,control:control || null,heartbeatAtStart:heartbeat,maxTimerGapMs:0}
        if (control && ["invalidate","context","timeout"].indexOf(control.kind)>=0) command.probeDelayMs=200
        workerObject.sendMessage(command)
        if (control && control.kind==="transport-copy") {
            // Qt must already have copied the data. Mutate the caller-owned
            // frame briefly without changing the message or stored fixture.
            const old=f.snapshot.windows[0].rect.x
            active.transportMutatedX=old
            f.snapshot.windows[0].rect.x=old+123
        }
        active.dispatchMs=Date.now()-start
        if (control && control.kind==="invalidate") {
            send({type:"invalidate",reason:control.reason})
            require(state.phase==="Draining" && !state.pending,"cancellation did not invalidate")
            require(send({type:"request",now:Date.now(),timeoutMs:1000,context:context,job:job}).length===0,"queued behind invalid work")
        }
        if (control && control.kind==="context") active.context=Object.assign({},context,{settingsRevision:"s2"})
        if (savedReply && control && control.kind==="duplicate") {
            const before=JSON.stringify(state)
            require(send(Object.assign({},savedReply,{type:"reply",now:Date.now(),currentContext:context})).length===0,"stale worker reply emitted a plan")
            require(JSON.stringify(state)===before,"stale reply modified fresh worker slot")
        }
    }
    function receive(reply) {
        try {
            require(!!active,"unsolicited worker reply")
            if (active.control && active.control.kind==="transport-copy")
                active.fixture.snapshot.windows[0].rect.x=active.transportMutatedX
            const before=Date.now(),commands=send(Object.assign({},reply,{type:"reply",now:before,currentContext:active.context}))
            const control=active.control
            if (!control || ["duplicate","deadline","budget","mode","transport-copy","capacity"].indexOf(control.kind)>=0) {
                require(reply.ok && commands.length===1 && commands[0].type==="planningReady","fresh worker result rejected: "+reply.error)
                const result=commands[0].result,f=active.fixture
                if (result.plan && result.plan.moves.length) {
                    const checked=active.mode==="shift" || result.plan.metadata.mode==="ordinary_shift"?Planner.validateFinalPlan(f.snapshot,f.registry,result.plan):
                        active.mode==="group"?GroupSafety.validateGroupFinalPlan(f.snapshot,f.registry,result.plan,f.group):
                        active.mode==="coordinated"?Planner.validateCoordinatedFinalPlan(f.snapshot,f.registry,result.plan):Planner.validateSwapFinalPlan(f.snapshot,f.registry,result.plan)
                    require(checked.valid,"unsafe worker final plan")
                    if (result.steps.length) require(Steps.validateSwapSteps(f.snapshot,f.registry,result.plan,result.steps,{group:f.group}).valid,"unsafe worker steps")
                }
                if (control && ["deadline","budget"].indexOf(control.kind)>=0)
                    require(!result.plan && !result.steps.length,"bounded worker request leaked a plan")
                active.durationMs=reply.elapsedMs;active.roundTripMs=before-active.start
                active.moves=result.plan?result.plan.moves.length:0;active.complete=!!result.stats.complete
                active.stats=result.stats;active.attempts=result.attempts || []
            } else {
                require(commands.length===0,"invalidated result emitted a plan")
                if (control.kind==="timeout") require(state.phase==="Unavailable","timeout did not require worker replacement")
                if (control.kind==="error") require(!reply.ok && state.phase==="Unavailable","worker failure did not fail closed")
                if (["invalidate","context"].indexOf(control.kind)>=0) {
                    require(state.phase==="Idle","stale transport slot did not drain")
                    require(heartbeat-active.heartbeatAtStart>=5,"worker blocked compositor timer during delayed computation")
                }
                active.roundTripMs=before-active.start
            }
            active.acceptanceMs=Date.now()-before
            savedReply=reply
            finishing=active;active=null
            if (control && ["timeout","error"].indexOf(control.kind)>=0) replacementPending=true
        } catch(error) { fail(error) }
    }
    function fail(error) { failure=String(error);clock.stop();console.warn("OWG_PLANNER_FAIL "+failure) }
    function tick() {
        try {
            const stamp=Date.now(),gap=lastTick?stamp-lastTick:0
            if (lastTick) {
                heartbeatGaps.push(gap)
                if (active) active.maxTimerGapMs=Math.max(active.maxTimerGapMs,gap)
                if (finishing) {
                    finishing.maxTimerGapMs=Math.max(finishing.maxTimerGapMs,gap)
                    const s=finishing
                    if (s.control) workerControls.push({kind:s.control.kind,reason:s.control.reason || null,
                        mode:s.mode,roundTripMs:s.roundTripMs,heartbeats:heartbeat-s.heartbeatAtStart,
                        maxTimerGapMs:s.maxTimerGapMs,dispatchMs:s.dispatchMs,acceptanceMs:s.acceptanceMs})
                    else samples.push({mode:s.mode,candidates:s.candidates,obstacles:s.obstacles,run:s.run,
                        durationMs:s.durationMs,roundTripMs:s.roundTripMs,moves:s.moves,complete:s.complete,
                        stats:s.stats,attempts:s.attempts,followingTimerGapMs:s.maxTimerGapMs,
                        dispatchMs:s.dispatchMs,acceptanceMs:s.acceptanceMs})
                    finishing=null
                } else if (!active) baseline.push(gap)
            }
            lastTick=stamp;heartbeat++
            if (active && active.control && active.control.kind==="timeout" && state.occupied && stamp>=state.occupied.deadline) {
                send({type:"timeout",token:active.token,now:stamp})
                require(state.phase==="Unavailable","worker timeout not observed")
            }
            if (active || finishing) return
            if (replacementPending) createWorker()
            if (!workerObject || !workerObject.ready) return
            if (!readyNotified) {
                send({type:"workerReady",workerEpoch:instanceEpoch+"/worker-"+epochCount})
                require(state.phase==="Idle","worker readiness failed");readyNotified=true
            }
            if (warmup<15) {warmup++;return}
            if (runIndex<120) {
                const scenario=Math.floor(runIndex/30),f=fixtures[scenario%2],mode=scenario<2?"shift":"swap"
                issue(mode,f,null);runIndex++;return
            }
            if (controlIndex<controls.length) {
                const control=controls[controlIndex++],f=control.mode==="group"?fixtures[2]:control.kind==="capacity"?fixtures[3]:fixtures[control.kind==="deadline" || control.kind==="budget"?1:0]
                issue(control.mode || "swap",f,control);return
            }
            require(samples.length===120 && workerControls.length===25,"missing worker measurements/controls")
            require(samples.every(function(s){return s.followingTimerGapMs<=32}) &&
                workerControls.every(function(s){return s.maxTimerGapMs<=32}),"threaded workload exceeded two 16ms timer intervals")
            require(!state.pending && !state.occupied,"worker request survived completion")
            send({type:"close"});require(state.phase==="Closed","worker close failed")
            clock.stop()
            console.warn("OWG_PLANNER_RESULT "+JSON.stringify({samples:samples,workerControls:workerControls,
                baselineTimerGapsMs:baseline,heartbeatGapsMs:heartbeatGaps,controls:25,timerIntervalMs:16,workloadTimerBudgetMs:32,
                workerInstances:epochCount,scope:"private virtual KWin; Qt WorkerScript; fixture-only planning"}))
        } catch(error) {fail(error)}
    }
    property Timer clock: Timer {interval:16;repeat:true;running:false;onTriggered:probe.tick()}
    Component.onCompleted: {
        const grouped=fixture(4,0)
        grouped.group=Groups.builtinGroups(0)[3]
        grouped.snapshot.windows.forEach(function(w,i){w.rect={x:i%2?1100:100,y:i>=2?700:100,width:100,height:100}})
        grouped.registry=Registry.reconcile(grouped.snapshot,Registry.createRegistryState("bench"),
            {sessionEpoch:"bench",selectedOutputIds:["DP-1"]})
        fixtures=[fixture(4,24),fixture(12,240),grouped,fixture(12,244)]
        controls=["pause","input","locked","sleep","inactive","topology","settings","library","focus","geometry","companion","kwin-reload"].map(function(reason){return {kind:"invalidate",reason:reason}})
        controls.push({kind:"context"},{kind:"timeout"},{kind:"error"},{kind:"duplicate"})
        for (const mode of ["shift","swap"]) for (const kind of ["deadline","budget"]) controls.push({kind:kind,mode:mode})
        controls.push({kind:"mode",mode:"group",rotate:false},{kind:"mode",mode:"group",rotate:true},
            {kind:"mode",mode:"coordinated"},{kind:"transport-copy"},{kind:"capacity"})
        createWorker();clock.start()
    }
}

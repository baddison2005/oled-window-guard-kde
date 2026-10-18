import QtQuick
import "core/model.js" as Model
import "core/registry.js" as Registry
import "core/planner.js" as Planner
import "core/swap.js" as Swap

QtObject {
    id: probe
    property int index: 0
    property double lastTick: 0
    property var pending: null
    property var samples: []
    property var baseline: []
    property var fixtures: []
    property string failure: ""

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
    function options(f) {
        return {outputId:"DP-1",maximumWindows:f.count,shiftRangePercent:100,sizeTolerance:.1,
            allowTransientOverlap:false,keepSimilarWindowsTogether:false,
            random:function(){return .37},now:function(){return Date.now()}}
    }
    function require(condition, message) { if (!condition) throw new Error(message) }
    function controls() {
        const f = fixtures[1]
        for (const mode of ["shift", "swap"]) for (const control of ["cancel", "deadline", "budget"]) {
            const o = options(f)
            if (control === "cancel") o.shouldCancel = function(){return true}
            if (control === "deadline") o.deadlineMs = 0
            if (control === "budget") o.maxEvaluations = 1
            const r = mode === "shift" ? Planner.planOrdinaryShift(f.snapshot,f.registry,o) : Swap.planSwap(f.snapshot,f.registry,o)
            require(!r.plan || r.plan.moves.length === 0,mode+" "+control+" leaked moves")
            require(!r.steps || r.steps.length === 0,mode+" "+control+" leaked steps")
            console.warn("OWG_PLANNER_CONTROL " + JSON.stringify({mode:mode,control:control,stats:r.stats,attempts:r.attempts || []}))
        }
    }
    function tick() {
        try {
            const stamp = Date.now()
            if (lastTick) {
                const gap = stamp-lastTick
                if (pending) { pending.followingTimerGapMs=gap; samples.push(pending); pending=null }
                else baseline.push(gap)
            }
            lastTick=stamp
            if (index<15) { index++; return }
            const run = index-15
            if (run >= 120) {
                clock.stop()
                require(samples.length === 120,"missing timed samples")
                controls()
                console.warn("OWG_PLANNER_RESULT " + JSON.stringify({samples:samples,baselineTimerGapsMs:baseline,
                    controls:6,timerIntervalMs:16,scope:"private virtual KWin; pure fixtures; synchronous planning"}))
                return
            }
            const scenario=Math.floor(run/30), f=fixtures[scenario%2], mode=scenario<2?"shift":"swap"
            const start=Date.now()
            const r=mode==="shift"?Planner.planOrdinaryShift(f.snapshot,f.registry,options(f)):Swap.planSwap(f.snapshot,f.registry,options(f))
            const end=Date.now()
            require(r.stats.complete || !r.plan || r.plan.moves.length===0,"incomplete plan leaked moves")
            const checked=r.plan && r.plan.moves.length?(mode==="shift"?Planner.validateFinalPlan(f.snapshot,f.registry,r.plan):Planner.validateSwapFinalPlan(f.snapshot,f.registry,r.plan)):null
            require(!checked || checked.valid,"unsafe final plan: "+JSON.stringify(checked))
            pending={mode:mode,candidates:f.count,obstacles:f.obstacles,run:run%30,
                durationMs:end-start,moves:r.plan?r.plan.moves.length:0,complete:!!r.stats.complete,
                stats:r.stats,attempts:r.attempts || []}
            index++
        } catch(error) {
            clock.stop(); failure=String(error); console.warn("OWG_PLANNER_FAIL "+failure)
        }
    }
    property Timer clock: Timer { interval:16; repeat:true; running:false; onTriggered:probe.tick() }
    Component.onCompleted: { fixtures=[fixture(4,24),fixture(12,240)]; clock.start() }
}

import QtQuick
import QtQml.WorkerScript
import "core/planning_dispatch.js" as Dispatch
import "core/registry.js" as Registry

QtObject {
    id: probe
    readonly property string epoch: String(Date.now())+":"+String(Math.random())
    property var state: Dispatch.createPlanningState()
    property bool started: false
    property var current: null
    property WorkerScript worker: WorkerScript {
        source: "worker.js"
        onMessage: function(reply) {
            const out=Dispatch.transitionPlanning(probe.state,Object.assign({},reply,
                {type:"reply",now:Date.now(),currentContext:probe.current}))
            probe.state=out.state
            if (!reply.ok || out.commands.length!==1 || out.commands[0].type!=="planningReady") {
                console.warn("OWG_PLANNER_FAIL worker lifetime result was not fresh");return
            }
            console.warn("OWG_WORKER_LIFETIME_REPLY "+JSON.stringify({epoch:probe.epoch,token:reply.token}))
        }
    }
    function start() {
        if (started || !worker.ready) return
        started=true
        const area={x:0,y:0,width:300,height:200}
        const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"e",name:"fixture",geometry:area,usableRect:area,scale:1}],
            windows:[0,1].map(function(i){return {identity:String(i),appId:"fixture",outputIds:["DP-1"],
                rect:{x:i*100,y:0,width:100,height:100},visible:true,onCurrentDesktop:true,onCurrentActivity:true,
                focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,
                maximized:false,fullscreen:false,tiled:false,windowType:"normal",skipReasons:[]}}),
            focusedIdentity:null,topologyRevision:"life"}
        const registry=Registry.reconcile(snapshot,Registry.createRegistryState(epoch),{sessionEpoch:epoch,selectedOutputIds:["DP-1"]})
        current={sessionEpoch:epoch,settingsRevision:"s1",snapshotSignature:Registry.snapshotSignature(snapshot),
            topologyRevision:"life",libraryRevision:null}
        state=Dispatch.transitionPlanning(state,{type:"workerReady",workerEpoch:epoch}).state
        const out=Dispatch.transitionPlanning(state,{type:"request",now:Date.now(),timeoutMs:2000,context:current,
            job:{mode:"swap",snapshot:snapshot,registry:registry,options:{outputId:"DP-1",maximumWindows:2,sizeTolerance:.1,
                keepSimilarWindowsTogether:false,allowTransientOverlap:true,seed:7}}})
        state=out.state
        const command=out.commands[0];command.probeDelayMs=500
        worker.sendMessage(command)
        console.warn("OWG_WORKER_LIFETIME_START "+JSON.stringify({epoch:epoch,token:command.token}))
        clock.stop()
    }
    property Timer clock: Timer {interval:16;running:true;repeat:true;onTriggered:probe.start()}
}

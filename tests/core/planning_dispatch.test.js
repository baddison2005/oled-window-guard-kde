"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),os=require("node:os"),path=require("node:path"),cp=require("node:child_process");
const directory=fs.mkdtempSync(path.join(os.tmpdir(),"owg-worker-test-"));
try {
    const bundle=path.join(directory,"worker.js");
    cp.execFileSync("python3",["tools/build_planning_worker.py",bundle]);
    let message;
    const context={WorkerScript:{sendMessage:value=>{message=value}},Date:{now:()=>0}};
    vm.runInNewContext(fs.readFileSync(bundle,"utf8"),context);
    const modules=context.OWGModules,D=modules["planning_dispatch.js"],M=modules["model.js"],R=modules["registry.js"];
    const copy=value=>JSON.parse(JSON.stringify(value));
    const frame=(x)=>({x,y:0,width:100,height:100}),area={x:0,y:0,width:300,height:200};
    const snapshot={outputs:[{id:"DP-1",connector:"DP-1",edid:"fixture",name:"fixture",geometry:area,usableRect:area,scale:1}],
        windows:[0,1].map(i=>({identity:String(i),appId:"app",outputIds:["DP-1"],rect:frame(i*100),
            visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,
            transient:false,movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,
            tiled:false,windowType:"normal",skipReasons:[]})),focusedIdentity:null,topologyRevision:"topo"};
    const registry=R.reconcile(snapshot,R.createRegistryState("session-1"),{sessionEpoch:"session-1",selectedOutputIds:["DP-1"]});
    const current={sessionEpoch:"session-1",settingsRevision:"s1",snapshotSignature:R.snapshotSignature(snapshot),topologyRevision:"topo",libraryRevision:null};
    const job={mode:"swap",snapshot,registry,options:{outputId:"DP-1",maximumWindows:2,sizeTolerance:.1,
        allowTransientOverlap:false,keepSimilarWindowsTogether:false,seed:7,deadlineMs:100,maxEvaluations:5000}};
    // Both plain drift and the coordinated fallback must deliver executable
    // two-window steps, rather than entering the single-window writer.
    for (const mode of ['shift','coordinated']) {
        const sample=copy(snapshot);sample.outputs[0].geometry=sample.outputs[0].usableRect={x:0,y:0,width:800,height:600};
        sample.windows[1].rect.x=400;
        sample.focusedIdentity=sample.windows[0].identity;sample.windows[0].focused=true;
        const reg=R.reconcile(sample,R.createRegistryState('multi'),{sessionEpoch:'multi',selectedOutputIds:['DP-1'],allowIdleFocused:true});
        const ctx={...current,sessionEpoch:'multi',snapshotSignature:R.snapshotSignature(sample)};
        const work={mode,snapshot:sample,registry:reg,options:{outputId:'DP-1',maximumWindows:2,shiftRangePercent:30,
            shiftHorizontalOrder:false,shiftVerticalOrder:false,seed:7,deadlineMs:100,maxEvaluations:5000}};
        let st=D.transitionPlanning(D.createPlanningState(),{type:'workerReady',workerEpoch:'multi'}).state;
        const command=D.transitionPlanning(st,{type:'request',now:0,timeoutMs:1000,context:ctx,job:work}).commands[0];
        context.WorkerScript.onMessage(command);assert.equal(message.ok,true,message.error);
        assert.equal(message.result.plan.metadata.mode,'ordinary_shift');
        assert.equal(message.result.plan.moves.length,2);assert.equal(message.result.steps.length,2);
        assert.equal(modules['swap_steps.js'].validateSwapSteps(sample,reg,message.result.plan,message.result.steps).valid,true);
        const unsafe=copy(message.result.plan);unsafe.metadata.allowTransientOverlap=true;
        assert.equal(modules['swap_steps.js'].validateSwapSteps(sample,reg,unsafe,message.result.steps).valid,false);
        const extra=copy(message.result.steps);extra[0].kind='stage';
        assert.equal(modules['swap_steps.js'].validateSwapSteps(sample,reg,message.result.plan,extra).valid,false);

    }
    let cases=0;
    function driver() {
        let state=D.createPlanningState();
        const send=e=>{const r=D.transitionPlanning(state,e);state=r.state;return r.commands};
        send({type:"workerReady",workerEpoch:"worker-1"});
        const request=()=>send({type:"request",now:0,timeoutMs:1000,context:current,job});
        const command=request()[0];assert.ok(command);
        context.WorkerScript.onMessage(command);assert.equal(message.ok,true,message.error);
        const reply=Object.assign(copy(message),{type:"reply",currentContext:copy(current),now:10});
        return {send,request,command,reply,get state(){return state}};
    }
    {
        const d=driver(),original=copy(job),commands=d.send(d.reply);
        assert.equal(commands[0].type,"planningReady");assert.equal(d.state.phase,"Idle");
        assert.equal(commands[0].result.plan.moves.length,2);
        assert.equal(modules["swap_steps.js"].validateSwapSteps(snapshot,registry,commands[0].result.plan,commands[0].result.steps).valid,true);
        assert.deepEqual(copy(job),original);
        const state=copy(d.state);assert.equal(d.send(d.reply).length,0);assert.deepEqual(copy(d.state),state);
        cases++;
    }
    for (const reason of ["pause","input","locked","sleep","inactive","topology","settings","library","focus","geometry","companion","kwin-reload"]) {
        const d=driver();d.send({type:"invalidate",reason});
        assert.equal(d.state.phase,"Draining");assert.equal(d.state.pending,null);
        assert.equal(d.request().length,0,"no queue while draining");
        assert.equal(d.send(d.reply).length,0);assert.equal(d.state.phase,"Idle");
        const next=d.request()[0];assert.ok(next);assert.notEqual(next.token.id,d.command.token.id);
        const state=copy(d.state);assert.equal(d.send(d.reply).length,0);assert.deepEqual(copy(d.state),state);
        cases++;
    }
    for (const key of Object.keys(current)) {
        const d=driver();d.reply.currentContext[key]="changed";
        assert.equal(d.send(d.reply).length,0);assert.equal(d.state.lastResult.reason,"stale-planning-result");cases++;
    }
    for (const key of ["workerEpoch","id","generation"]) {
        const d=driver(),state=copy(d.state);d.reply.token[key]="wrong";
        assert.equal(d.send(d.reply).length,0);assert.deepEqual(copy(d.state),state);cases++;
    }
    for (const failure of ["workerFailed","timeout","error","malformed","lateReply"]) {
        const d=driver();
        if (failure==="workerFailed")d.send({type:failure,workerEpoch:"worker-1"});
        else if (failure==="timeout") {
            assert.equal(d.send({type:"timeout",token:d.command.token,now:999}).length,0);
            assert.equal(d.state.phase,"Busy");d.send({type:"timeout",token:d.command.token,now:1000});
        } else {
            const reply=copy(d.reply);
            if (failure==="error")reply.ok=false;
            if (failure==="malformed")reply.result.steps=null;
            if (failure==="lateReply")reply.now=1000;
            assert.equal(d.send(reply).length,0);
        }
        assert.equal(d.state.phase,"Unavailable");assert.equal(d.request().length,0);
        d.send({type:"workerReady",workerEpoch:"worker-1"});assert.equal(d.state.phase,"Unavailable");
        d.send({type:"workerReady",workerEpoch:"worker-2"});const next=d.request()[0];assert.ok(next);
        const state=copy(d.state);assert.equal(d.send(d.reply).length,0);assert.deepEqual(copy(d.state),state);cases++;
    }
    {
        const d=driver();d.send({type:"close"});const state=copy(d.state);
        for (const e of [d.reply,{type:"workerReady",workerEpoch:"worker-2"},{type:"invalidate",reason:"wake"}]) {
            assert.equal(d.send(e).length,0);assert.deepEqual(copy(d.state),state);
        }
        cases++;
    }
    for (const mutation of [
        j=>{j.mode="unknown"},j=>{j.options.deadlineMs=101},j=>{j.options.maxEvaluations=50001},
        j=>{j.options.seed=NaN},j=>{j.snapshot.windows=Array(513).fill(j.snapshot.windows[0])},
        j=>{j.snapshot.windows[0].rect.x+=1},j=>{j.registry.sessionEpoch="new-session"}
    ]) {
        const d=driver(),bad=copy(d.command);mutation(bad.job);
        context.WorkerScript.onMessage(bad);assert.equal(message.ok,false);cases++;
    }
    // UI settings and single-window physical checks can retain order-change
    // flags while maximumWindows is one. That is a valid fallback, not a worker failure.
    for (const flags of [[true,false],[false,true],[true,true]]) {
        const d=driver(),request=copy(d.command);request.job.mode="coordinated";
        Object.assign(request.job.options,{maximumWindows:1,shiftRangePercent:100,
            shiftHorizontalOrder:flags[0],shiftVerticalOrder:flags[1]});
        context.WorkerScript.onMessage(request);
        assert.equal(message.ok,true,message.error);
        assert.ok(message.result.plan);
        assert.equal(message.result.plan.metadata.mode,"ordinary_shift");
        assert.equal(message.result.plan.moves.length,1);
        assert.equal(message.result.steps.length,0);
        assert.equal(modules["planner.js"].validateFinalPlan(snapshot,registry,message.result.plan).valid,true);
        const ready=d.send(Object.assign(copy(message),{type:"reply",currentContext:copy(current),now:10}));
        assert.equal(ready.length,1);assert.equal(d.state.phase,"Idle");cases++;
    }
    for (const mode of ["shift","coordinated","group"]) {
        const d=driver(),request=copy(d.command);request.job.mode=mode;
        if (mode==="shift")request.job.options.shiftRangePercent=100;
        if (mode==="coordinated")Object.assign(request.job.options,{shiftRangePercent:100,shiftHorizontalOrder:true,shiftVerticalOrder:false});
        if (mode==="group") {
            request.job.group=modules["groups.js"].builtinGroups(0)[1];
            Object.assign(request.job.options,{rotateGroup:false,shiftRangePercent:100});
        }
        context.WorkerScript.onMessage(request);assert.equal(message.ok,true,message.error);cases++;
    }
    {
        const library={sourceId:"kde:window-layouts",status:"available",executionReady:true,revision:"library-1",padding:0,
            layouts:[{id:"left",name:"Left",x:0,y:0,width:1/3,height:.5,groupId:"pair"},
                {id:"middle",name:"Middle",x:1/3,y:0,width:1/3,height:.5,groupId:"pair"}],
            groups:[{id:"pair",name:"Pair",layoutIds:["left","middle"]}]};
        const group=modules["groups.js"].groupsFromLibrary(library)[0];
        for (const variation of ["fresh","context-changed","library-changed","unavailable-library"]) {
            const d=driver(),request=copy(d.command);request.job.mode="group";
            request.job.group=copy(group);request.job.library=copy(library);
            request.token.context.libraryRevision=library.revision;
            Object.assign(request.job.options,{rotateGroup:true,allowTransientOverlap:true});
            if (variation==="context-changed")request.token.context.libraryRevision="library-2";
            if (variation==="library-changed")request.job.library.revision="library-2";
            if (variation==="unavailable-library")request.job.library.executionReady=false;
            context.WorkerScript.onMessage(request);
            if (variation==="fresh") {
                assert.equal(message.ok,true,message.error);assert.equal(message.result.plan.moves.length,2);
                assert.equal(message.result.plan.metadata.libraryRevision,library.revision);
                assert.equal(D.validPlanningResult(message.result,request.token.context),true);
            } else assert.ok(message.ok===false || message.result.plan===null,"stale provider cannot return executable plan");
            cases++;
        }
    }
    for (const mutate of [
        j=>{j.snapshot.windows=Array(257).fill(j.snapshot.windows[0])},
        j=>{j.registry.windows=Array(257).fill(j.registry.windows[0])},
        j=>{j.snapshot.outputs=Array(17).fill(j.snapshot.outputs[0])},
        j=>{j.registry.outputs=Array(17).fill(j.registry.outputs[0])}
    ]) {
        const d=driver();d.send(d.reply);const large=copy(job);mutate(large);
        assert.equal(d.send({type:"request",now:20,timeoutMs:1000,context:current,job:large}).length,0);
        assert.equal(d.state.lastResult.reason,"planning-input-limit");assert.equal(d.state.occupied,null);cases++;
    }
    for (const mutate of [
        r=>{r.plan.snapshotRevision="wrong-topology"},r=>{r.plan.metadata.libraryRevision="wrong-library"},
        r=>{r.steps=Array(37).fill(r.steps[0])},r=>{r.steps[0].identity="foreign-window"},
        r=>{r.steps[0].kind="activate"},r=>{r.steps[0].fromRect=null},
        r=>{r.plan.outputIds.push("DP-2")},r=>{r.plan.metadata.maximumWindows=0},
        r=>{r.stats.bad=NaN},r=>{r.stats.bad=Infinity},r=>{r.stats.bad=r.stats}
    ]) {
        const d=driver(),bad=copy(d.reply);mutate(bad.result);
        assert.equal(d.send(bad).length,0);assert.equal(d.state.phase,"Unavailable");
        assert.equal(d.state.lastResult.reason,"malformed-planning-result");cases++;
    }
    for (const [label,mutate,policy,expected] of [
        ["other desktop",s=>s.windows.forEach(w=>{w.onCurrentDesktop=false}),{},"no-eligible-windows: other_desktop=2"],
        ["owned controls",s=>s.windows.forEach(w=>{w.appId="oled-window-guard"}),{ownAppIds:["oled-window-guard"]},"no-eligible-windows: guard_owned=2"],
        ["overlap",s=>{s.windows[1].rect.x=50},{},"no-eligible-windows: overlapping_window=2"]
    ]) {
        const d=driver(),j=copy(job);j.mode="shift";j.options.shiftRangePercent=10;mutate(j.snapshot);
        j.registry=R.reconcile(j.snapshot,R.createRegistryState("session-1"),Object.assign({sessionEpoch:"session-1",selectedOutputIds:["DP-1"]},policy));
        const token=copy(d.command.token);token.context.snapshotSignature=R.snapshotSignature(j.snapshot);
        context.WorkerScript.onMessage({token,job:j});
        assert.equal(message.ok,true,label+": "+message.error);
        assert.equal(message.result.plan,null,label);
        assert.equal(message.result.reason,expected,label);
        assert.equal(message.result.trace,undefined,"no per-window trace is exported");cases++;
    }
    {
        const d=driver(),j=copy(job);j.mode="shift";j.options.maximumWindows=1;j.options.shiftRangePercent=10;
        const second=copy(j.snapshot.outputs[0]);second.id="HDMI-A-1";
        second.geometry.x=400;second.usableRect.x=400;j.snapshot.outputs.push(second);
        j.registry=R.reconcile(j.snapshot,R.createRegistryState("session-1"),{sessionEpoch:"session-1",selectedOutputIds:["DP-1","HDMI-A-1"]});
        const token=copy(d.command.token);token.context.snapshotSignature=R.snapshotSignature(j.snapshot);
        context.WorkerScript.onMessage({token,job:j});
        assert.equal(message.ok,true,message.error);assert.equal(message.result.plan.moves.length,1);
        assert.deepEqual(copy(message.result.plan.outputIds),["DP-1"]);cases++;
    }
    console.log("planning dispatch and bundled worker: "+cases+" cases passed");
} finally { fs.rmSync(directory,{recursive:true,force:true}); }

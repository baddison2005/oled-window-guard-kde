.pragma library
.import "model.js" as Model
.import "registry.js" as Registry
.import "planner.js" as Planner
.import "swap.js" as Swap
.import "group_execution.js" as GroupExecution
.import "swap_steps.js" as Steps
.import "planning_dispatch.js" as Dispatch

// Worker-only pure entry point: bounded plain-data input and validated output.
// Callbacks and compositor objects never cross the transport.
function planningRandom(seed) {
    var value=seed || 0x12345678;
    return function() { value^=value<<13;value^=value>>>17;value^=value<<5;return (value>>>0)/4294967296; };
}
function processPlanningRequest(message) {
    var start=Date.now(),token=message && message.token;
    try {
        var job=message && message.job,context=token && token.context;
        if (!token || !Dispatch.validPlanningContext(context) || !job || !job.snapshot || !job.registry ||
                !Array.isArray(job.snapshot.windows) || job.snapshot.windows.length>256 ||
                !Array.isArray(job.snapshot.outputs) || job.snapshot.outputs.length>16 ||
                !Array.isArray(job.registry.windows) || job.registry.windows.length>256 ||
                !Array.isArray(job.registry.outputs) || job.registry.outputs.length>16 ||
                !Model.validateSnapshot(job.snapshot).valid || !Planner.registryMatches(job.snapshot,job.registry) ||
                context.sessionEpoch !== job.registry.sessionEpoch || context.topologyRevision !== job.snapshot.topologyRevision ||
                context.snapshotSignature !== Registry.snapshotSignature(job.snapshot)) throw new Error("invalid worker input/context");
        var source=job.options || {},options=Model.copyPlain(source);
        if ((source.deadlineMs !== undefined && (typeof source.deadlineMs !== "number" || !isFinite(source.deadlineMs) || source.deadlineMs<0 || source.deadlineMs>100)) ||
                (source.maxEvaluations !== undefined && (!Number.isInteger(source.maxEvaluations) || source.maxEvaluations<1 || source.maxEvaluations>50000)) ||
                (source.seed !== undefined && (!Number.isInteger(source.seed) || source.seed<0 || source.seed>4294967295)))
            throw new Error("invalid worker budgets/seed");
        options.random=planningRandom(source.seed);options.now=function(){return Date.now()};
        // Prevent even preprocessing/validation from running on KWin's thread.
        var result,checked,stepCheck;
        if (job.mode === "shift") {
            result=Planner.planOrdinaryShift(job.snapshot,job.registry,options);
            if (!result.plan.metadata.complete || !result.plan.moves.length) {
                // Preserve a bounded explanation without exporting window IDs,
                // titles or the potentially large per-window planning trace.
                var relevant=Object.create(null),counts=Object.create(null);
                job.snapshot.windows.forEach(function(window) {
                    if (Array.isArray(window.outputIds) && window.outputIds.indexOf(options.outputId)>=0) relevant[window.identity]=true;
                });
                result.trace.forEach(function(item) {
                    if (relevant[item.identity] && item.status==="skipped")
                        counts[item.reason]=(counts[item.reason] || 0)+1;
                });
                var details=Object.keys(counts).sort().map(function(key){return key+"="+counts[key];}).join(",");
                var noPlanReason=result.stats.stopReason || (result.stats.candidates===0?"no-eligible-windows":"no-safe-move");
                result={plan:null,steps:[],stats:result.stats,reason:(noPlanReason+(details?": "+details:"")).slice(0,240)};
            }
            else {
                checked=Planner.validateFinalPlan(job.snapshot,job.registry,result.plan);
                result.steps=[];
            }
        } else if (job.mode === "swap" || job.mode === "coordinated") {
            result=job.mode === "swap"?Swap.planSwap(job.snapshot,job.registry,options):Swap.planCoordinatedShift(job.snapshot,job.registry,options);
            if (result.plan && result.plan.moves.length) {
                checked=result.plan.metadata.mode === "ordinary_shift"?Planner.validateFinalPlan(job.snapshot,job.registry,result.plan):
                    job.mode === "swap"?Planner.validateSwapFinalPlan(job.snapshot,job.registry,result.plan):Planner.validateCoordinatedFinalPlan(job.snapshot,job.registry,result.plan);
                if (result.steps.length) stepCheck=Steps.validateSwapSteps(job.snapshot,job.registry,result.plan,result.steps);
            } else result={plan:null,steps:[],stats:result.stats};
        } else if (job.mode === "group") {
            options.group=job.group;options.library=job.library;
            if ((job.group && job.group.revision || null) !== context.libraryRevision) throw new Error("library context mismatch");
            result=GroupExecution.planGroupMovement(job.snapshot,job.registry,options);
            if (result.plan) stepCheck=Steps.validateSwapSteps(job.snapshot,job.registry,result.plan,result.steps,{group:job.group,library:job.library});
        } else throw new Error("unknown planning mode");
        if (result.plan && result.plan.metadata.mode === "ordinary_shift" && result.plan.moves.length>1) {
            result.steps=Steps.orderOrdinarySteps(job.snapshot,job.registry,result.plan);
            stepCheck=Steps.validateSwapSteps(job.snapshot,job.registry,result.plan,result.steps);
        }
        if ((checked && !checked.valid) || (stepCheck && !stepCheck.valid)) throw new Error("worker final/step validation failed");
        // Nonparticipant traces can dwarf a plan on a crowded snapshot. Keep
        // the normal reply bounded to its validated plan, steps and summary.
        var reply={plan:result.plan || null,steps:result.steps || [],stats:result.stats || {},
            attempts:result.attempts || [],reason:result.reason || null};
        return {token:Model.copyPlain(token),ok:true,result:reply,elapsedMs:Date.now()-start};
    } catch(error) {
        return {token:token?Model.copyPlain(token):null,ok:false,error:String(error),elapsedMs:Date.now()-start};
    }
}

.pragma library
.import "scheduler.js" as Scheduler
.import "lifecycle.js" as Lifecycle
.import "safety.js" as Safety

// Pure bridge between session readiness and per-output clocks. A host must
// satisfy beginTransaction through the guarded warning/ordered executor; this
// module never invokes a geometry writer or starts a timer.
function createAutomaticState() {
    return { lifecycle:Lifecycle.createLifecycleState(), scheduler:Scheduler.createSchedulerState() };
}
function automaticCopy(value) { return JSON.parse(JSON.stringify(value)); }
function automaticTransition(input, event) {
    var state = automaticCopy(input || createAutomaticState()), commands = [];
    if (!event || typeof event.type !== "string") return { state:state, commands:commands };
    var oldFlight = state.scheduler.inFlight, life, scheduled;
    function cancelIfNeeded(reason) {
        if (oldFlight && !state.scheduler.inFlight) commands.push({ type:"cancelTransaction",
            outputId:oldFlight.outputId, schedulerGeneration:oldFlight.generation,
            reason:reason });
    }
    function lifecycle(eventValue) {
        life = Lifecycle.transitionLifecycle(state.lifecycle,eventValue);
        state.lifecycle = life.state;
        return life;
    }
    function schedule(eventValue) {
        scheduled = Scheduler.transitionScheduler(state.scheduler,eventValue);
        state.scheduler = scheduled.state;
        return scheduled;
    }
    if (event.type === "configure") {
        lifecycle({type:"pause"});
        schedule({type:"suspend"});
        schedule({type:"start",now:event.now,outputIds:event.outputIds,
            intervals:event.intervals,intervalMs:event.intervalMs});
        cancelIfNeeded("configure");
        return { state:state, commands:commands };
    }
    if (["pause","locked","companionLost","kwinEpochChanged","settingsChanged",
            "topologyChanged","wake"].indexOf(event.type) >= 0) {
        lifecycle(event);
        if (["settingsChanged","topologyChanged","wake"].indexOf(event.type) >= 0) {
            schedule({type:"suspend"});
            schedule({type:event.type,now:event.now,outputIds:event.outputIds,
                intervals:event.intervals,intervalMs:event.intervalMs});
        } else schedule({type:event.type === "pause" ? "pause" : "suspend"});
        cancelIfNeeded(event.type);
        return { state:state, commands:commands };
    }
    if (event.type === "resume") {
        lifecycle(event);
        if (state.lifecycle.phase === "Running") {
            var ids = Object.keys(state.scheduler.intervals);
            if (!ids.length || ids.some(function(id) {
                return event.safety.topologyOutputIds.indexOf(id) < 0;
            })) {
                lifecycle({type:"topologyChanged"});
                schedule({type:"suspend"});
            } else {
                schedule({type:"resume",now:event.now});
                if (state.scheduler.phase !== "Running") lifecycle({type:"wake"});
            }
        }
        cancelIfNeeded("resume-unsafe");
        return { state:state, commands:commands };
    }
    if (["tick","warningDeadline","skip","finished"].indexOf(event.type) >= 0) {
        lifecycle({type:"safetyUpdate",now:event.now,safety:event.safety,
            maxSafetyAgeMs:event.maxSafetyAgeMs,kwinEpoch:event.kwinEpoch,
            companion:event.companion});
        if (state.lifecycle.phase !== "Running" || life.effects.length) {
            schedule({type:"suspend"});
            cancelIfNeeded(state.lifecycle.reason || "unsafe");
            return { state:state, commands:commands };
        }
        if (event.type === "tick") {
            var due = schedule({type:"due",now:event.now}).commands;
            if (state.scheduler.phase !== "Running") {
                lifecycle({type:"wake"});
                cancelIfNeeded("clock-or-scheduler-suspended");
                return { state:state, commands:commands };
            }
            for (var i=0;i<due.length;i++) {
                state.scheduler.inFlight.stage = "Warning";
                commands.push({type:"beginTransaction",
                    outputId:due[i].outputId, schedulerGeneration:due[i].generation,
                    lifecycleGeneration:state.lifecycle.generation});
            }
        } else if (oldFlight && event.outputId === oldFlight.outputId &&
                event.schedulerGeneration === oldFlight.generation &&
                event.lifecycleGeneration === state.lifecycle.generation) {
            if (event.type === "warningDeadline" && oldFlight.stage === "Warning") {
                var quiet = Safety.evaluateForOutput(event.safety,event.now,
                    event.maxSafetyAgeMs === undefined ? 1000 : event.maxSafetyAgeMs,event.outputId);
                if (!quiet.safe) {
                    schedule({type:"skipped",outputId:event.outputId,generation:event.schedulerGeneration,now:event.now});
                    commands.push({type:"outputSkipped",outputId:event.outputId,reason:quiet.reason});
                    cancelIfNeeded("output-active");
                } else {
                    state.scheduler.inFlight.stage = "Applying";
                    commands.push({type:"revalidateTransaction",outputId:event.outputId,
                        schedulerGeneration:event.schedulerGeneration,
                        lifecycleGeneration:event.lifecycleGeneration});
                }
            } else if (event.type === "skip" && oldFlight.stage === "Warning") {
                schedule({type:"skipped",outputId:event.outputId,generation:event.schedulerGeneration,now:event.now});
                cancelIfNeeded("user-skip");
            } else if (event.type === "finished" && oldFlight.stage === "Applying" &&
                    (event.status === "committed" || event.status === "skipped")) {
                var finalQuiet = Safety.evaluateForOutput(event.safety,event.now,
                    event.maxSafetyAgeMs === undefined ? 1000 : event.maxSafetyAgeMs,event.outputId);
                if (!finalQuiet.safe) {
                    lifecycle({type:"pause"}); schedule({type:"pause"}); cancelIfNeeded(finalQuiet.reason);
                } else schedule({type:event.status === "committed" ? "completed" : "skipped",
                    outputId:event.outputId,generation:event.schedulerGeneration,now:event.now});
            } else if (event.type === "finished" && oldFlight.stage === "Applying") {
                lifecycle({type:"pause"});
                schedule({type:"pause"});
                cancelIfNeeded("transaction-failed");
            }
            if (state.scheduler.phase !== "Running") lifecycle({type:"wake"});
        }
        return { state:state, commands:commands };
    }
    return { state:state, commands:commands };
}

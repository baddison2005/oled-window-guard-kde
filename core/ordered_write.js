.pragma library
.import "model.js" as Model
.import "registry.js" as Registry
.import "safety.js" as Safety
.import "ordered_executor.js" as Ordered
.import "swap_steps.js" as Steps

// Final synchronous assignment gate for an already validated ordered route.
function validate(command,state,context) {
    function no(reason){return {valid:false,reason:reason};}
    const tx=state && state.tx, pending=state && state.pending;
    if (!tx || state.phase!=="Applying" || !pending || pending.type!=="writeGeometry" ||
            !command || command.type!==pending.type || command.id!==pending.id ||
            command.generation!==state.generation || pending.generation!==state.generation)
        return no("stale-ordered-command");
    if (!context || context.now<tx.writeIssuedAt || context.now>tx.writeDeadline)
        return no("ordered-write-deadline");
    if (!context || context.enabled!==true || context.paused!==false || context.nativeTokenCurrent!==true ||
            context.settingsRevision!==tx.settingsRevision || !context.safety ||
            !Safety.evaluate(context.safety,context.now,500).safe || !Ordered.orderedSafe(tx,context))
        return no("ordered-authority-unavailable");
    if (!context.registry || context.registry.sessionEpoch!==tx.sessionEpoch ||
            context.registry.topologyRevision!==tx.topologyRevision ||
            !Ordered.orderedSnapshotMatches(tx,context.snapshot,tx.expected)) return no("ordered-desktop-changed");
    const step=Ordered.orderedCurrentStep(tx);
    if (!step || command.identity!==step.identity || command.stepIndex!==tx.index ||
            command.rollback!==tx.rollback || !Ordered.orderedSame(command.expectedFrom,step.fromRect) ||
            !Ordered.orderedSame(command.destination,step.toRect)) return no("ordered-command-tampered");
    const window=Ordered.orderedWindow(context.snapshot,step.identity);
    // Eligibility can change during an explicitly permitted transient overlap;
    // original classification plus the full snapshot signature covers policy.
    if (!window || !Ordered.orderedSame(window.rect,step.fromRect) || (window.focused!==false && !(window.focused===true && context.registry.windows.some(function(w){return w.identity===window.identity && w.allowIdleFocused===true;}))) ||
            window.managed!==true || window.movable!==true || window.visible!==true ||
            window.minimized!==false || window.maximized!==false || window.fullscreen!==false || window.tiled!==false)
        return no("ordered-target-changed");
    const area=context.snapshot.outputs.filter(function(o){return o.id===tx.plan.outputIds[0];})[0];
    if (window.integerPosition===true && (step.toRect.x!==Math.round(step.toRect.x) || step.toRect.y!==Math.round(step.toRect.y)))
        return no("ordered-unrepresentable-destination");
    if (!area || !Model.rectangleContains(area.usableRect,step.toRect)) return no("ordered-destination-invalid");
    if (tx.plan.metadata.allowTransientOverlap!==true && context.snapshot.windows.some(function(w){
            return w.identity!==step.identity && Steps.stepObstacle(w) && Model.movementCollision(tx.initial,tx.plan,step.identity,step.toRect,w.identity,w.rect);
        })) return no("ordered-intermediate-collision");
    return {valid:true,reason:"safe"};
}

.pragma library
.import "model.js" as Model
.import "registry.js" as Registry
.import "planner.js" as Planner
.import "safety.js" as Safety

// Last synchronous policy check at the assignment boundary, including rollback.
function validate(command, state, context) {
    function no(reason) {return {valid:false,reason:reason};}
    if (!command || !state || !state.transaction || !state.pendingCommand ||
            command.type!=="writeGeometry" || command.id!==state.pendingCommand.id ||
            state.pendingCommand.type!=="writeGeometry" || state.pendingCommand.generation!==state.generation ||
            command.generation!==state.generation || command.transactionId!==state.transaction.id ||
            ["Applying","RollingBack"].indexOf(state.phase)<0) return no("stale-write-command");
    const tx=state.transaction;
    if (!context || context.enabled!==true || context.paused!==false || context.nativeTokenCurrent!==true ||
            context.settingsRevision!==tx.settingsRevision || !context.safety ||
            !Safety.evaluate(context.safety,context.now,500).safe ||
            context.safety.topologyGeneration!==tx.safetyTopologyGeneration) return no("write-authority-unavailable");
    if (!Model.validateSnapshot(context.snapshot).valid || !context.registry ||
            context.snapshot.topologyRevision!==tx.topologyRevision || context.registry.topologyRevision!==tx.topologyRevision)
        return no("write-context-changed");
    if (command.identity!==tx.move.identity || !Model.rectangle(command.expectedFrom) || !Model.rectangle(command.destination))
        return no("invalid-write-command");
    function equal(a,b) {return a && b && a.x===b.x && a.y===b.y && a.width===b.width && a.height===b.height;}
    const reversing=state.phase==="RollingBack";
    if (reversing!==!!command.rollback || !equal(command.expectedFrom,reversing?(tx.lastObservedFrame || tx.move.toRect):tx.move.fromRect) ||
            !equal(command.destination,reversing?tx.move.fromRect:tx.move.toRect)) return no("write-command-tampered");
    const plan=Model.copyPlain(reversing?tx.rollbackPlan:tx.plan);
    if (!plan || plan.moves.length!==1 || !Planner.validateFinalPlan(context.snapshot,context.registry,plan).valid)
        return no("write-plan-invalid");
    if (!reversing && Registry.snapshotSignature(context.snapshot)!==Registry.snapshotSignature(tx.snapshot))
        return no("write-desktop-changed");
    const ids=context.snapshot.outputs.map(function(o){return o.id;}).sort();
    if (JSON.stringify(ids)!==JSON.stringify(context.safety.topologyOutputIds.slice().sort())) return no("write-output-set-mismatch");
    const window=context.snapshot.windows.filter(function(w){return w.identity===command.identity;})[0];
    if (!window || !equal(window.rect,command.expectedFrom) || (window.focused!==false && !(window.focused===true && context.registry.windows.some(function(w){return w.identity===window.identity && w.allowIdleFocused===true;}))) || window.managed!==true ||
            window.movable!==true || window.maximized!==false || window.fullscreen!==false || window.tiled!==false)
        return no("write-target-changed");
    return {valid:true,reason:"safe"};
}

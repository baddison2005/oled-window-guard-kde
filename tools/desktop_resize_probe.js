// Read-only counterfactual using production eligibility and rotation search.
const fs=require('fs'), vm=require('vm');
const input=JSON.parse(fs.readFileSync(0,'utf8'));
const context={WorkerScript:{}};
vm.runInNewContext(fs.readFileSync(input.worker,'utf8'),context);
const modules=context.OWGModules, snapshot=input.snapshot, output=input.output;
const registry=modules['registry.js'].reconcile(snapshot,
    modules['registry.js'].createRegistryState('resize-probe'),
    {sessionEpoch:'resize-probe',selectedOutputIds:[output.id],excludeFocused:true,ownAppIds:['oled-window-guard']});
const candidate=snapshot.windows.find(w=>w.identity===input.identity);
const registered=registry.windows.find(w=>w.identity===input.identity);
if(!candidate || !modules['planner.js'].eligible(candidate,snapshot,output,registered) || candidate.resizable!==(input.expectShrink===true))
    throw Error('Target eligibility or compositor resize capability does not match the requested check');
const options={padding:0,sizeTolerance:0.1,maximumWindows:1,random:()=>0.25,now:()=>0,maxEvaluations:2400,deadlineMs:30};
let productionRefusal=null;
if(input.library) {
    const group=modules['groups.js'].groupsFromLibrary(input.library).find(g=>g.id===input.groupId);
    if(!group || group.padding!==0) throw Error('Imported test group unavailable');
    const frames=modules['groups.js'].groupFrames(group,output.usableRect);
    if(JSON.stringify(frames)!==JSON.stringify(input.zones)) throw Error('Imported rounding changed test geometry');
    productionRefusal=modules['group_execution.js'].planGroupMovement(snapshot,registry,
        Object.assign({},options,{outputId:output.id,group,library:input.library,rotateGroup:true}));
    if(input.expectShrink) {
        const moves=productionRefusal.plan && productionRefusal.plan.moves;
        if(!moves || moves.length!==1 || moves[0].identity!==candidate.identity ||
            Math.abs(moves[0].toRect.width-(candidate.rect.width-2))>0.01 || !moves[0].permitsRoundingResize)
            throw Error('Full production planner did not propose the selected two-pixel shrink');
        process.stdout.write(JSON.stringify({productionPlan:productionRefusal,target:input.identity}));
        process.exit(0);
    }
    if(productionRefusal.plan || productionRefusal.reason!=='no_group_rotation')
        throw Error('Full production planner did not isolate fixed-size refusal: '+productionRefusal.reason);
}
const rotation=modules['group_rotation.js'];
const fixed=rotation.proposeGroupRotation([candidate],input.zones,output.usableRect,input.obstacles,options);
const resizable=rotation.proposeGroupRotation([Object.assign({},candidate,{resizable:true})],input.zones,output.usableRect,input.obstacles,options);
if(fixed.moves.length || fixed.reason!=='no_group_rotation' || resizable.moves.length!==1 ||
    resizable.moves[0].toRect.width!==candidate.rect.width-2 || !resizable.moves[0].permitsRoundingResize)
    throw Error('Geometry does not isolate a two-pixel constraint-driven shrink refusal');
process.stdout.write(JSON.stringify({fixed,resizable,productionRefusal,target:input.identity}));

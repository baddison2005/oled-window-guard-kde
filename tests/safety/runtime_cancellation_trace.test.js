const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const source = fs.readFileSync('packages/kwin-script/contents/ui/RuntimeController.qml', 'utf8');
const Guarding = {};
vm.runInNewContext(fs.readFileSync('core/guarding.js','utf8').replace(/^\.pragma library\s*/m,''),Guarding);
function extract(name) {
    const start = source.indexOf('function ' + name + '(');
    assert(start >= 0);
    const open = source.indexOf('{', start);
    let depth = 1, end = open + 1;
    while (depth && end < source.length) {
        if (source[end] === '{') depth++;
        if (source[end] === '}') depth--;
        end++;
    }
    return source.slice(start, end);
}
function fixture() {
    let sample = {epoch:'native-1', cancellationGeneration:8, topologyGeneration:2,
        sequence:5, pointer:3, button:2, axis:0, keyboard:0};
    const warning = {activeGeneration:1};
    const runtime = {guards:{movement:true,brightness:true,unlock:null}, get armed(){return this.guards.movement || this.guards.brightness;}, pending:{id:'move-1', action:'MoveNow', issued:true},
        executing:true, result:null, cancellationTrace:[], scheduler:{phase:'Suspended'},
        planning:{invalidate() {},setScope() {}, nativeObservation:{immediateCancellation:true,
            localSnapshot() {return sample;}}},
        observer:{dimming:{clears:[],clear(reason){this.clears.push(reason);}},warningSurface:warning, writer:{cancel() {warning.activeGeneration=-1;}},
            orderedWriter:{cancel() {}}}, schedulerNow() {return 100;}};
    const context = vm.createContext({runtime, Guarding, Scheduler:{transitionScheduler(state) {return {state};}}});
    for (const name of ['complete','cancel','topologyChanged','onCanceled']) {
        // QML properties are lexical names in its JavaScript functions.
        runtime[name] = vm.runInContext('with (runtime) { (' + extract(name) + ') }', context);
    }
    return {runtime, context, setSample(value) {sample=value;}};
}
{
    const {runtime, setSample} = fixture();
    runtime.onCanceled('native-input');
    assert.equal(runtime.observer.dimming.clears.length, 0); // movement cancellation preserves dimming
    assert.equal(runtime.result.reason, 'native-input');
    assert.equal(runtime.pending, null);
    assert.equal(runtime.armed, true);
    setSample({epoch:'native-1', cancellationGeneration:9, topologyGeneration:3,
        sequence:5, pointer:3, button:2, axis:0, keyboard:0});
    runtime.onCanceled('native-topology');
    assert.equal(runtime.observer.dimming.clears.at(-1), 'native-topology-unavailable');
    assert.equal(runtime.armed, false);
    assert.equal(runtime.result.reason, 'native-input');
    assert.equal(runtime.cancellationTrace.length, 2);
    assert.equal(runtime.cancellationTrace[0].commandId, 'move-1');
    assert.equal(runtime.cancellationTrace[1].commandId, null);
    assert.equal(runtime.cancellationTrace[1].topologyGeneration, 3);
}
{
    const {runtime} = fixture();
    runtime.onCanceled('native-topology');
    assert.equal(runtime.result.reason, 'native-topology-unavailable');
    assert.equal(runtime.armed, false);
    for (let n=0;n<30;n++) runtime.onCanceled('native-topology');
    assert.equal(runtime.cancellationTrace.length, 16);
}
{
    const {runtime} = fixture();
    runtime.planning.nativeObservation.localSnapshot = () => {throw Error('unavailable');};
    runtime.onCanceled('native-topology');
    assert.equal(runtime.armed, false);
    assert.equal(runtime.pending, null);
    assert.equal(runtime.result.reason, 'native-topology-unavailable');
}
console.log('PASS runtime cancellation attribution, topology disarm, bounded trace and diagnostic failure');

// A completed warning receipt cannot be replayed for another request or after
// expiry, input invalidation, hide failure or controller state replacement.
{
 const {runtime,context}=fixture();
 runtime.pending={id:'move',action:'MoveNow',stage:'planning',receipt:{hidden:true,deadline:1000,nativeToken:{id:1}}};
 runtime.planning.report={id:'move'};
 let now=1100,current=true;
 runtime.planning.nativeObservation.monotonicNow=()=>now;
 runtime.planning.nativeObservation.tokenCurrent=()=>current;
 const receipt=vm.runInContext('with(runtime){('+extract('movementReceipt')+')}',context);
 assert.ok(receipt({}));assert.ok(receipt({},true));assert.equal(receipt({},true),null);now=999;assert.equal(receipt({}),null);
 now=3001;assert.equal(receipt({}),null);now=1100;current=false;assert.equal(receipt({}),null);
 current=true;runtime.pending.receipt.hidden=false;assert.equal(receipt({}),null);
 runtime.pending.receipt.hidden=true;runtime.planning.report.id='another';assert.equal(receipt({}),null);
}
console.log('PASS warning receipts reject early, expired, hidden-failure, wrong-request and invalidated use');

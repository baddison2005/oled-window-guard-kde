'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const qml=fs.readFileSync('packages/kwin-script/contents/ui/OrderedGuardedWriter.qml','utf8');
const source=qml.slice(qml.indexOf('    function write(command)'),qml.indexOf('    property Connections inputEvents:'));
assert.ok(source.includes('client-size-constraint'));
function run({minimum={width:98,height:60},maximum={width:1000,height:1000},destination={x:10,y:10,width:118,height:100},from={x:10,y:10,width:120,height:100},client={x:20,y:30,width:100,height:80},authorized=true}={}){
 const acks=[],cancels=[],assignments=[];
 const window={internalId:"client-a",deleted:false,clientGeometry:client,minSize:minimum,maxSize:maximum,get frameGeometry(){return from;},set frameGeometry(rect){assignments.push(rect);}};
 const c={state:{generation:1},nativeToken:{},observer:{identity:()=> 'a',rect:r=>r},planning:{nativeObservation:{tokenCurrent:()=>true,localObserver:{frameSizeSupported:(id,width,height)=>{
 assert.equal(id,'client-a');const bw=client?from.width-client.width:0,bh=client?from.height-client.height:0;
 return !!client && !!minimum && !!maximum && [minimum.width,minimum.height,maximum.width,maximum.height].every(Number.isFinite) && width>=minimum.width+bw && height>=minimum.height+bh && width<=maximum.width+bw && height<=maximum.height+bh;
 }}}},Workspace:{stackingOrder:[window],activeWindow:null},WriteGate:{validate:()=>({valid:authorized,reason:'not-authorized'})},captureContext:()=>({registry:{windows:[]}}),Model:{rectanglesClose:(a,b)=>JSON.stringify(a)===JSON.stringify(b),copyPlain:x=>JSON.parse(JSON.stringify(x))},Qt:{rect:(x,y,width,height)=>({x,y,width,height})},cancel:r=>cancels.push(r),acknowledge:e=>acks.push(e),writeCount:0};
 vm.createContext(c);vm.runInContext(source,c);c.write({identity:'a',id:'write-1',expectedFrom:from,destination});return{acks,cancels,assignments};
}
assert.equal(run().assignments.length,1);
for(const options of [{minimum:{width:99,height:60}},{maximum:{width:97,height:1000}},{minimum:{width:NaN,height:60}},{client:null}]){
 const r=run(options);assert.equal(r.assignments.length,0);assert.equal(r.acks[0].reason,'client-size-constraint');
}
const expansion=run({from:{x:10,y:10,width:118,height:100},client:{x:20,y:30,width:98,height:80},maximum:{width:99,height:1000},destination:{x:10,y:10,width:120,height:100}});
assert.equal(expansion.assignments.length,0);assert.equal(expansion.acks[0].reason,'client-size-constraint');
assert.equal(run({authorized:false}).assignments.length,0);
assert.equal(run({minimum:null,maximum:null,destination:{x:20,y:20,width:120,height:100}}).assignments.length,1,'position-only moves do not need resize hints');
console.log('PASS native assignment resize bounds: fresh client minima/maxima, decorations, inverse growth, unknown hints and unchanged-size movement');

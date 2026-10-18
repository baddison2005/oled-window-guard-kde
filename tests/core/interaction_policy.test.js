'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function load(name,imports={}){const context={...imports};vm.runInNewContext(fs.readFileSync('core/'+name+'.js','utf8').replace(/^\.pragma library\s*/m,'').replace(/^\.import .*\n/gm,''),context);return context;}
const Native=load('native_observation'),P=load('interaction_policy',{Native}),Model=load('model'),R=load('registry',{Model}),S=load('native_safety',{Native,Model});
const copy=x=>JSON.parse(JSON.stringify(x)),rect=(x,y,width,height)=>({x,y,width,height});
function sample(){return {schema:2,epoch:'e',sequence:0,pointer:0,button:0,axis:0,keyboard:0,unknownFocusKeyboard:0,focusGeneration:0,topologyGeneration:0,sessionGeneration:0,lockState:'unlocked',sleepState:'awake',sessionActivity:'active',ageMs:-1,buttonKnown:true,buttonsDown:false,focusKnown:true,outputIdentityComplete:true,immediateCancellation:true,otherInputObserved:false,cancellationGeneration:0,interactionProtocol:1,outputs:['A','B'].map(id=>({id,events:0,ageMs:-1,interactionEvents:0,interactionAgeMs:10000,focusEvents:0,buttonsDown:false}))};}
let s=sample(),token=P.token(s,['A']),preview=P.token(s,[]);assert.ok(token);
let cases=0;
for(const kind of ['pointer','button','axis','keyboard','focus']) for(const target of [0,1]){
 const fresh=copy(s);
 if(kind==='focus'){fresh.focusGeneration++;fresh.outputs[target].focusEvents++;}
 else {fresh[kind]++;fresh.sequence++;fresh.outputs[target].events++;
  if(kind!=='pointer'){fresh.outputs[target].interactionEvents++;fresh.outputs[target].interactionAgeMs=0;}}
 assert.equal(P.current(token,fresh),kind==='pointer'||target===1);
 assert.equal(P.current(preview,fresh),true);
 assert.equal(P.shouldCancel(s,fresh,['A'],kind==='focus'?'native-focus':'native-input'),kind!=='pointer'&&target===0);
 cases++;
}
for(const boundary of [{epoch:'new'},{topologyGeneration:1},{sessionGeneration:1},{otherInputObserved:true}]){
 assert.equal(P.current(preview,Object.assign(copy(s),boundary)),false);cases++;
}
s.outputs[1].buttonsDown=true;s.buttonsDown=true;s.outputs[1].interactionAgeMs=0;
const full={topologyRevision:'t',focusedIdentity:null,outputs:[{id:'A',connector:'A',geometry:rect(0,0,1000,800),usableRect:rect(0,0,1000,800),scale:1},{id:'B',connector:'B',geometry:rect(1000,0,1000,800),usableRect:rect(1000,0,1000,800),scale:1}],windows:[]};
const options={immediateCancellation:true,companionConnected:true,topologyStable:true,quietMs:5000,interactionOnly:true,outputIds:['A']};
assert.equal(S.normalize(s,full,10000,options).safe,true);
assert.equal(S.normalize(s,full,10000,{...options,outputIds:['B']}).safe,false);
assert.equal(S.normalize(s,full,10000,{...options,outputIds:['unknown']}).safe,false);
assert.equal(S.normalize({...s,interactionProtocol:undefined},full,10000,options).safe,false);
function win(identity,x,ids){return {identity,appId:'app',outputIds:ids,rect:rect(x,0,200,100),visible:true,onCurrentDesktop:true,onCurrentActivity:true,focused:false,managed:true,modal:false,transient:false,movable:true,resizable:true,minimized:false,maximized:false,fullscreen:false,tiled:false,windowType:'normal',skipReasons:[]};}
full.windows=[win('a',20,['A']),win('b',1200,['B']),win('span',900,['A','B'])];full.focusedIdentity='b';full.windows[1].focused=true;
const scoped=R.scopeSnapshot(full,['A']);
assert.ok(Model.validateSnapshot(scoped).valid);
assert.equal(scoped.outputs.length,1);assert.equal(scoped.windows.length,2);assert.equal(scoped.focusedIdentity,null);
assert.ok(scoped.windows[1].skipReasons.includes('spanning'));
const original=R.snapshotSignature(scoped);
const focusLeft=copy(scoped);focusLeft.focusedIdentity='a';focusLeft.windows[0].focused=true;
assert.equal(R.snapshotSignature(focusLeft),original);full.windows[1].rect.x+=20;full.windows[1].focused=false;full.focusedIdentity=null;
assert.equal(R.snapshotSignature(R.scopeSnapshot(full,['A'])),original);
full.windows[0].rect.x+=20;assert.notEqual(R.snapshotSignature(R.scopeSnapshot(full,['A'])),original);
console.log('PASS scoped interaction tokens, passive motion, other-display holds, target quiet time and collision snapshots ('+cases+' token cases)');

s.focusKnown=false;
assert.equal(S.normalize(s,full,10000,options).safe,false);
assert.equal(S.normalize(s,full,10000,{...options,allowUnfocusedDesktop:true}).safe,true);
console.log('PASS unfocused desktop permitted for dimming only; normal movement gate remains closed');

// Native monotonic event timestamps make the grace cutoff independent of
// callback/poll delay and wall-clock changes. The exact boundary cancels.
s=sample();s.graceProtocol=1;s.monotonicMs=100;
s.outputs.forEach(o=>{o.lastInteractionMs=-1;o.keysDown=false;});
const grace=P.token(s,['A'],{ignoreFocus:true,graceUntilMs:1100});assert.ok(grace);
const inputAt=time=>{const next=copy(s);next.monotonicMs=2000;next.keyboard++;next.sequence++;
 next.outputs[0].events++;next.outputs[0].interactionEvents++;next.outputs[0].lastInteractionMs=time;return next;};
assert.equal(P.current(grace,inputAt(1099)),true);
assert.equal(P.current(grace,inputAt(1100)),false);
assert.equal(P.current(grace,inputAt(1101)),false);
assert.equal(P.current(grace,{...inputAt(1099),lockState:'locked'}),false);
const focused=copy(s);focused.focusGeneration++;focused.outputs[0].focusEvents++;
assert.equal(P.current(grace,focused),true);
assert.equal(P.current(P.token(s,['A'],{ignoreFocus:true}),inputAt(1099)),false);
const absent=copy(s);delete absent.graceProtocol;
assert.equal(P.token(absent,['A'],{graceUntilMs:1100}),null);
const held=copy(s);held.buttonsDown=true;held.outputs[0].buttonsDown=true;
assert.equal(S.normalize(held,full,10000,{...options,quietMs:0}).safe,false);
assert.equal(S.normalize(held,full,10000,{...options,quietMs:0,ignoreKnownHolds:true}).safe,true);
console.log('PASS native monotonic grace: before/exact/after cutoff, delayed sampling, focus, held input and global boundaries');
// Desktop aliases are used by both movement eligibility and dimming; matching
// is exact after case/desktop-suffix normalization, never a title substring.
assert.equal(Model.applicationExcluded('steam',['com.valvesoftware.Steam'],{'com.valvesoftware.Steam':['Steam']}),true);
assert.equal(Model.applicationExcluded('steam-helper',['com.valvesoftware.Steam'],{'com.valvesoftware.Steam':['Steam']}),false);
assert.equal(Model.applicationExcluded('ORG.KDE.KONSOLE',['org.kde.konsole.desktop']),true);
const aliases={'com.valvesoftware.Steam':['Steam']};
const steam={...full.windows[0],appId:'steam'};
const steamSnapshot={...full,windows:[steam],focusedIdentity:null};
const steamRegistry=R.reconcile(steamSnapshot,R.createRegistryState('steam'),{sessionEpoch:'steam',selectedOutputIds:['A'],excludedApps:['com.valvesoftware.Steam'],applicationAliases:aliases});
assert.ok(steamRegistry.windows[0].skipReasons.includes('excluded_app'));

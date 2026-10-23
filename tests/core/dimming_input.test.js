const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('packages/kwin-script/contents/ui/DimmingController.qml','utf8');
const begin=source.indexOf('function inputChanged('),open=source.indexOf('{',begin);let depth=1,end=open+1;
while(depth){if(source[end]==='{')depth++;if(source[end]==='}')depth--;end++;}
let sample={button:0,buttonsDown:false,keyboard:0,axis:0},hover='active',captures=0,ticks=0;
const dimmer={stopping:false,lastInput:null,pendingScope:null,protectedWindowIds:[],
 observer:{capture(){return {windows:[]};},planning:{nativeObservation:{localSnapshot(){return {...sample};}}}},
 pointerScope(){captures++;return {type:'windowClick',displayId:'A',windowId:hover};},
 tick(){ticks++;},clear(){this.protectedWindowIds=[];}};
const context=vm.createContext({dimmer,Context:{shellWindow(){return false;}}});
const input=vm.runInContext('with(dimmer){('+source.slice(begin,end)+')}',context);
input('native-input');
for(let i=0;i<1000;i++)input('native-input');
assert.equal(ticks,0);assert.equal(captures,0);
sample.button++;sample.buttonsDown=true;input('native-input');
assert.equal(ticks,1);assert.deepEqual([...dimmer.protectedWindowIds],['active']);
hover='inactive';for(let i=0;i<1000;i++)input('native-input');
assert.equal(ticks,1);assert.equal(captures,1);assert.deepEqual([...dimmer.protectedWindowIds],['active']);
sample.button++;sample.buttonsDown=false;input('native-input');
assert.equal(ticks,2);assert.equal(dimmer.protectedWindowIds.length,0);
sample.keyboard++;input('native-input');assert.equal(ticks,3);
input('native-focus');assert.equal(ticks,4);
console.log('PASS passive/drag motion does not flood captures or brighten hovered inactive windows');

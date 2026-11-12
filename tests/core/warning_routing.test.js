'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
function method(file,name,context) {
 const text=fs.readFileSync('packages/kwin-script/contents/ui/'+file,'utf8');
 const start=text.indexOf('function '+name+'('),open=text.indexOf('{',start);let end=open+1,depth=1;
 while(depth){if(text[end]==='{')depth++;if(text[end]==='}')depth--;end++;}
 return vm.runInNewContext('('+text.slice(start,end)+')',context);
}
const outputs=[{id:'LG',usableRect:{x:12,y:40,width:2000,height:1000},geometry:{x:0,y:0,width:2048,height:1152}},
 {id:'HDMI',usableRect:{x:2100,y:-900,width:900,height:600},geometry:{x:2048,y:-900,width:1024,height:768}}];
for(const target of outputs)for(const position of ['topLeft','topCenter','topRight','random']) {
 const calls=[],warning={showForOutput(...args){calls.push(args);}};
 const settings={warningSeconds:8,quietSeconds:4,warningPosition:position};
 const nativeObservation={startWarningGrace(){},cancellationToken(){return {};}};
 const planning={configuration:{settings},nativeObservation,prepareWarning(){}};
 const observer={outputsNow(){return outputs;},warningSurface:warning};
 const runtime={observer,planning,pending:{id:'move',outputId:target.id,seed:9},warningSerial:1,complete(){throw Error('unexpected rejection');}};
 method('RuntimeController.qml','startMovementWarning',runtime)();
 const single={observer,planning,configuration:{settings},warning,state:{generation:5},nativeToken:null};
 method('GuardedWriter.qml','handle',single)({type:'showWarning',outputId:target.id,durationMs:8000});
 const ordered={observer,planning,configuration:{settings},warning,state:{generation:6},nativeToken:null,
  Model:{copyPlain(v){return JSON.parse(JSON.stringify(v));}}};
 method('OrderedGuardedWriter.qml','showWarning',ordered)({plan:{outputIds:[target.id],moves:[{}]}},{snapshot:{outputs}},true);
 assert.equal(calls.length,3);
 for(const args of calls){assert.strictEqual(args[1],target.usableRect);assert.equal(args[2],8000);assert.equal(args[3],position);assert.strictEqual(args[4],target.geometry);}
 assert.equal(warning.restoring,true);
}
console.log('PASS warning placement routed for both displays through manual, single-writer and ordered Restore paths');

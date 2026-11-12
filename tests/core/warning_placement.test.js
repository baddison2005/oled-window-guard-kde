'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const p={};vm.runInNewContext(fs.readFileSync('core/warning_placement.js','utf8').replace(/^\.pragma library\s*/,''),p);
const rect=(x,y,width,height)=>({x,y,width,height});
const screen=rect(-2560,-1000,2560,1440),usable=rect(-2480,-976,2480,1416);
for(const [position,x] of [['topLeft',-2468],['topCenter',-1435],['topRight',-402]]) {
 const frame=p.geometry(usable,screen,position);
 assert.equal(frame.x,x);assert.equal(frame.y,-964);assert.equal(frame.width,390);assert.equal(frame.height,126);
}
function inside(inner,outer) {
 assert.ok(inner.x>=outer.x && inner.y>=outer.y && inner.x+inner.width<=outer.x+outer.width && inner.y+inner.height<=outer.y+outer.height,JSON.stringify({inner,outer}));
}
// Negative origins, portrait displays, scaled logical sizes, top/side panels,
// fractional bounds and narrow/short monitors all keep the entire card visible.
for(const full of [screen,rect(5120,1080,1920,1080),rect(0,-1440,1080,1920),rect(-1234.5,42.25,1706.66,720),rect(0,0,280,150),rect(0,0,1280,480)]) {
 const area=rect(full.x+8,full.y+20,full.width-16,full.height-20);
 if(area.width<264 || area.height<150)continue;
 for(const rx of [0,.1,.4,.7,1])for(const ry of [0,.2,.6,1]) {
  const f=p.geometry(area,full,'random',rx,ry);inside(f,area);
  if(Math.ceil(area.y)+126<=full.y+full.height/4)
   assert.ok(f.y+f.height<=full.y+full.height/4);
  else assert.equal(f.y,Math.ceil(area.y));
 }
}
const xs=new Set(),ys=new Set();
const fractional=rect(-100.25,10.5,1280.5,720.5),roundedWorkArea=rect(-100.5,10.3,1281,720.9);
inside(p.geometry(roundedWorkArea,fractional,'random',1,1),fractional);
for(let i=0;i<30;i++) {
 const f=p.geometry(usable,screen,'random',i/29,((i*11)%30)/29);xs.add(f.x);ys.add(f.y);
}
assert.ok(xs.size>20 && ys.size>20);
const short=rect(100,50,1280,480),shortUsable=rect(100,85,1280,445);
for(const r of [0,.2,.5,1])assert.equal(p.geometry(shortUsable,short,'random',r,r).y,85);
for(const position of ['topLeft','topCenter','topRight','random']) {
 const f=p.geometry(rect(0,0,264,150),rect(0,0,264,150),position,.5,.5);inside(f,rect(0,0,264,150));assert.equal(f.width,240);
}
for(const args of [[null,screen,'random',0,0],[usable,null,'topCenter'],[usable,screen,'bottomLeft'],[usable,screen,'random',NaN,0],[usable,screen,'random',0,1.01],[rect(0,0,263,150),rect(0,0,263,150),'topLeft'],[rect(0,0,900,149),rect(0,0,900,149),'topLeft'],[rect(-3000,0,900,650),screen,'topLeft']])assert.equal(p.geometry(...args),null);
console.log('PASS warning placement: corners, top quarter, panels, negative/fractional origins, small displays and invalid inputs');

"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("core/free_swap.js", "utf8").replace(/^\.pragma library\s*/m, "");
const context = {};
vm.runInNewContext(source + "\nthis.api={proposeFreeSwap,proposeCoordinatedShift};", context);
const api=context.api;
const rect=(x,y,width,height)=>({x,y,width,height});
const win=(identity,x,y,width,height)=>({identity,rect:rect(x,y,width,height)});
const area=rect(0,0,1000,800);
const options={maximumWindows:4,random:()=>0.37,now:()=>10,deadlineMs:500};
function assertSafe(windows, moves, obstacles=[]) {
    const final=windows.map(w=>{const m=moves.find(x=>x.identity===w.identity);return m?m.toRect:w.rect;});
    for(let i=0;i<final.length;i++) {
        const r=final[i]; assert(r.x>=area.x&&r.y>=area.y&&r.x+r.width<=area.x+area.width&&r.y+r.height<=area.y+area.height);
        for(let j=i+1;j<final.length;j++) assert(!overlap(r,final[j]));
        for(const o of obstacles) assert(!overlap(r,o));
    }
}
function overlap(a,b){return a.x<b.x+b.width-0.01&&b.x<a.x+a.width-0.01&&a.y<b.y+b.height-0.01&&b.y<a.y+a.height-0.01;}

const mixed=[win("large",20,20,420,260),win("small",500,30,150,110),win("medium",230,400,260,170)];
const swap=api.proposeFreeSwap(mixed,area,[],options);
assert(swap.moves.length>=2,JSON.stringify(swap));
assertSafe(mixed,swap.moves);
assert(swap.stats.visited<=50000);

const blocked=api.proposeFreeSwap(mixed,area,[rect(0,0,1000,800)],options);
assert.equal(blocked.moves.length,0);
assert.equal(blocked.reason,"no-arrangement");

const pair=[win("left",100,300,180,180),win("right",650,300,180,180)];
const shiftOptions={...options,shiftRangePercent:90,enableHorizontalOrderChange:true,enableVerticalOrderChange:false,random:(()=>{let n=0;return()=>n++%2===0?0.5:0.25;})()};
const coordinated=api.proposeCoordinatedShift(pair,area,[],shiftOptions);
assert(coordinated.moves.length>0,JSON.stringify(coordinated));
assert(coordinated.moves.some(m=>Math.hypot((m.toRect.x-m.fromRect.x)/(area.width*.9),(m.toRect.y-m.fromRect.y)/(area.height*.9))>=.4));
assertSafe(pair,coordinated.moves);
const bothAxes=api.proposeCoordinatedShift(pair,area,[],{...shiftOptions,enableVerticalOrderChange:true,random:(()=>{let n=0;return()=>n++%2===0?0.5:0.25;})()});
assert.ok(bothAxes.moves.length>=2);assertSafe(pair,bothAxes.moves);
const tooSmall=api.proposeCoordinatedShift(pair,area,[],{...shiftOptions,random:()=>0.5});
assert.equal(tooSmall.reason,"sample-too-small");
const disabled=api.proposeCoordinatedShift(pair,area,[],{...shiftOptions,enableHorizontalOrderChange:false});
assert.equal(disabled.reason,"no-enabled-axis");
const deadline=api.proposeFreeSwap(mixed,area,[],{...options,now:(()=>{let n=0;return()=>n++;})(),deadlineMs:0});
assert.equal(deadline.reason,"deadline");
const canceled=api.proposeFreeSwap(mixed,area,[],{...options,shouldCancel:()=>true});
assert.equal(canceled.reason,"canceled");
const bounded=api.proposeFreeSwap(mixed,area,[],{...options,maxEvaluations:2});
assert(bounded.stats.visited<=2);
console.log("core free swap: tests passed");

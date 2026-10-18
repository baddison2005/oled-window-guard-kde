// Pure bounded search for assigning grouped-layout windows to alternative zones.
// The caller owns eligibility filtering, execution ordering, and final revalidation.
var HARD_MAX_EVALUATIONS = 50000;
var MAX_CANDIDATES = 64;
function finite(n) { return typeof n === "number" && isFinite(n); }
function validRect(r) { return r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0 && finite(r.x + r.width) && finite(r.y + r.height); }
function copyRect(r) { return { x:r.x, y:r.y, width:r.width, height:r.height }; }
function contains(a, r) { return r.x >= a.x && r.y >= a.y && r.x + r.width <= a.x + a.width && r.y + r.height <= a.y + a.height; }
function overlaps(a, b) { return Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x) > 0.01 && Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y) > 0.01; }
function close(a,b) { return Math.abs(a.x-b.x)<=0.01 && Math.abs(a.y-b.y)<=0.01 && Math.abs(a.width-b.width)<=0.01 && Math.abs(a.height-b.height)<=0.01; }
function similar(a,b,t) { return Math.abs(a.width-b.width)/Math.max(a.width,b.width)<=t && Math.abs(a.height-b.height)/Math.max(a.height,b.height)<=t; }
function setup(windows,zones,area,obstacles,options) {
    options=options||{};
    var padding=options.padding, tolerance=options.sizeTolerance, max=options.maximumWindows;
    if (!Array.isArray(windows)||!Array.isArray(zones)||!zones.length||!validRect(area)||!Array.isArray(obstacles)||
        !finite(padding)||padding<0||padding>200||!finite(tolerance)||tolerance<0||tolerance>0.25||!finite(max)||Math.floor(max)!==max||max<1||max>12||
        zones.some(function(z){return !validRect(z);})||obstacles.some(function(o){return !validRect(o);})) throw new Error("invalid group rotation inputs");
    var random=options.random||Math.random, now=options.now||function(){return Date.now();};
    if(typeof random!=="function"||typeof now!=="function"||(options.shouldCancel!==undefined&&typeof options.shouldCancel!=="function")) throw new Error("invalid planner callback");
    var budget=options.maxEvaluations===undefined?2400:options.maxEvaluations, duration=options.deadlineMs===undefined?30:options.deadlineMs;
    if(!finite(budget)||Math.floor(budget)!==budget||budget<1||!finite(duration)||duration<0) throw new Error("invalid search budget");
    var start=now(); if(!finite(start)) throw new Error("planner clock must be finite");
    return { windows:windows.slice(0,MAX_CANDIDATES).filter(function(w){return w&&typeof w.identity==="string"&&w.identity&&validRect(w.rect)&&contains(area,w.rect)&&typeof w.resizable==="boolean";}),
        zones:zones.map(copyRect),area:copyRect(area),obstacles:obstacles.map(copyRect),padding:padding,tolerance:tolerance,max:max,random:random,now:now,start:start,deadline:start+duration,
        budget:Math.min(HARD_MAX_EVALUATIONS,budget),shouldCancel:options.shouldCancel||function(){return false;},evaluations:0,stopped:false };
}
function tick(s) { if(s.evaluations>=s.budget||s.now()>s.deadline||s.shouldCancel()){s.stopped=true;return false;} s.evaluations++; return true; }
function candidates(s,w) {
    var allowance=Math.min(s.padding+2,32), shrinkLimit=Math.min(2*s.padding+2,32), result=[];
    var sources=s.zones.filter(function(z){return contains({x:z.x-allowance,y:z.y-allowance,width:z.width+2*allowance,height:z.height+2*allowance},w.rect);});
    sources.forEach(function(source){s.zones.forEach(function(zone){
        if(close(source,zone)||!similar(source,zone,s.tolerance)) return;
        var left=w.integerPosition===true?Math.ceil(zone.x):zone.x,top=w.integerPosition===true?Math.ceil(zone.y):zone.y;
        var availableWidth=zone.x+zone.width-left,availableHeight=zone.y+zone.height-top;
        var width=Math.abs(availableWidth-w.rect.width)<=2?availableWidth:Math.min(w.rect.width,availableWidth);
        var height=Math.abs(availableHeight-w.rect.height)<=2?availableHeight:Math.min(w.rect.height,availableHeight);
        if (w.integerPosition===true) {width=Math.floor(width);height=Math.floor(height);}
        if(width<=0||height<=0||(width!==w.rect.width||height!==w.rect.height)&&(!w.resizable||w.rect.width-width>shrinkLimit||w.rect.height-height>shrinkLimit||width-w.rect.width>2||height-w.rect.height>2)) return;
        if ((width!==w.rect.width || height!==w.rect.height) && (w.minimumSize===null || w.minimumSize && (width<w.minimumSize.width || height<w.minimumSize.height))) return;
        var right=zone.x+zone.width-width,bottom=zone.y+zone.height-height;
        if (w.integerPosition===true) {right=Math.floor(right);bottom=Math.floor(bottom);}
        var x=Math.max(left,Math.min(right,zone.x+w.rect.x-source.x)),y=Math.max(top,Math.min(bottom,zone.y+w.rect.y-source.y));
        if (w.integerPosition===true) {x=Math.round(x);y=Math.round(y);}
        var target={x:x,y:y,width:width,height:height};
        if(!contains(s.area,target)||!contains(zone,target)||close(target,w.rect)||result.some(function(r){return close(r.rect,target);}))return;
        result.push({rect:target,resize:width!==w.rect.width||height!==w.rect.height,limit:shrinkLimit});
    });});
    // Randomize equal alternatives without making search unbounded.
    for(var i=result.length-1;i>0;i--){var v=s.random();if(!finite(v)||v<0||v>=1)throw new Error("random must yield values in [0,1)");var j=Math.floor(v*(i+1)),t=result[i];result[i]=result[j];result[j]=t;}
    return result;
}
function proposeGroupRotation(windows,zoneRects,area,obstacles,options) {
    var s=setup(windows,zoneRects,area,obstacles,options), entries=s.windows.map(function(w){return {window:w,targets:candidates(s,w)};}).filter(function(e){return e.targets.length;});
    // Eligible input windows that cannot participate remain stationary blockers.
    s.windows.forEach(function(w){if(!entries.some(function(e){return e.window.identity===w.identity;}))s.obstacles.push(w.rect);});
    entries.sort(function(a,b){return a.targets.length-b.targets.length;});
    var best=[], selected=[], occupied=[], max=Math.min(s.max,entries.length);
    function search(i,moved) {
        if(!tick(s))return;
        if(i===entries.length){if(moved>best.length)best=selected.slice();return;}
        if(moved+(entries.length-i)<=best.length)return;
        var e=entries[i], w=e.window, choices=(moved<max?e.targets:[]).concat([{rect:w.rect,stay:true}]);
        for(var c=0;c<choices.length;c++){
            var choice=choices[c];
            if(!s.obstacles.some(function(o){return overlaps(o,choice.rect);})&&!occupied.some(function(o){return overlaps(o,choice.rect);})){
                occupied.push(choice.rect); if(!choice.stay){
                    var move={identity:w.identity,fromRect:copyRect(w.rect),toRect:copyRect(choice.rect)};
                    if(choice.resize){move.permitsRoundingResize=true;move.roundingResizeLimit=choice.limit;}
                    selected.push(move);
                }
                search(i+1,moved+(choice.stay?0:1));
                if(!choice.stay)selected.pop(); occupied.pop();
            }
            if(s.stopped||best.length===max)break;
        }
    }
    search(0,0);
    if(s.stopped)best=[];
    return {moves:best,stats:{evaluations:s.evaluations,candidates:s.windows.length,assignable:entries.length,bounded:s.stopped},
        reason:best.length?"group_rotation":(s.stopped?"search_stopped":"no_group_rotation")};
}
var api={proposeGroupRotation:proposeGroupRotation};
if(typeof module!=="undefined"&&module.exports)module.exports=api;

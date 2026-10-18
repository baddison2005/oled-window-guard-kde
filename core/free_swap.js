.pragma library

// Pure bounded search for mixed-size swaps and coordinated shift reordering.
var HARD_MAX_EVALUATIONS = 50000;
var EPSILON = 1e-9;
function finite(n) { return typeof n === "number" && isFinite(n); }
function validRect(r) { return !!r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0; }
function overlaps(a, b) { return a.x < b.x + b.width - 0.01 && b.x < a.x + a.width - 0.01 && a.y < b.y + b.height - 0.01 && b.y < a.y + a.height - 0.01; }
function contains(a, b) { return b.x >= a.x - EPSILON && b.y >= a.y - EPSILON && b.x + b.width <= a.x + a.width + EPSILON && b.y + b.height <= a.y + a.height + EPSILON; }
function close(a, b) { return Math.abs(a.x-b.x) < 0.5 && Math.abs(a.y-b.y) < 0.5 && Math.abs(a.width-b.width) < 0.5 && Math.abs(a.height-b.height) < 0.5; }
function result(moves, visited, reason) { return { moves: moves || [], stats: { visited: visited || 0 }, reason: reason || (moves && moves.length ? "candidate-found" : "no-arrangement") }; }
function chooseContext(windows, area, obstacles, options) {
    options = options || {};
    var max = options.maximumWindows === undefined ? windows.length : options.maximumWindows;
    var maxEval = options.maxEvaluations === undefined ? HARD_MAX_EVALUATIONS : options.maxEvaluations;
    var random = options.random || Math.random, now = options.now || function() { return Date.now(); };
    var duration = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    if (!Array.isArray(windows) || !validRect(area) || !Array.isArray(obstacles || []) || !finite(max) || max < 1 || Math.floor(max) !== max || !finite(maxEval) || maxEval < 1 || Math.floor(maxEval) !== maxEval || typeof random !== "function" || typeof now !== "function" || !finite(duration) || duration < 0 || (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function")) return { error: "invalid-input" };
    var start = now();
    if (!finite(start)) return { error: "invalid-clock" };
    return { options: options, maximum: max, cap: Math.min(HARD_MAX_EVALUATIONS, maxEval), random: random, now: now, deadline: start + duration, start: start, windows: windows.slice(), obstacles: obstacles || [] };
}
function canceled(c) {
    if (c.options.shouldCancel && c.options.shouldCancel()) return "canceled";
    var t = c.now();
    if (!finite(t)) return "invalid-clock";
    if (t > c.deadline) return "deadline";
    return null;
}
function rangeDistance(a, b, area, percent) {
    var rx = area.width * percent / 100, ry = area.height * percent / 100;
    if (!(rx > 0) || !(ry > 0)) return Infinity;
    var dx = (b.x-a.x)/rx, dy = (b.y-a.y)/ry;
    return Math.sqrt(dx*dx+dy*dy);
}
function coordinates(windows, area, w, axis) {
    var vals = [axis === "x" ? area.x : area.y];
    var span = axis === "x" ? area.width : area.height, size = axis === "x" ? w.rect.width : w.rect.height;
    vals.push((axis === "x" ? area.x : area.y) + span - size);
    // Alignment edges cover tight packing; quartiles give the search interior alternatives.
    for (var q=1;q<4;q++) vals.push(Math.round((axis === "x" ? area.x : area.y) + (span-size)*q/4));
    for (var i=0;i<windows.length;i++) {
        var r=windows[i].rect;
        if (axis === "x") vals.push(r.x, r.x+r.width-w.rect.width, r.x+r.width, r.x-w.rect.width);
        else vals.push(r.y, r.y+r.height-w.rect.height, r.y+r.height, r.y-w.rect.height);
    }
    vals = vals.filter(function(v){ return finite(v); });
    // Preserve exact area/source alignment edges. Rounding these can discard
    // the only safe packing of fractional logical frames on scaled outputs.
    // Interior quartile alternatives above retain whole-logical-pixel sampling.
    var seen=Object.create(null), out=[];
    for (i=0;i<vals.length;i++) { var n=vals[i], k=String(n); if (!seen[k]) { seen[k]=true; out.push(n); } }
    return out;
}
function gaussianShiftMagnitude(random) {
    var u=random(), angle=random();
    if (!finite(u)||u<0||u>=1||!finite(angle)||angle<0||angle>=1) throw new Error("random must yield values in [0,1)");
    if (u===0) u=1/4294967296;
    var normal=Math.sqrt(-2*Math.log(u))*Math.cos(2*Math.PI*angle);
    return Math.min(1,Math.max(0.2,0.6+normal*(0.8/3)));
}
function plan(windows, area, obstacles, options, coordinated) {
    windows = windows || []; options=options||{};
    var c=chooseContext(windows,area,obstacles||[],options);
    if (c.error) return result([],0,c.error);
    var candidates=c.windows;
    if (candidates.length < 2 || c.maximum < 2) return result([],0,"insufficient-windows");
    var rangePercent=options.shiftRangePercent;
    if (coordinated && (!finite(rangePercent)||rangePercent<=0)) return result([],0,"invalid-range");
    var allowX=options.enableHorizontalOrderChange===true, allowY=options.enableVerticalOrderChange===true;
    if (coordinated && !allowX && !allowY) return result([],0,"no-enabled-axis");
    var magnitude=0;
    if (coordinated) { try { magnitude=gaussianShiftMagnitude(c.random); } catch(e) { return result([],0,"invalid-random"); } if (magnitude < 0.6) return result([],0,"sample-too-small"); }
    var entries=candidates.map(function(w){ return { window:w, targets:[] }; }).sort(function(a,b){ return b.window.rect.width*b.window.rect.height-a.window.rect.width*a.window.rect.height; });
    var started={}; candidates.forEach(function(w){ if (!w || typeof w.identity !== "string" || !validRect(w.rect)) started["!"]=true; else started[w.identity]=true; });
    if (started["!"]) return result([],0,"invalid-window");
    for (var ei=0;ei<entries.length;ei++) {
        var w=entries[ei].window, xs=coordinates(candidates,area,w,"x"), ys=coordinates(candidates,area,w,"y"), targets=[];
        for(var xi=0;xi<xs.length;xi++) for(var yi=0;yi<ys.length;yi++) {
            var target={x:w.integerPosition===true?Math.round(xs[xi]):xs[xi],y:w.integerPosition===true?Math.round(ys[yi]):ys[yi],width:w.rect.width,height:w.rect.height};
            if(!contains(area,target)||close(target,w.rect)) continue;
            if(coordinated && rangeDistance(w.rect,target,area,rangePercent)>1+EPSILON) continue;
            // A swap destination may be occupied by another participant's
            // source frame; final placement and ordered steps validate safety.
            targets.push(target);
        }
        // Randomize equal choices while keeping runs reproducible with injected RNG.
        for(var ti=targets.length-1;ti>0;ti--){var rv=c.random();if(!finite(rv)||rv<0||rv>=1)return result([],0,"invalid-random");var jj=Math.floor(rv*(ti+1)),tmp=targets[ti];targets[ti]=targets[jj];targets[jj]=tmp;}
        entries[ei].targets=targets;
    }
    var obstaclesList=c.obstacles.slice(), moves=[], placed=[], placedIds=[], visited=0, best=null, goal=Math.min(c.maximum,entries.length), minMoves=2;
    function collision(id,rect,otherId,otherRect) {
        return typeof options.movementCollision==="function"?options.movementCollision(id,rect,otherId,otherRect):overlaps(rect,otherRect);
    }
    function validFinal() {
        var occupied=placed;
        for(var a=0;a<obstaclesList.length;a++) if(!validRect(obstaclesList[a])) return false;
        for(a=0;a<occupied.length;a++) {
            for(var b=0;b<obstaclesList.length;b++) if(overlaps(occupied[a],obstaclesList[b])) return false;
            for(b=a+1;b<occupied.length;b++) if(collision(placedIds[a],occupied[a],placedIds[b],occupied[b])) return false;
        }
        return true;
    }
    function orderAllowed() {
        var finals=Object.create(null);
        moves.forEach(function(m){finals[m.identity]=m.toRect;});
        var reversedX=false, reversedY=false;
        for(var i=0;i<candidates.length;i++) for(var j=i+1;j<candidates.length;j++) {
            var a=candidates[i].rect,b=candidates[j].rect,fa=finals[candidates[i].identity]||a,fb=finals[candidates[j].identity]||b;
            var hx=(a.x+a.width/2-b.x-b.width/2)*(fa.x+fa.width/2-fb.x-fb.width/2)<-1;
            var vy=(a.y+a.height/2-b.y-b.height/2)*(fa.y+fa.height/2-fb.y-fb.height/2)<-1;
            if(hx&&!allowX||vy&&!allowY)return false;
            reversedX=reversedX||hx; reversedY=reversedY||vy;
        }
        return (allowX && reversedX) || (allowY && reversedY);
    }
    function search(index) {
        if(visited>=c.cap||best&&best.length>=goal)return;
        if((visited&63)===0) { var stop=canceled(c); if(stop){c.stopReason=stop;return;} }
        visited++;
        if(index===entries.length){
            if(moves.length<minMoves||!validFinal())return;
            if(coordinated&&( !moves.some(function(m){return rangeDistance(m.fromRect,m.toRect,area,rangePercent)>=0.4-EPSILON;})||!orderAllowed()))return;
            best=moves.slice(); return;
        }
        var entry=entries[index], optionsFor=entry.targets.concat([entry.window.rect]);
        for(var z=0;z<optionsFor.length;z++){
            var target=optionsFor[z], isMoved=!close(entry.window.rect,target), skip=false;
            if(obstaclesList.some(function(o){return overlaps(o,target);})||placed.some(function(p,i){return collision(entry.window.identity,target,placedIds[i],p);}))continue;
            if(isMoved&&moves.length>=c.maximum)continue;
            placed.push(target); placedIds.push(entry.window.identity); if(isMoved)moves.push({identity:entry.window.identity,fromRect:entry.window.rect,toRect:target});
            search(index+1);
            if(isMoved)moves.pop(); placed.pop(); placedIds.pop();
            if(visited>=c.cap||best&&best.length>=goal||c.stopReason)break;
        }
    }
    search(0);
    return {moves:best||[],stats:{visited:visited,limit:c.cap,elapsedMs:Math.max(0,c.now()-c.start)},reason:best?"candidate-found":(c.stopReason|| (visited>=c.cap?"evaluation-limit":"no-arrangement"))};
}
function proposeFreeSwap(windows, area, obstacles, options) { return plan(windows,area,obstacles,options,false); }
function proposeCoordinatedShift(windows, area, obstacles, options) { return plan(windows,area,obstacles,options,true); }

// Pure proposals for similar-size swap cycles and rigid neighbour groups.
// Final bounds/collision validation and execution ordering belong to the caller.
var HARD_MAX_EVALUATIONS = 50000;
var MAX_CANDIDATES = 64;
function finite(n) { return typeof n === "number" && isFinite(n); }
function validRect(r) { return r && finite(r.x) && finite(r.y) && finite(r.width) && finite(r.height) && r.width > 0 && r.height > 0; }
function closeSize(a, b, tolerance) {
    return Math.abs(a.width - b.width) / Math.max(a.width, b.width) <= tolerance &&
        Math.abs(a.height - b.height) / Math.max(a.height, b.height) <= tolerance;
}
function contains(a, r) { return r.x >= a.x && r.y >= a.y && r.x + r.width <= a.x + a.width && r.y + r.height <= a.y + a.height; }
function randomIndex(random, n) {
    var value = random();
    if (!finite(value) || value < 0 || value >= 1) throw new Error("random must yield values in [0,1)");
    return Math.floor(value * n);
}
function setup(windows, area, options) {
    options = options || {};
    var max = options.maximumWindows, tolerance = options.sizeTolerance;
    if (!Array.isArray(windows) || !validRect(area) || !finite(max) || Math.floor(max) !== max || max < 1 || max > 12 ||
            !finite(tolerance) || tolerance < 0 || tolerance > 0.25) throw new Error("invalid swap cycle inputs");
    var random = options.random || Math.random, now = options.now || function() { return Date.now(); };
    if (typeof random !== "function" || typeof now !== "function" || (options.shouldCancel !== undefined && typeof options.shouldCancel !== "function"))
        throw new Error("invalid planner callback");
    var budget = options.maxEvaluations === undefined ? 2400 : options.maxEvaluations;
    var duration = options.deadlineMs === undefined ? 30 : options.deadlineMs;
    if (!finite(budget) || Math.floor(budget) !== budget || budget < 1 || !finite(duration) || duration < 0) throw new Error("invalid search budget");
    var start = now();
    if (!finite(start)) throw new Error("planner clock must be finite");
    // Bound preprocessing as well as the search, even if the caller hands us a
    // pathological snapshot with far more candidates than can enter one plan.
    return { windows: windows.slice(0, MAX_CANDIDATES).filter(function(w) { return w && typeof w.identity === "string" && validRect(w.rect) && contains(area, w.rect); }),
        area: area, max: max, tolerance: tolerance, random: random, now: now,
        budget: Math.min(budget, HARD_MAX_EVALUATIONS), deadline: start + duration, start: start,
        shouldCancel: options.shouldCancel || function() { return false; }, evaluations: 0, cancelled: false };
}
function tick(s) {
    if (s.evaluations >= s.budget || s.now() > s.deadline || s.shouldCancel()) { s.cancelled = true; return false; }
    s.evaluations++; return true;
}
function cleanCoordinate(n) { return Math.abs(n-Math.round(n))<1e-9?Math.round(n):n; }
function rectAt(w, x, y) { return { x: w.integerPosition===true?Math.round(x):cleanCoordinate(x), y: w.integerPosition===true?Math.round(y):cleanCoordinate(y), width: w.rect.width, height: w.rect.height }; }
function movesForCycle(items) {
    return items.map(function(w, i) { var target = items[(i + 1) % items.length].rect; var move = {
        identity: w.identity, fromRect: { x: w.rect.x, y: w.rect.y, width: w.rect.width, height: w.rect.height },
        toRect: rectAt(w, target.x, target.y)
    };
        // Independently rounded thirds can differ by one pixel. Fit the actual
        // destination slot, including X11 position alignment, before validation.
        var to=move.toRect;
        if (w.integerPosition===true) {to.x=Math.ceil(target.x);to.y=Math.ceil(target.y);}
        var slotWidth=target.x+target.width-to.x,slotHeight=target.y+target.height-to.y;
        var width=Math.abs(slotWidth-to.width)<=2?slotWidth:Math.min(to.width,slotWidth);
        var height=Math.abs(slotHeight-to.height)<=2?slotHeight:Math.min(to.height,slotHeight);
        width=cleanCoordinate(width);height=cleanCoordinate(height);
        if (w.integerPosition===true) {width=Math.floor(width);height=Math.floor(height);}
        if (w.resizable===true && w.minimumSize!==null && (!w.minimumSize || width>=w.minimumSize.width && height>=w.minimumSize.height) && width>0 && height>0 && Math.abs(w.rect.width-width)<=2 && Math.abs(w.rect.height-height)<=2 &&
                (width!==to.width || height!==to.height)) {
            to.width=width;to.height=height;move.permitsRoundingResize=true;
        }
        return move;
    });
}
function proposeSimilarCycles(windows, area, options) {
    var s = setup(windows, area, options), best = [], groups = [];
    s.windows.forEach(function(w) {
        var group = groups.filter(function(g) { return g.every(function(m) { return closeSize(w.rect, m.rect, s.tolerance); }); })[0];
        if (!group) { group = []; groups.push(group); } group.push(w);
    });
    for (var g = 0; g < groups.length && !s.cancelled; g++) {
        var pool = groups[g];
        if (pool.length < 2) continue;
        for (var attempt = 0; attempt < 40 && !s.cancelled; attempt++) {
            if (!tick(s)) break;
            var ordered = pool.slice();
            for (var i = ordered.length - 1; i > 0; i--) { var j = randomIndex(s.random, i + 1), t = ordered[i]; ordered[i] = ordered[j]; ordered[j] = t; }
            var cycle = ordered.slice(0, Math.min(ordered.length, s.max));
            if (cycle.length >= 2 && cycle.every(function(w, i) { return Math.abs(w.rect.x - cycle[(i + 1) % cycle.length].rect.x) > 0.01 || Math.abs(w.rect.y - cycle[(i + 1) % cycle.length].rect.y) > 0.01; })) {
                best = movesForCycle(cycle); break;
            }
        }
        if (best.length) break;
    }
    return { moves: best, stats: { evaluations: s.evaluations, candidates: s.windows.length, bounded: s.cancelled }, reason: best.length ? "similar_size_cycle" : (s.cancelled ? "search_stopped" : "no_similar_cycle") };
}
function adjacent(a, b) {
    var gap = 8, vertical = a.y < b.y + b.height && b.y < a.y + a.height;
    var horizontal = a.x < b.x + b.width && b.x < a.x + a.width;
    return (vertical && Math.max(0, Math.max(a.x - (b.x + b.width), b.x - (a.x + a.width))) <= gap) ||
        (horizontal && Math.max(0, Math.max(a.y - (b.y + b.height), b.y - (a.y + a.height))) <= gap);
}
function makeGroups(s) {
    var left = s.windows.slice(), groups = [];
    while (left.length) {
        var group = [left.shift()], changed = true;
        while (changed) {
            changed = false;
            for (var i = left.length - 1; i >= 0; i--) {
                if (group.some(function(m) { return closeSize(m.rect, left[i].rect, s.tolerance) && adjacent(m.rect, left[i].rect); })) {
                    group.push(left.splice(i, 1)[0]); changed = true;
                }
            }
        }
        groups.push(group);
    }
    return groups;
}
function groupBounds(group) {
    var left=Infinity,top=Infinity,right=-Infinity,bottom=-Infinity;
    group.forEach(function(w){left=Math.min(left,w.rect.x);top=Math.min(top,w.rect.y);right=Math.max(right,w.rect.x+w.rect.width);bottom=Math.max(bottom,w.rect.y+w.rect.height);});
    return {x:left,y:top,width:right-left,height:bottom-top};
}
// Parallel columns/rows may have different starting vertical/horizontal anchors.
// Their empty perpendicular space is usable by a taller/wider incoming group.
// Rounded shared seams become one boundary; genuine gaps retain their spacing.
function groupedSlots(bounds, area) {
    function separated(position,size) {
        return bounds.every(function(a,i){return bounds.every(function(b,j){return i===j || Math.min(a[position]+a[size],b[position]+b[size])-Math.max(a[position],b[position])<=2+1e-9;});});
    }
    var columns=separated("x","width"),rows=separated("y","height");
    var slots=bounds.map(function(r){return {x:r.x,y:r.y,width:r.width,height:r.height};});
    if (!columns && !rows) return slots;
    var position=columns?"x":"y",size=columns?"width":"height",other=columns?"y":"x",otherSize=columns?"height":"width";
    slots.forEach(function(r){r[other]=area[other];r[otherSize]=area[otherSize];});
    var order=bounds.map(function(_,i){return i;}).sort(function(a,b){return bounds[a][position]-bounds[b][position];});
    for (var i=1;i<order.length;i++) {
        var before=slots[order[i-1]],after=slots[order[i]],end=before[position]+before[size],start=after[position];
        if (Math.abs(end-start)>2+1e-9) continue;
        var seam=(end+start)/2;
        if (Math.abs(end-Math.round(end))<1e-9 && Math.abs(start-Math.round(start))<1e-9) seam=Math.round(seam);
        seam=cleanCoordinate(seam);
        var afterEnd=after[position]+after[size];
        before[size]=seam-before[position];after[position]=seam;after[size]=afterEnd-seam;
    }
    return slots;
}
function fitGroupedAt(group, from, x, y, width, height) {
    var moves=[];
    for (var i=0;i<group.length;i++) {
        var w=group[i],r=w.rect;
        var left=cleanCoordinate(x+(r.x-from.x)*width/from.width),top=cleanCoordinate(y+(r.y-from.y)*height/from.height);
        var right=cleanCoordinate(x+(r.x+r.width-from.x)*width/from.width),bottom=cleanCoordinate(y+(r.y+r.height-from.y)*height/from.height);
        if (w.integerPosition===true) {left=Math.round(left);top=Math.round(top);right=Math.round(right);bottom=Math.round(bottom);}
        var to={x:left,y:top,width:cleanCoordinate(right-left),height:cleanCoordinate(bottom-top)};
        if (!validRect(to) || Math.abs(to.width-r.width)>2+1e-9 || Math.abs(to.height-r.height)>2+1e-9) return null;
        var resized=to.width!==r.width || to.height!==r.height;
        if (resized && (w.resizable!==true || w.minimumSize===null || w.minimumSize && (to.width<w.minimumSize.width || to.height<w.minimumSize.height))) return null;
        // An assigned group moves as a whole, preserving its internal order.
        if (to.x===r.x && to.y===r.y && to.width===r.width && to.height===r.height) return null;
        var move={identity:w.identity,fromRect:r,toRect:to};
        if (resized) move.permitsRoundingResize=true;
        moves.push(move);
    }
    return moves;
}
function proposeGroupedCycles(windows, area, options) {
    var s=setup(windows,area,options),groups=makeGroups(s),bounds=groups.map(groupBounds),slots=groupedSlots(bounds,area);
    var best=[],selected=[],occupied=[],used=Object.create(null),cache=Object.create(null),obstacles=options.obstacles || [];
    var goal=Math.min(s.max,s.windows.length),order=groups.map(function(_,i){return i;}),accepted=0;
    function shuffle(values) {
        for(var i=values.length-1;i>0;i--){var j=randomIndex(s.random,i+1),t=values[i];values[i]=values[j];values[j]=t;}return values;
    }
    function collision(id,r,otherId,other) {
        if (typeof options.movementCollision==="function") return options.movementCollision(id,r,otherId,other);
        return Math.min(r.x+r.width,other.x+other.width)-Math.max(r.x,other.x)>0.01 && Math.min(r.y+r.height,other.y+other.height)-Math.max(r.y,other.y)>0.01;
    }
    function candidates(index,slotIndex) {
        var key=index+":"+slotIndex;if(cache[key])return cache[key];
        var group=groups[index],from=bounds[index],slot=slots[slotIndex],integral=group.some(function(w){return w.integerPosition===true;});
        var left=integral?Math.ceil(slot.x):slot.x,top=integral?Math.ceil(slot.y):slot.y;
        var right=integral?Math.floor(slot.x+slot.width):slot.x+slot.width,bottom=integral?Math.floor(slot.y+slot.height):slot.y+slot.height;
        var width=Math.abs(right-left-from.width)<=2?right-left:Math.min(from.width,right-left);
        var height=Math.abs(bottom-top-from.height)<=2?bottom-top:Math.min(from.height,bottom-top);
        if (!(width>0 && height>0) || Math.abs(width-from.width)>2+1e-9 || Math.abs(height-from.height)>2+1e-9) return cache[key]=[];
        var xs=[Math.max(left,Math.min(right-width,bounds[slotIndex].x)),left,right-width];
        var ys=[Math.max(top,Math.min(bottom-height,bounds[slotIndex].y)),top,bottom-height];
        var choices=[],seen=Object.create(null);
        for(var xi=0;xi<xs.length && !s.cancelled;xi++)for(var yi=0;yi<ys.length && !s.cancelled;yi++) {
            var x=cleanCoordinate(xs[xi]),y=cleanCoordinate(ys[yi]);if(integral){x=Math.round(x);y=Math.round(y);}
            var anchor=x+":"+y;if(seen[anchor])continue;seen[anchor]=true;
            if(!tick(s))break;
            var moves=fitGroupedAt(group,from,x,y,width,height);
            // Filling a one-pixel gap is optional. A fixed/unknown-size client
            // may still move without that increase when the original size fits.
            if(!moves && (width>from.width || height>from.height))
                moves=fitGroupedAt(group,from,x,y,Math.min(width,from.width),Math.min(height,from.height));
            if(!moves || moves.some(function(m){return !contains(area,m.toRect) || !contains(slot,m.toRect);}))continue;
            choices.push(moves);
        }
        return cache[key]=choices;
    }
    function safeAgainst(moves,others) {
        return moves.every(function(m){return others.every(function(o){return !collision(m.identity,m.toRect,o.identity,o.rect);});});
    }
    function search(depth,movedGroups) {
        if(s.cancelled || best.length>=goal || !tick(s))return;
        if(depth===order.length) {
            if(movedGroups<2 || selected.length<=best.length)return;
            if(typeof options.acceptMoves==="function" && !options.acceptMoves(selected))return;
            best=selected.slice();accepted++;return;
        }
        var index=order[depth],group=groups[index],targets=shuffle(order.filter(function(i){return i!==index;})).concat([index]);
        for(var t=0;t<targets.length && !s.cancelled && best.length<goal;t++) {
            var slotIndex=targets[t];if(used[slotIndex])continue;
            var stay=slotIndex===index;
            if(!stay && selected.length+group.length>s.max)continue;
            var choices=stay?[[]]:candidates(index,slotIndex);
            for(var c=0;c<choices.length && !s.cancelled && best.length<goal;c++) {
                var moves=choices[c],frames=stay?group.map(function(w){return {identity:w.identity,rect:w.rect};}):moves.map(function(m){return {identity:m.identity,rect:m.toRect};});
                var probe=frames.map(function(w){return {identity:w.identity,toRect:w.rect};});
                if(!safeAgainst(probe,occupied) || !safeAgainst(probe,obstacles))continue;
                used[slotIndex]=true;
                var count=selected.length,previous=occupied.length;
                selected=selected.concat(moves);occupied=occupied.concat(frames);
                search(depth+1,movedGroups+(stay?0:1));
                selected.length=count;occupied.length=previous;delete used[slotIndex];
            }
        }
    }
    // A single connected block cannot swap with itself. With no neighbouring
    // block the caller retains ordinary similar/free-swap behaviour.
    if(groups.length>1 && groups.some(function(g){return g.length>1;}))search(0,0);
    if(s.shouldCancel())best=[];
    return {moves:best,stats:{evaluations:s.evaluations,candidates:s.windows.length,groups:groups.length,accepted:accepted,bounded:s.cancelled},
        reason:best.length?"grouped_arrangement":s.cancelled?"search_stopped":"no_grouped_cycle"};
}
var api = { proposeSimilarCycles: proposeSimilarCycles, proposeGroupedCycles: proposeGroupedCycles,
    closeSize: closeSize, adjacent: adjacent };
if (typeof module !== "undefined" && module.exports) module.exports = api;

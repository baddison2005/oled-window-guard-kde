.pragma library

// Logical compositor coordinates (Y increases downwards). Pick once per warning;
// the presenter retains the result through placement confirmation and countdown.
function rectangle(r) {
    return r && ["x","y","width","height"].every(function(k) {
        return typeof r[k]==="number" && isFinite(r[k]);
    }) && r.width>0 && r.height>0 && isFinite(r.x+r.width) && isFinite(r.y+r.height);
}
function geometry(usable, screen, position, randomX, randomY) {
    if (!rectangle(usable) || !rectangle(screen) ||
            usable.x<screen.x-1 || usable.y<screen.y-1 ||
            usable.x+usable.width>screen.x+screen.width+1 || usable.y+usable.height>screen.y+screen.height+1 ||
            ["topLeft","topCenter","topRight","random"].indexOf(position)<0) return null;
    // Fractional scaling can round a work-area edge outward by a logical pixel.
    // Intersect those edges before choosing coordinates; never paint off-screen.
    var x0=Math.max(usable.x,screen.x), y0=Math.max(usable.y,screen.y);
    usable={x:x0,y:y0,width:Math.min(usable.x+usable.width,screen.x+screen.width)-x0,
        height:Math.min(usable.y+usable.height,screen.y+screen.height)-y0};
    if (usable.width<264 || usable.height<150) return null;
    var width=Math.floor(Math.min(390,usable.width-24)), height=126;
    var first=Math.ceil(usable.x), last=Math.floor(usable.x+usable.width-width);
    var inset=Math.min(12,Math.floor((last-first)/2)), left=first+inset, right=last-inset;
    var highest=Math.ceil(usable.y), lowest=Math.floor(usable.y+usable.height-height);
    var top=Math.min(highest+12,lowest), x, y=top;
    if (position==="topLeft") x=left;
    else if (position==="topRight") x=right;
    else if (position==="topCenter") x=Math.round((left+right)/2);
    else {
        if (typeof randomX!=="number" || !isFinite(randomX) || randomX<0 || randomX>1 ||
                typeof randomY!=="number" || !isFinite(randomY) || randomY<0 || randomY>1) return null;
        var bandLimit=Math.floor(screen.y+screen.height/4-height);
        var start=Math.max(highest,Math.min(top,bandLimit));
        var end=Math.max(start,Math.min(lowest,bandLimit));
        x=Math.round(left+randomX*(right-left));
        y=Math.round(start+randomY*(end-start));
    }
    return {x:x,y:y,width:width,height:height};
}

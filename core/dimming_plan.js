.pragma library

const MAX_PIECES = 2048;
const EPSILON = 0;

function validRect(rect) {
  return rect && Number.isFinite(rect.x) && Number.isFinite(rect.y) &&
    Number.isFinite(rect.width) && Number.isFinite(rect.height) &&
    rect.width > 0 && rect.height > 0 &&
    Number.isFinite(rect.x + rect.width) && Number.isFinite(rect.y + rect.height);
}
function right(r) { return r.x + r.width; }
function bottom(r) { return r.y + r.height; }
function intersection(a, b) {
  if (!validRect(a) || !validRect(b)) return null;
  const x = Math.max(a.x, b.x), y = Math.max(a.y, b.y);
  const r = Math.min(right(a), right(b)), bt = Math.min(bottom(a), bottom(b));
  return r > x && bt > y ? { x, y, width: r - x, height: bt - y } : null;
}
function overlaps(a, b) { return intersection(a, b) !== null; }

/** Subtract cut from rect, returning non-overlapping top, bottom, left and right pieces. */
function subtract(rect, cut) {
  if (!validRect(rect)) return [];
  const i = intersection(rect, cut);
  if (!i) return [{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }];
  return [
    { x: rect.x, y: rect.y, width: rect.width, height: i.y - rect.y },
    { x: rect.x, y: bottom(i), width: rect.width, height: bottom(rect) - bottom(i) },
    { x: rect.x, y: i.y, width: i.x - rect.x, height: i.height },
    { x: right(i), y: i.y, width: right(rect) - right(i), height: i.height },
  ].filter(validRect);
}
function subtractMany(pieces, cuts) {
  for (const cut of cuts) {
    const next = [];
    for (const piece of pieces) {
      const fragments = subtract(piece, cut);
      for (const fragment of fragments) next.push(fragment);
      if (next.length > MAX_PIECES) return null;
    }
    pieces = next;
    if (pieces.length > MAX_PIECES) return null;
  }
  return pieces;
}
function closeRect(a, b, tolerance = 2) {
  return !!a && !!b && Math.abs(a.x - b.x) <= tolerance && Math.abs(a.y - b.y) <= tolerance &&
    Math.abs(a.width - b.width) <= tolerance && Math.abs(a.height - b.height) <= tolerance;
}
function clampAmount(n) { return Number.isFinite(n) ? Math.max(0, Math.min(1, n)) : 0; }

/**
 * Produce black-mask regions for one display. Windows must be front-to-back.
 * Rectangles use logical coordinates and may be fractional or negative.
 * A focused rectangle overlapping the display suppresses whole-display dimming;
 * in window mode that rectangle is subtracted from every candidate mask.
 */
function regions(options) {
  options = options || {};
  const display = options.display, windows = options.windows || [], focused = options.focused || null;
  const windowAmount = options.windowAmount || 0, displayAmount = options.displayAmount || 0;
  const displayProgress = options.displayProgress === undefined ? 1 : options.displayProgress;
  if (!validRect(display)) return [];
  if (!Array.isArray(windows) || (focused !== null && !validRect(focused)) ||
      windows.some(window => !window || !validRect(window.frame) || typeof window.dimmable !== 'boolean')) return [];
  const wa = clampAmount(windowAmount), da = clampAmount(displayAmount);
  const progress = clampAmount(displayProgress);
  const whole = da > 0 && !overlaps(focused, display);
  if (whole) {
    let pieces = [ { x:display.x, y:display.y, width:display.width, height:display.height } ];
    const occluders = [];
    for (const window of windows) {
      if (!window.dimmable && window.displayDimmable!==true) {
        let visible = intersection(window.frame, display) ? [intersection(window.frame, display)] : [];
        visible = subtractMany(visible, occluders);
        if (!visible) return [];
        pieces = subtractMany(pieces, visible);
        if (!pieces) return [];
      }
      if (validRect(window.frame)) occluders.push(window.frame);
    }
    if (progress >= 1) return pieces.map(frame => ({ frame, amount: da }));
    const underlay = regions({ display, windows, focused, windowAmount: wa, displayAmount: 0 });
    let remaining = pieces;
    const blended = [];
    for (const region of underlay) {
      remaining = subtractMany(remaining, [region.frame]);
      if (!remaining) return [];
      blended.push({ frame: region.frame, amount: region.amount * (1 - progress) + da * progress });
    }
    return blended.concat(remaining.map(frame => ({ frame, amount: da * progress })));
  }
  if (wa <= 0) return [];
  const occluders = [];
  const result = [];
  for (const window of windows) {
    const clipped = intersection(window.frame, display);
    if (clipped && window.dimmable && window.ready !== false && !closeRect(focused, window.frame)) {
      const cuts = occluders.concat(validRect(focused) ? [focused] : []);
      const pieces = subtractMany([clipped], cuts);
      if (!pieces) return [];
      const amount = wa * clampAmount(window.fadeProgress === undefined ? 1 : window.fadeProgress);
      for (const frame of pieces) result.push({ frame, amount });
      if (result.length > MAX_PIECES) return [];
    }
    if (validRect(window.frame)) occluders.push(window.frame);
  }
  return result;
}

/** Plan selected displays; each display is {id, frame}; returns [{id, regions}]. */
function plan(options) {
  options = options || {};
  const displays = options.displays || [], selectedOutputIds = options.selectedOutputIds === undefined ? null : options.selectedOutputIds;
  const windows = options.windows || [], focused = options.focused || null;
  const windowAmount = options.windowAmount || 0, displayAmount = options.displayAmount || 0;
  const displayProgress = options.displayProgress === undefined ? 1 : options.displayProgress;
  const selected = selectedOutputIds == null ? null : new Set(selectedOutputIds);
  return displays.filter(display => selected === null || selected.has(display.id)).map(display => ({
    id: display.id,
    regions: regions({ display: display.frame, windows, focused, windowAmount, displayAmount, displayProgress }),
  }));
}

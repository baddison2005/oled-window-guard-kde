'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm');
const context = {};
vm.runInNewContext(fs.readFileSync('core/dimming_plan.js','utf8').replace(/^\.pragma library\s*/m,'') +
  '\nthis.api={subtract,overlaps,regions,plan};', context);
const { subtract, overlaps } = context.api;
const regions = options => JSON.parse(JSON.stringify(context.api.regions(options)));
const plan = options => JSON.parse(JSON.stringify(context.api.plan(options)));
const plain = value => JSON.parse(JSON.stringify(value));

const screen = { x: 0, y: 0, width: 1000, height: 600 };
const area = rs => rs.reduce((sum, r) => sum + r.frame.width * r.frame.height, 0);
const window = (frame, options = {}) => ({ frame, dimmable: true, ...options });

test('rectangle subtraction yields disjoint exact pieces including fractional geometry', () => {
  const pieces = plain(subtract({ x: -2.5, y: 0.25, width: 10, height: 8 }, { x: 1.25, y: 2.5, width: 2, height: 3 }));
  assert.equal(pieces.length, 4);
  assert.equal(pieces.reduce((n, r) => n + r.width * r.height, 0), 74);
  for (let i = 0; i < pieces.length; i++) for (let j = i + 1; j < pieces.length; j++) assert.equal(overlaps(pieces[i], pieces[j]), false);
});

test('focused content and front windows remain clear and window masks do not overlap', () => {
  const front = { x: 200, y: 100, width: 300, height: 300 };
  const rs = regions({ display: screen, windows: [window(front), window(screen)], focused: front, windowAmount: .3 });
  assert.equal(area(rs), 510000);
  assert.ok(rs.every(r => !overlaps(r.frame, front)));
  const background = regions({ display: screen, windows: [window({ x: 100, y: 100, width: 300, height: 200 }), window(screen)], windowAmount: .3 });
  assert.equal(area(background), 600000);
  for (let i = 0; i < background.length; i++) for (let j = i + 1; j < background.length; j++) assert.equal(overlaps(background[i].frame, background[j].frame), false);
});

test('whole display dimming takes precedence and cuts out visible non-dimmable controls', () => {
  const controls = { x: 0, y: 0, width: 100, height: 100 };
  const rs = regions({ display: screen, windows: [window(controls, { dimmable: false }), window(screen)], focused: { x: -500, y: 0, width: 400, height: 600 }, windowAmount: .3, displayAmount: .4 });
  assert.equal(area(rs), 590000);
  assert.ok(rs.every(r => r.amount === .4 && !overlaps(r.frame, controls)));
});

test('a focused window spanning the display blocks its whole-display mask', () => {
  assert.deepEqual(regions({ display: screen, focused: { x: -200, y: 50, width: 400, height: 200 }, displayAmount: .4 }), []);
  const left = regions({ display: { x: -1000, y: 0, width: 1000, height: 600 }, focused: { x: -200, y: 50, width: 400, height: 200 }, displayAmount: .4 });
  assert.deepEqual(left, []);
});

test('negative display origins clip windows and preserve logical geometry', () => {
  const display = { x: -1000, y: -600, width: 1000, height: 600 };
  const rs = regions({ display, windows: [window({ x: -1100, y: -700, width: 500, height: 400 })], windowAmount: .3 });
  assert.equal(area(rs), 120000);
  assert.ok(rs.every(r => r.frame.x >= display.x && r.frame.y >= display.y && r.frame.x + r.frame.width <= 0 && r.frame.y + r.frame.height <= 0));
});

test('selected outputs alone receive plans', () => {
  const result = plan({ displays: [{ id: 'a', frame: screen }, { id: 'b', frame: { x: 1000, y: 0, width: 500, height: 500 } }], selectedOutputIds: ['b'], displayAmount: .4 });
  assert.deepEqual(result.map(x => x.id), ['b']);
  assert.deepEqual(result[0].regions, [{ frame: { x: 1000, y: 0, width: 500, height: 500 }, amount: .4 }]);
});
test('invalid focus or window geometry fails bright', () => {
  assert.deepEqual(regions({display:screen,focused:{},displayAmount:.4}),[]);
  assert.deepEqual(regions({display:screen,windows:[{dimmable:true,frame:{}}],displayAmount:.4}),[]);
});

test('dock stays bright in window mode and participates in whole-display dimming',()=>{
 const display={x:0,y:0,width:500,height:300};
 const dock={frame:{x:0,y:280,width:500,height:20},dimmable:false,displayDimmable:true};
 assert.equal(regions({display,windows:[dock],windowAmount:.4}).length,0);
 const result=regions({display,windows:[dock],displayAmount:.5});
 assert.equal(result.length,1);assert.deepEqual(result[0],{frame:display,amount:.5});
});

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Compatibility = require('../../core/native_compatibility.js');
const valid = 'KWin version: 6.7.5\nQt Version: 6.11.2\nQt compile version: 6.11.2\n';
assert(Compatibility.compatible(valid, '6.7.5', '6.11.2'));
const invalid = [null, '', {}, valid.replace('6.7.5', '6.8.2'),
    valid.replace('Qt Version: 6.11.2', 'Qt Version: 6.12.0'),
    valid + 'KWin version: 6.7.5\n', valid.replace('6.7.5', '6.7.5-dev'),
    'KWin version: 6.7.5\n', 'x'.repeat(262145)];
for (const value of invalid) assert(!Compatibility.compatible(value, '6.7.5', '6.11.2'));
assert(!Compatibility.compatible(valid, '', ''));
const current=valid.replace('6.7.5','6.8.2');
assert(Compatibility.compatible(current,'6.8.2','6.11.2'));
assert(!Compatibility.compatible(valid,'6.8.2','6.11.2'));
assert(!Compatibility.compatible(current.replace('6.8.2','6.9.0'),'6.8.2','6.11.2'));

// Exercise the actual QML reply handler with a spy in place of the ELF loader.
const qml = fs.readFileSync(path.join(__dirname, '../../packages/kwin-script/contents/ui/NativeObservation.qml'), 'utf8');
const body = qml.match(/onFinished: function\(values\) \{([\s\S]*?)\n        \}\n        onFailed:/)[1];
const reply = new Function('consumer', 'Compatibility', 'values', body);
for (const values of [null, [], [null], [''], [valid.replace('6.7.5', '6.8.2')], [valid, valid]]) {
    const consumer = {compatibilityPending:true, expectedKwin:'6.7.5', expectedQt:'6.11.2',
        loadCompatibleObserver() {throw Error('unsafe import');}};
    reply(consumer, Compatibility, values);
    assert.equal(consumer.compatibilityPending, false);
    assert.match(consumer.loadError, /compatible OWG build/);
}
let loads = 0;
const consumer = {compatibilityPending:true, expectedKwin:'6.7.5', expectedQt:'6.11.2', loadCompatibleObserver() {loads++;}};
reply(consumer, Compatibility, [valid]);
reply(consumer, Compatibility, [valid]);
assert.equal(loads, 1);
consumer.compatibilityPending = false; // timeout/failure already closed the gate
reply(consumer, Compatibility, [valid]);
assert.equal(loads, 1);
assert(qml.includes('Component.onCompleted: compatibilityCall.call()'));
console.log('native compatibility: mismatch, unknown, duplicate, late reply and matching load passed');

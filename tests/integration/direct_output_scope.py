"""Physical keyboard attribution matrix using only an owned private client."""
import json
import math
from pathlib import Path
import subprocess
import time

from direct_vt import output_geometry, transitions
from direct_warning import publish_instruction, scripting
from run_input_direct import KINDS, ROOT, environment, snapshot, verify_continuity, verify_environment

PREFIX = 'OWG_DIRECT_SCOPE '


def intersections(frame, outputs):
    return {row['id'] for row in outputs
            if frame['x'] < row['x'] + row['width'] and row['x'] < frame['x'] + frame['width']
            and frame['y'] < row['y'] + row['height'] and row['y'] < frame['y'] + frame['height']}


def contained_stages(outputs, centered=False, top_inset=80):
    if len(outputs) != 2 or len({row['id'] for row in outputs}) != 2:
        raise RuntimeError('Exactly two distinct enabled physical outputs are required')
    rows = sorted(outputs, key=lambda row: (not row['id'].startswith('DP-'), row['id']))
    result = []
    for row in rows:
        if row['width'] < 800 or row['height'] < max(500, top_inset + 400):
            raise RuntimeError('Output is too small for the owned instructions')
        target = dict(x=row['x'] + (math.floor((row['width'] - 660) / 2) if centered else 80),
                      y=row['y'] + top_inset, width=660, height=380)
        if intersections(target, rows) != {row['id']}:
            raise RuntimeError('Contained test window would overlap another output')
        result.append({'name': row['id'], 'frame': target, 'expectedOutputs': [row['id']]})
    return result


def plan(outputs):
    result = contained_stages(outputs)
    rows = sorted(outputs, key=lambda row: (not row['id'].startswith('DP-'), row['id']))
    a, b = rows
    target = None
    for left, right in ((a, b), (b, a)):
        lo, hi = max(left['y'], right['y']), min(left['y'] + left['height'], right['y'] + right['height'])
        if abs(left['x'] + left['width'] - right['x']) < .01 and hi - lo >= 420:
            target = dict(x=right['x'] - 330, y=lo + 20, width=660, height=380)
            break
        lo, hi = max(left['x'], right['x']), min(left['x'] + left['width'], right['x'] + right['width'])
        if abs(left['y'] + left['height'] - right['y']) < .01 and hi - lo >= 700:
            target = dict(x=lo + 20, y=right['y'] - 190, width=660, height=380)
            break
    if target is None or intersections(target, rows) != {a['id'], b['id']}:
        raise RuntimeError('Outputs need an adjacent shared edge for the spanning test; no layout is changed')
    result.append({'name': 'both monitors', 'frame': target, 'expectedOutputs': sorted([a['id'], b['id']])})
    return result


def validate_frame(record, stage, outputs):
    frame = record.get('frame', {})
    if (record.get('focusOwned') is not True or
            any(type(frame.get(k)) not in (int, float) or not math.isfinite(frame[k])
                or abs(frame[k] - stage['frame'][k]) > 1 for k in ('x', 'y', 'width', 'height')) or
            intersections(frame, outputs) != set(stage['expectedOutputs'])):
        raise RuntimeError('Owned focus/frame readback does not match the keyboard stage')
    return frame


def check_log(log, index):
    if PREFIX + 'FAIL ' in log:
        raise RuntimeError('Owned scope fixture failed: ' + next(line for line in log.splitlines() if PREFIX + 'FAIL ' in line))
    marker = PREFIX + 'READY ' + str(index) + ' '
    lines = [line.split(marker, 1)[1] for line in log.splitlines() if marker in line]
    if len(lines) > 1:
        raise RuntimeError('Duplicate scope readiness record')
    return json.loads(lines[0]) if lines else None


def output_counts(state):
    rows = state.get('outputs', [])
    if (len(rows) != 2 or len({row.get('id') for row in rows}) != 2 or
            any(not row.get('id') or type(row.get('events')) is not int or row['events'] < 0 for row in rows)):
        raise RuntimeError('Invalid two-output activity counters')
    return {row['id']: row['events'] for row in rows}


class NonKeyboardInput(RuntimeError):
    pass


def keyboard_result(before, after, origin_before, origin_after, clients, expected, *, require_client=True):
    verify_continuity(before, after)
    if type(clients) is not int or clients < 0:
        raise RuntimeError('Invalid client keyboard notifications')
    old, new = output_counts(before), output_counts(after)
    if old.keys() != new.keys() or not expected or not set(expected) <= old.keys():
        raise RuntimeError('Keyboard output identities changed or expected scope is invalid')
    for counts in (before, after, origin_before['libinput'], origin_after['libinput']):
        if any(type(counts.get(k)) is not int or counts[k] < 0 for k in KINDS):
            raise RuntimeError('Invalid input counts')
    delta = {k: after[k] - before[k] for k in KINDS}
    physical = {k: origin_after['libinput'][k] - origin_before['libinput'][k] for k in KINDS}
    if any(n < 0 for n in (*delta.values(), *physical.values())):
        raise RuntimeError('Input counter regressed')
    if any(delta[k] or physical[k] for k in ('pointer', 'button', 'axis')):
        raise NonKeyboardInput('Non-keyboard input requires a fresh keyboard-only baseline')
    keys = delta['keyboard']
    if keys < 2 or physical['keyboard'] < 2 or (require_client and clients <= 0):
        return None
    if keys != physical['keyboard']:
        raise RuntimeError('Keyboard events are not entirely physical libinput-origin events')
    scope = {name: new[name] - old[name] for name in old}
    if scope != {name: keys if name in expected else 0 for name in old}:
        raise RuntimeError('Physical keyboard output attribution does not match the owned frame')
    return {'observerKeyboard': keys, 'libinputKeyboard': physical['keyboard'],
            'clientKeyboardNotifications': clients, 'outputEventDeltas': scope}


def coherent_state(env, include_trace=False):
    first = snapshot(env)
    facts = environment(env)
    trace = transitions(env)
    last = snapshot(env)
    fields = ('epoch', 'sequence', 'topologyGeneration', 'sessionGeneration', 'focusGeneration')
    if any(first.get(k) != last.get(k) for k in fields):
        return None
    if trace.get('schema') != 1 or trace.get('epoch') != last.get('epoch'):
        raise RuntimeError('Stale or invalid output geometry snapshot')
    rows = output_geometry(last, trace.get('outputGeometries'))
    return (last, facts, rows, trace) if include_trace else (last, facts, rows)


def load_stage(directory, env, index, stage, outputs):
    package_id = 'owg-direct-output-scope-' + str(index)
    package = directory / package_id
    ui = package / 'contents/ui'
    ui.mkdir(parents=True)
    source = (ROOT / 'tests/integration/direct_output_scope_stage.qml').read_text()
    source = source.replace('__TARGET__', json.dumps(stage['frame'])).replace('__OUTPUTS__', json.dumps(outputs)).replace('__INDEX__', str(index))
    (ui / 'main.qml').write_text(source)
    (package / 'metadata.json').write_text(json.dumps({
        'KPackageStructure': 'KWin/Script',
        'KPlugin': {'Id': package_id, 'Name': 'Private owned output-scope test', 'Version': '0.1.0',
                    'License': 'GPL-2.0-or-later', 'EnabledByDefault': False},
        'X-Plasma-API': 'declarativescript', 'X-Plasma-API-Minimum-Version': '6.0',
        'X-Plasma-MainScript': 'ui/main.qml'}))
    subprocess.run(['kpackagetool6', '--type', 'KWin/Script', '--install', str(package)],
                   env=env, check=True, text=True, capture_output=True, timeout=10)
    installed = Path(env['XDG_DATA_HOME']) / 'kwin/scripts' / package_id / 'contents/ui/main.qml'
    script_id = scripting(env, 'loadDeclarativeScript', str(installed), package_id)
    if type(script_id) is not int or script_id < 0:
        raise RuntimeError('Owned output-scope script did not load')
    subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin', '--object-path',
                    '/Scripting/Script' + str(script_id), '--method', 'org.kde.kwin.Script.run'],
                   env=env, check=True, text=True, capture_output=True, timeout=5)
    return package_id


def unload_stage(env, package_id):
    if scripting(env, 'unloadScript', package_id) is not True or scripting(env, 'isScriptLoaded', package_id) is not False:
        raise RuntimeError('Owned scope script did not unload')


def observe(directory, env, kwin, client, seconds, vt):
    end = time.monotonic() + seconds
    def alive():
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Owned keyboard check aborted or compositor/client exited')
        if time.monotonic() >= end:
            raise RuntimeError('Physical two-monitor keyboard sequence did not complete before the deadline')
    initial = None
    while initial is None:
        alive()
        initial = coherent_state(env)
        time.sleep(.08)
    root, facts, outputs = initial
    verify_continuity(root, root)
    verify_environment(facts, vt)
    stages = plan(outputs)
    results = []
    for index, stage in enumerate(stages):
        publish_instruction(directory, 'Moving the owned window to ' + stage['name'] + '.\nWait for TYPE NOW. Keep the mouse still.')
        package_id = load_stage(directory, env, index, stage, outputs)
        ready = None
        while ready is None:
            alive()
            ready = check_log((directory / 'kwin.log').read_text(errors='replace'), index)
            time.sleep(.1)
        frame = validate_frame(ready, stage, outputs)
        before = None
        while before is None:
            alive()
            before = coherent_state(env)
        baseline, origin_before, geometry = before
        verify_continuity(root, baseline)
        verify_environment(origin_before, vt)
        if geometry != outputs:
            raise RuntimeError('Physical output geometry changed before the keyboard stage')
        count_before = (directory / 'client.log').read_text(errors='replace').count('CLIENT_EVENT keyboard')
        publish_instruction(directory, 'Stage ' + str(index + 1) + '/3: ' + stage['name'] + '\nTYPE NOW: press and release one ordinary key.\nKeep the mouse still; do not click. Wait for the window to move.')
        print('DIRECT_SCOPE_READY:', stage['name'], flush=True)
        result = None
        while result is None:
            alive()
            check_log((directory / 'kwin.log').read_text(errors='replace'), index)
            sample = coherent_state(env)
            if sample is None:
                time.sleep(.08)
                continue
            current, origin, geometry = sample
            verify_continuity(root, current)
            verify_environment(origin, vt)
            if geometry != outputs:
                raise RuntimeError('Physical output geometry changed during the keyboard stage')
            count = (directory / 'client.log').read_text(errors='replace').count('CLIENT_EVENT keyboard')
            try:
                result = keyboard_result(baseline, current, origin_before, origin, count - count_before, stage['expectedOutputs'])
            except NonKeyboardInput:
                baseline, origin_before, count_before = current, origin, count
                publish_instruction(directory, 'Stage ' + str(index + 1) + '/3: ' + stage['name'] + '\nMouse input detected: keep it still.\nTYPE NOW: press and release another ordinary key.')
            progress = {'stage': stage['name'], 'baseline': baseline, 'current': current,
                        'originBefore': origin_before, 'origin': origin, 'outputs': outputs, 'completed': results}
            temporary = directory / 'scope-progress.tmp'
            temporary.write_text(json.dumps(progress, indent=2) + '\n')
            temporary.replace(directory / 'scope-progress.json')
            time.sleep(.08)
        check_log((directory / 'kwin.log').read_text(errors='replace'), index)
        unload_stage(env, package_id)
        results.append(dict(name=stage['name'], frame=frame, expectedOutputs=stage['expectedOutputs'], scriptUnloaded=True, **result))
        print('DIRECT_SCOPE_STAGE: PASS', stage['name'], flush=True)
    return {'outputScope': {'outputs': outputs, 'stages': results, 'ownedFocusMonitored': True,
                            'scope': 'Physical keyboard attribution for contained and spanning owned client only'},
            'environment': origin}

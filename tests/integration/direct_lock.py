"""Password-authenticated physical lock cycle in private DRM KWin."""
from types import SimpleNamespace
import subprocess
import time

import direct_output_scope as placement
from direct_hotplug import client_counts, recovery_stage, same_geometry, valid_geometry
from direct_recording import checkpoint, event
from direct_warning import publish_instruction
from direct_vt import transitions
from run_input_direct import KINDS, environment, snapshot, verify_continuity, verify_environment


def prepare_authentication(directory):
    # Private config only: no grace period or password-free unlock.
    (directory / 'config/kscreenlockerrc').write_text(
        '[Daemon]\nRequirePassword=true\nLockGrace=0\nLockOnResume=true\n')


def screen_saver(env, method):
    reply = subprocess.run(['gdbus', 'call', '--session', '--dest',
                            'org.freedesktop.ScreenSaver', '--object-path', '/ScreenSaver',
                            '--method', 'org.freedesktop.ScreenSaver.' + method],
                           env=env, check=True, capture_output=True, text=True, timeout=5)
    value = reply.stdout.strip()
    if method == "Lock":
        if value != "()":
            raise RuntimeError("Unexpected private Lock reply")
        return ()
    if value not in ("(true,)", "(false,)"):
        raise RuntimeError("Unexpected private GetActive reply")
    return (value == "(true,)",)


def validate_trace(baseline, current, trace, geometry):
    if (trace.get('schema') != 1 or trace.get('epoch') != baseline['epoch'] or
            trace.get('lifecycleTraceCapacity') != 16):
        raise RuntimeError('Missing synchronous lock lifecycle trace; rebuild probe')
    all_rows = trace.get('lifecycleTransitions')
    if not isinstance(all_rows, list) or len(all_rows) > 16:
        raise RuntimeError('Invalid bounded lifecycle trace')
    rows = [r for r in all_rows if r.get('sessionGeneration', -1) > baseline['sessionGeneration']]
    reasons = [r.get('reason') for r in rows]
    phase = 'locking'
    for reason in reasons:
        if reason == 'locking' and phase == 'locking':
            continue
        if reason == 'locked' and phase == 'locking' and reasons[0] == 'locking':
            phase = 'locked'
        elif reason == 'unlocked' and phase == 'locked':
            phase = 'unlocked'
        else:
            raise RuntimeError('Unexpected lock lifecycle sequence')
    if (current.get('schema') != 2 or current.get('epoch') != baseline['epoch'] or
            current.get('outputIdentityComplete') is not True or
            current.get('sessionActivity') != baseline['sessionActivity'] or
            current.get('sleepState') != baseline['sleepState'] or
            {r.get('id') for r in current.get('outputs', [])} != {r['id'] for r in geometry} or
            any(type(current.get(k)) is not int or current[k] < baseline[k]
                for k in (*KINDS, 'sequence', 'sessionGeneration', 'topologyGeneration'))):
        raise RuntimeError('Incomplete or changed current lock session facts')
    previous = baseline
    for i, row in enumerate(rows):
        reason = reasons[i]
        if (row.get('schema') != 2 or row.get('epoch') != baseline['epoch'] or
                row.get('sessionGeneration') != baseline['sessionGeneration'] + i + 1 or
                row.get('reason') != reason or row.get('lockState') != reason or
                row.get('sessionActivity') != baseline['sessionActivity'] or
                row.get('sleepState') != baseline['sleepState'] or
                row.get('topologyGeneration') != baseline['topologyGeneration'] or
                row.get('outputIdentityComplete') is not True or
                row.get('buttonKnown') is not False or row.get('buttonsDown') is not False or
                row.get('ageMs') != -1 or
                {r.get('id') for r in row.get('outputs', [])} != {r['id'] for r in geometry} or
                any(r.get('events') != 0 for r in row.get('outputs', [])) or
                not same_geometry(row.get('outputGeometries'), geometry) or
                type(row.get('monotonicMs')) is not int or row['monotonicMs'] < 0 or
                (i and row['monotonicMs'] < previous['monotonicMs']) or
                any(type(row.get(k)) is not int or row[k] < previous[k] for k in (*KINDS, 'sequence'))):
            raise RuntimeError('Missing, stale or uncleared lock boundary')
        if i and any(row[k] != rows[0][k] for k in (*KINDS, 'sequence')):
            raise RuntimeError('Observer accepted input while locking/locked')
        previous = row
    expected = baseline['sessionGeneration'] + len(rows)
    if current['sessionGeneration'] < expected:
        return None  # Reads straddled a signal; retry with coherent evidence.
    if current['sessionGeneration'] != expected:
        raise RuntimeError('Session generation missing from lifecycle trace')
    if current['topologyGeneration'] != baseline['topologyGeneration']:
        raise RuntimeError('Output topology changed during lock test')
    expected_state = rows[-1]['reason'] if rows else baseline['lockState']
    if current['lockState'] != expected_state:
        return None
    if rows and phase != 'unlocked' and (
            any(current[k] != rows[0][k] for k in (*KINDS, 'sequence')) or
            current.get('buttonKnown') is not False or current.get('buttonsDown') is not False or
            current.get('ageMs') != -1 or any(r['events'] for r in current['outputs'])):
        raise RuntimeError('Locked observation retained/accepted input')
    return rows


def observe(directory, env, kwin, client, seconds, vt):
    end = time.monotonic() + seconds
    initial = placement.coherent_state(env)
    if initial is None:
        raise RuntimeError('Initial lock-test output facts are incoherent')
    baseline, origin, geometry = initial
    valid_geometry(geometry)
    stages = placement.contained_stages(geometry, centered=True, top_inset=200)
    verify_continuity(baseline, baseline)
    verify_environment(origin, vt)
    if screen_saver(env, 'GetActive') != (False,):
        raise RuntimeError('Private screen locker unexpectedly active at startup')
    before = client_counts(directory / 'client.log')
    publish_instruction(directory, 'Click here, move, scroll and type an ordinary key.\n'
                        'Then HOLD the left mouse button for 3 seconds and keep holding.\n'
                        'The PRIVATE session will lock automatically. Release once locked.\n'
                        'Wait 5 seconds on the lock screen, then unlock with your normal password.\n'
                        'Keep both monitors connected and stay on this VT.')
    armed = False
    held_since = None
    locked_samples = 0
    locked_since = None
    locked_span = 0
    locked_clients = None
    locked_origin = None
    initial_input = None
    while time.monotonic() < end:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Private lock compositor/client exited')
        current, facts, trace = snapshot(env), environment(env), transitions(env)
        verify_environment(facts, vt)
        if not same_geometry(trace.get('outputGeometries'), geometry):
            raise RuntimeError('Physical output geometry changed during lock/unlock')
        rows = validate_trace(baseline, current, trace, geometry)
        clients = client_counts(directory / 'client.log')
        checkpoint(directory, 'lock-progress.json', dict(baseline=baseline, current=current,
                   environment=facts, transitions=trace, clientCounters=clients,
                   armed=armed, lockedSamples=locked_samples, lockedObservedMs=round(locked_span*1000)))
        if rows is None:
            time.sleep(.05)
            continue
        if not armed:
            if rows:
                raise RuntimeError('Lock occurred before complete initial input')
            verify_continuity(baseline, current)
            complete = (all(current[k] > baseline[k] and facts['libinput'][k] > origin['libinput'][k]
                            and clients[k] > before[k] for k in KINDS) and
                        current.get('buttonKnown') is True and current.get('buttonsDown') is True)
            if complete:
                held_since = held_since if held_since is not None else time.monotonic()
                if time.monotonic() - held_since >= 3:
                    initial_input = dict(heldSnapshot=current,
                        observer={k:current[k]-baseline[k] for k in KINDS},
                        libinput={k:facts['libinput'][k]-origin['libinput'][k] for k in KINDS},
                        client={k:clients[k]-before[k] for k in KINDS})
                    event(directory, 'private-password-lock-requested')
                    screen_saver(env, 'Lock')
                    armed = True
            else:
                held_since = None
        elif rows and rows[-1]['reason'] == 'locked':
            if screen_saver(env, 'GetActive') != (True,):
                time.sleep(.1)
                continue
            locked_since = locked_since if locked_since is not None else time.monotonic()
            if locked_clients is None:
                locked_clients, locked_origin = clients.copy(), facts['libinput'].copy()
            elif clients != locked_clients or facts['libinput'] != locked_origin:
                raise RuntimeError('Owned client or physical observer accepted locked input')
            locked_samples += 1
            locked_span = time.monotonic() - locked_since
        elif rows and rows[-1]['reason'] == 'unlocked':
            if locked_samples < 2 or locked_span < 1:
                raise RuntimeError('Locked session not observed long enough; wait five seconds before unlocking')
            if screen_saver(env, 'GetActive') != (False,):
                time.sleep(.1)
                continue
            returned = placement.coherent_state(env)
            if returned is None:
                continue
            fresh, fresh_origin, fresh_geometry = returned
            verify_continuity(current, fresh)
            if not same_geometry(geometry, fresh_geometry):
                raise RuntimeError('Output geometry did not recover after unlock')
            cycle = SimpleNamespace(initial_geometry=geometry, return_baseline=fresh, vt=vt)
            recovered = []
            for index, stage in enumerate(stages):
                result, fresh_origin = recovery_stage(directory, env, kwin, client, cycle, stage, index, end)
                recovered.append(result)
            return {'lockCycle': dict(initialInput=initial_input, lifecycleBoundaries=rows,
                    lockedSamples=locked_samples, lockedObservedMs=round(locked_span*1000),
                    initialOutputGeometries=geometry, returnedOutputGeometries=fresh_geometry,
                    returnBaseline=fresh, recoveredInputStages=recovered,
                    passwordRequired=True, lockGraceSeconds=0), 'environment':fresh_origin}
        time.sleep(.1)
    raise RuntimeError('Authenticated lock/unlock or fresh input incomplete before deadline')

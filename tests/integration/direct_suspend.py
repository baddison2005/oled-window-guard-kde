"""Opt-in real system suspend with durable sleep/reset and two-output evidence."""
from pathlib import Path
from types import SimpleNamespace
import subprocess
import time

import direct_output_scope as placement
from direct_hotplug import client_counts, recovery_stage, same_geometry, valid_geometry
from direct_recording import checkpoint, event
from direct_vt import transitions
from direct_warning import publish_instruction
from run_input_direct import KINDS, environment, snapshot, verify_continuity, verify_environment

COUNTERS = (*KINDS, 'sequence')


def clock_sample():
    # Linux BOOTTIME includes suspended time; MONOTONIC excludes it.
    return dict(bootID=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                monotonicNs=time.clock_gettime_ns(time.CLOCK_MONOTONIC),
                boottimeNs=time.clock_gettime_ns(time.CLOCK_BOOTTIME))


def suspended_ms(before, after):
    if before['bootID'] != after['bootID']:
        raise RuntimeError('Boot changed during suspend test')
    for key in ('monotonicNs', 'boottimeNs'):
        if (type(before.get(key)) is not int or type(after.get(key)) is not int or
                before[key] < 0 or after[key] < before[key]):
            raise RuntimeError('Invalid suspend clock evidence')
    elapsed = ((after['boottimeNs'] - before['boottimeNs']) -
               (after['monotonicNs'] - before['monotonicNs'])) / 1_000_000
    if elapsed < 3000:
        raise RuntimeError('Real suspend lasted less than three seconds or was not observed')
    return round(elapsed)


def suspend_preflight():
    if not hasattr(time, 'CLOCK_BOOTTIME') or 'mem' not in Path('/sys/power/state').read_text().split():
        raise RuntimeError('Linux suspend-to-RAM and BOOTTIME clock are required')
    reply = subprocess.run(['gdbus', 'call', '--system', '--dest', 'org.freedesktop.login1',
                            '--object-path', '/org/freedesktop/login1', '--method',
                            'org.freedesktop.login1.Manager.CanSuspend'],
                           capture_output=True, text=True, check=True, timeout=5)
    if reply.stdout.strip() != "('yes',)":
        raise RuntimeError('Logind must permit suspend without an authentication prompt: ' + reply.stdout.strip())
    clock_sample()


def validate_trace(baseline, current, trace, geometry):
    """Require contiguous synchronous reset histories, including resume topology."""
    if (trace.get('schema') != 1 or trace.get('epoch') != baseline['epoch'] or
            trace.get('lifecycleTraceCapacity') != 16 or trace.get('topologyTraceCapacity') != 64):
        raise RuntimeError('Missing bounded suspend lifecycle/topology trace')
    histories = []
    for field, generation, capacity in (('lifecycleTransitions', 'sessionGeneration', 16),
                                        ('topologyTransitions', 'topologyGeneration', 64)):
        rows = trace.get(field)
        if (not isinstance(rows, list) or len(rows) > capacity or
                any(type(r.get(generation)) is not int for r in rows)):
            raise RuntimeError('Invalid bounded suspend reset history')
        rows = [r for r in rows if r[generation] > baseline[generation]]
        for i, row in enumerate(rows, 1):
            if row[generation] != baseline[generation] + i:
                raise RuntimeError('Missing or reordered suspend reset generation')
        histories.append(rows)
    lifecycle, topology = histories
    states = {baseline['sessionGeneration']: baseline}
    sleep = baseline['sleepState']
    activity = baseline['sessionActivity']
    seen_sleep = seen_wake = False
    previous = baseline
    for row in lifecycle:
        reason = row.get('reason')
        if reason == 'sleeping' and not seen_sleep:
            sleep, seen_sleep = 'sleeping', True
        elif reason == 'awake' and seen_sleep and not seen_wake:
            sleep, seen_wake = 'awake', True
        elif reason in ('inactive', 'active') and activity != reason:
            activity = reason
        else:
            raise RuntimeError('Unexpected suspend lifecycle order or lock transition')
        if (row.get('sleepState') != sleep or row.get('sessionActivity') != activity or
                row.get('lockState') != baseline['lockState']):
            raise RuntimeError('Suspend boundary states disagree with lifecycle reasons')
        if (previous.get('sleepState') == 'sleeping' or previous.get('sessionActivity') == 'inactive') and any(
                row.get(k) != previous.get(k) for k in COUNTERS):
            raise RuntimeError('Observer accepted input while sleeping/inactive')
        states[row['sessionGeneration']] = row
        previous = row

    known_ids = {r['id'] for r in geometry}
    for rows in histories:
        previous = baseline
        for row in rows:
            record_geometry = valid_geometry(row.get('outputGeometries'))
            outputs = row.get('outputs', [])
            if (row.get('schema') != 2 or row.get('epoch') != baseline['epoch'] or
                    row.get('outputIdentityComplete') is not True or
                    len(outputs) != len(record_geometry) or
                    {r.get('id') for r in outputs} != {r['id'] for r in record_geometry} or
                    not {r['id'] for r in record_geometry}.issubset(known_ids) or
                    row.get('buttonKnown') is not False or row.get('buttonsDown') is not False or
                    row.get('ageMs') != -1 or any(r.get('events') != 0 for r in outputs) or
                    type(row.get('monotonicMs')) is not int or row['monotonicMs'] < 0 or
                    (previous is not baseline and row['monotonicMs'] < previous['monotonicMs']) or
                    any(type(row.get(k)) is not int or row[k] < previous[k] for k in COUNTERS)):
                raise RuntimeError('Stale, uncleared or invalid suspend reset boundary')
            state = states.get(row.get('sessionGeneration'))
            if state is None or any(row.get(k) != state.get(k) for k in ('lockState', 'sleepState', 'sessionActivity')):
                raise RuntimeError('Topology reset has unrecorded lifecycle state')
            if rows is topology:
                generation = row['sessionGeneration']
                next_state = states.get(generation + 1)
                if ((state is not baseline and row['monotonicMs'] < state['monotonicMs']) or
                        (next_state and row['monotonicMs'] > next_state['monotonicMs']) or
                        any(row[k] < state[k] for k in COUNTERS) or
                        (next_state and any(row[k] > next_state[k] for k in COUNTERS)) or
                        ((state['sleepState'] == 'sleeping' or state['sessionActivity'] == 'inactive') and
                         any(row[k] != state[k] for k in COUNTERS))):
                    raise RuntimeError('Topology input/state does not belong to suspend boundaries')
            previous = row

    for key, rows in (('sessionGeneration', lifecycle), ('topologyGeneration', topology)):
        expected = baseline[key] + len(rows)
        if type(current.get(key)) is not int:
            raise RuntimeError('Invalid current suspend generation')
        if current[key] < expected:
            return None  # Separate D-Bus reads can straddle a reset.
        if current[key] != expected:
            raise RuntimeError('Current suspend generation missing from reset trace')
    for row in lifecycle:
        generation = row.get('topologyGeneration')
        if type(generation) is not int or not baseline['topologyGeneration'] <= generation <= current['topologyGeneration']:
            raise RuntimeError('Lifecycle reset has an unrecorded topology generation')
        before = next((r for r in topology if r['topologyGeneration'] == generation), None)
        after = next((r for r in topology if r['topologyGeneration'] == generation + 1), None)
        if ((before and before['monotonicMs'] > row['monotonicMs']) or
                (after and after['monotonicMs'] < row['monotonicMs'])):
            raise RuntimeError('Lifecycle and topology reset ordering disagree')
    if (current.get('schema') != 2 or current.get('epoch') != baseline['epoch'] or
            current.get('outputIdentityComplete') is not True or
            current.get('lockState') != baseline['lockState'] or
            any(type(current.get(k)) is not int or current[k] < previous_state[k]
                for previous_state in (baseline, *lifecycle, *topology) for k in COUNTERS)):
        raise RuntimeError('Incomplete current suspend facts')
    current_geometry = valid_geometry(trace.get('outputGeometries'))
    outputs = current.get('outputs', [])
    if (len(outputs) != len(current_geometry) or
            {r.get('id') for r in outputs} != {r['id'] for r in current_geometry} or
            not {r['id'] for r in current_geometry}.issubset(known_ids)):
        return None  # Topology reads may also straddle a reset.
    if current.get('sleepState') != sleep or current.get('sessionActivity') != activity:
        return None
    if sleep == 'sleeping' or activity == 'inactive':
        if (any(current[k] != states[current['sessionGeneration']][k] for k in COUNTERS) or
                current.get('buttonKnown') is not False or current.get('buttonsDown') is not False or
                current.get('ageMs') != -1 or any(r.get('events') != 0 for r in current.get('outputs', []))):
            raise RuntimeError('Suspended observation retained or accepted input')
    return lifecycle, topology


def observe(directory, env, kwin, client, seconds, vt):
    end = time.monotonic() + seconds
    initial = placement.coherent_state(env)
    if initial is None:
        raise RuntimeError('Initial suspend output facts are incoherent')
    baseline, origin, geometry = initial
    valid_geometry(geometry)
    stages = placement.contained_stages(geometry, centered=True, top_inset=200)
    verify_continuity(baseline, baseline)
    verify_environment(origin, vt)
    before = client_counts(directory / 'client.log')
    publish_instruction(directory, 'This test will SUSPEND THE WHOLE COMPUTER.\n'
                        'Click here, move, scroll and type an ordinary key.\n'
                        'Then HOLD the left button for 3 seconds and keep holding.\n'
                        'Release once the computer sleeps. Wait at least 5 seconds, then wake it.\n'
                        'Keep both cables connected. After wake, stay on or return to this test VT.')
    held_since = request_clock = initial_input = None
    settle = None
    while time.monotonic() < end:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Private suspend compositor/client exited')
        current, facts, trace = snapshot(env), environment(env), transitions(env)
        verify_environment(facts, vt, active=facts.get('sessionActive'))
        if type(facts.get('sessionActive')) is not bool:
            raise RuntimeError('Unknown physical session activity')
        records = validate_trace(baseline, current, trace, geometry)
        clients = client_counts(directory / 'client.log')
        checkpoint(directory, 'suspend-progress.json', dict(baseline=baseline, current=current,
                   environment=facts, transitions=trace, clientCounters=clients, requestClock=request_clock))
        if records is None:
            time.sleep(.05)
            continue
        lifecycle, topology = records
        if lifecycle and initial_input is not None and any(
                lifecycle[0][k] < initial_input['heldSnapshot'][k] for k in COUNTERS):
            raise RuntimeError('Input counters regressed from the held pre-suspend state')
        if request_clock is None:
            verify_continuity(baseline, current)
            if not same_geometry(trace.get('outputGeometries'), geometry):
                raise RuntimeError('Output geometry changed before suspend was armed')
            complete = (all(current[k] > baseline[k] and facts['libinput'][k] > origin['libinput'][k]
                            and clients[k] > before[k] for k in KINDS) and
                        current.get('buttonKnown') is True and current.get('buttonsDown') is True)
            if complete:
                held_since = held_since if held_since is not None else time.monotonic()
                if time.monotonic() - held_since >= 3:
                    initial_input = dict(heldSnapshot=current, environment=facts,
                                         clientDeltas={k:clients[k]-before[k] for k in KINDS})
                    request_clock = clock_sample()
                    checkpoint(directory, 'suspend-request.json', dict(clock=request_clock, initialInput=initial_input))
                    event(directory, 'real-system-suspend-requested', clock=request_clock)
                    # Explicit --suspend-cycle --run authorizes this single real suspend.
                    # Do not bypass inhibitors or request interactive credentials.
                    reply = subprocess.run(['systemctl', '--no-ask-password', 'suspend'], env=env,
                                           capture_output=True, text=True, timeout=10)
                    event(directory, 'system-suspend-command-returned', exitCode=reply.returncode,
                          stdout=reply.stdout, stderr=reply.stderr)
                    if reply.returncode:
                        raise RuntimeError('System suspend refused: ' + reply.stderr.strip())
            else:
                held_since = None
        elif (any(r['reason'] == 'sleeping' for r in lifecycle) and
              any(r['reason'] == 'awake' for r in lifecycle) and
              current['sleepState'] == 'awake' and facts['sessionActive'] is True and
              current['sessionActivity'] != 'inactive'):
            returned = placement.coherent_state(env)
            if returned is None:
                continue
            fresh, fresh_origin, fresh_geometry = returned
            if (fresh['sessionGeneration'] != current['sessionGeneration'] or
                    fresh['topologyGeneration'] != current['topologyGeneration']):
                settle = None
                continue
            signature = (fresh['sessionGeneration'], fresh['topologyGeneration'], repr(fresh_geometry))
            if settle is None or settle[0] != signature:
                settle = (signature, time.monotonic())
                continue
            if time.monotonic() - settle[1] < .75:
                continue
            if not same_geometry(fresh_geometry, geometry):
                raise RuntimeError('Outputs did not restore exact geometry/scales after resume')
            verify_continuity(current, fresh)
            verify_environment(fresh_origin, vt)
            wake_clock = clock_sample()
            duration = suspended_ms(request_clock, wake_clock)
            evidence = dict(initialInput=initial_input, lifecycleBoundaries=lifecycle,
                            topologyBoundaries=topology, requestClock=request_clock,
                            wakeClock=wake_clock, suspendedMs=duration,
                            initialOutputGeometries=geometry, returnedOutputGeometries=fresh_geometry,
                            returnBaseline=fresh, privateLockscreenEnabled=False)
            checkpoint(directory, 'suspend-cycle.json', evidence)
            event(directory, 'real-system-resume-verified', suspendedMs=duration)
            cycle = SimpleNamespace(initial_geometry=geometry, return_baseline=fresh, vt=vt)
            recovered = []
            for index, stage in enumerate(stages):
                result, fresh_origin = recovery_stage(directory, env, kwin, client, cycle, stage, index, end)
                recovered.append(result)
            evidence['recoveredInputStages'] = recovered
            return {'suspendCycle': evidence, 'environment': fresh_origin}
        time.sleep(.1)
    raise RuntimeError('Real suspend/resume or fresh two-output input incomplete before deadline')

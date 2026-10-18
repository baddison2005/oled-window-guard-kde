"""Physical connector disconnect/reconnect evidence with fresh recovered input."""
import json
import math
import time

import direct_output_scope as placement
from direct_recording import checkpoint, event
from direct_warning import publish_instruction
from direct_vt import TOPOLOGY_TRACE_LIMIT
from run_input_direct import KINDS, environment, snapshot, verify_continuity, verify_environment, verify_result


def ids(rows):
    return [row['id'] for row in rows]


def select_target(geometry, connector):
    if connector not in ('HDMI', 'DP'):
        raise RuntimeError('Unsupported hotplug connector type')
    candidates = [row['id'] for row in geometry if row['id'].startswith(connector + '-')]
    if len(geometry) != 2 or len(candidates) != 1:
        raise RuntimeError('Physical hotplug requires exactly two outputs and one ' + connector + ' connector')
    return candidates[0]


def valid_geometry(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 16:
        raise RuntimeError('Physical output geometry is empty or unbounded')
    names = []
    for row in rows:
        name = row.get('id')
        if (not isinstance(name, str) or not name or len(name) > 64 or name in names or
                any(type(row.get(key)) not in (int, float) or not math.isfinite(row[key])
                    for key in ('x', 'y', 'width', 'height', 'scale')) or
                row['width'] <= 0 or row['height'] <= 0 or not .25 <= row['scale'] <= 8):
            raise RuntimeError('Invalid physical output identity, geometry or scale')
        names.append(name)
    return rows


def same_geometry(left, right):
    return (isinstance(left, list) and isinstance(right, list) and
            sorted(left, key=lambda row: row.get('id', '')) ==
            sorted(right, key=lambda row: row.get('id', '')))


def validate_current(baseline, current, facts, trace, geometry, vt):
    if (current.get('schema') != 2 or current.get('epoch') != baseline.get('epoch') or
            current.get('sessionGeneration') != baseline.get('sessionGeneration') or
            current.get('outputIdentityComplete') is not True or not current.get('outputs') or
            current.get('lockState') in ('locking', 'locked') or
            current.get('sleepState') == 'sleeping' or current.get('sessionActivity') == 'inactive'):
        raise RuntimeError('Observer epoch/session/safety state changed during physical hotplug')
    for key in (*KINDS, 'sequence', 'topologyGeneration'):
        if type(current.get(key)) is not int or current[key] < 0:
            raise RuntimeError('Invalid hotplug event counter')
    if current['topologyGeneration'] < baseline['topologyGeneration']:
        raise RuntimeError('Topology generation regressed')
    geometry = valid_geometry(geometry)
    if ids(current['outputs']) != ids(geometry):
        raise RuntimeError('Snapshot and geometry output identities disagree')
    verify_environment(facts, vt)
    if trace.get('schema') != 1 or trace.get('epoch') != baseline['epoch']:
        raise RuntimeError('Missing or stale synchronous topology-transition evidence')
    session_rows = trace.get('transitions')
    if not isinstance(session_rows, list) or len(session_rows) > 8:
        raise RuntimeError('Invalid bounded session-transition evidence')
    if any(row.get('sessionGeneration', -1) > baseline['sessionGeneration'] for row in session_rows):
        raise RuntimeError('Session activity changed during physical hotplug')
    rows = trace.get('topologyTransitions')
    if not isinstance(rows, list) or len(rows) > TOPOLOGY_TRACE_LIMIT:
        raise RuntimeError('Invalid bounded topology-transition evidence')
    rows = [row for row in rows
            if row.get('topologyGeneration', -1) > baseline['topologyGeneration']]
    expected = current['topologyGeneration'] - baseline['topologyGeneration']
    if len(rows) != expected:
        generations = [row.get('topologyGeneration') for row in rows]
        raise RuntimeError(
            'Topology reset record is missing or truncated: '
            f"baseline={baseline['topologyGeneration']}, "
            f"current={current['topologyGeneration']}, expected={expected}, "
            f'recorded={generations}')
    previous = baseline
    for index, row in enumerate(rows, 1):
        if (row.get('schema') != 2 or row.get('epoch') != baseline['epoch'] or
                row.get('topologyGeneration') != baseline['topologyGeneration'] + index or
                row.get('sessionGeneration') != baseline['sessionGeneration'] or
                row.get('sessionActivity') != baseline.get('sessionActivity') or
                row.get('lockState') != baseline.get('lockState') or
                row.get('sleepState') != baseline.get('sleepState') or
                row.get('outputIdentityComplete') is not True or
                row.get('buttonKnown') is not False or row.get('buttonsDown') is not False or
                row.get('ageMs') != -1 or type(row.get('monotonicMs')) is not int or
                row['monotonicMs'] < 0 or any(output.get('events') != 0 for output in row.get('outputs', []))):
            raise RuntimeError('Topology boundary did not synchronously clear activity facts')
        record_geometry = valid_geometry(row.get('outputGeometries'))
        if ids(row.get('outputs', [])) != ids(record_geometry):
            raise RuntimeError('Topology boundary output identities disagree')
        if any(row[key] < previous[key] for key in (*KINDS, 'sequence')):
            raise RuntimeError('Input counters regressed across topology boundaries')
        if index > 1 and row['monotonicMs'] < rows[index - 2]['monotonicMs']:
            raise RuntimeError('Topology boundary times are unordered')
        previous = row
    return rows


def client_counts(path):
    log = path.read_text(errors='replace')
    return {kind: log.count('CLIENT_EVENT ' + kind) for kind in KINDS}


def candidate_complete(baseline, current, origin_before, origin, clients_before, clients,
                       held, released):
    return (held and released and current.get('buttonKnown') is True and
            current.get('buttonsDown') is False and
            all(current[key] > baseline[key] and
                origin['libinput'][key] > origin_before['libinput'][key] and
                clients[key] > clients_before[key] for key in KINDS))


def advance_with_evidence(directory, cycle, current, facts, trace, clients, geometry, now):
    """Retain the exact attempted sample even when boundary validation rejects it."""
    progress = {'stageBeforeValidation': cycle.stage, 'baseline': cycle.baseline,
                'current': current, 'environment': facts, 'transitions': trace,
                'clientCounters': clients, 'outputGeometries': geometry}
    try:
        cycle.advance(current, facts, trace, clients, geometry, now)
        progress['validation'] = 'passed'
    except Exception as exc:
        progress.update(validation='failed', error=str(exc))
        raise
    finally:
        progress.update(stage=cycle.stage,
                        disconnectedBaseline=getattr(cycle, 'disconnected_baseline', None),
                        result=cycle.result)
        checkpoint(directory, 'hotplug-progress.json', progress)


class HotplugCycle:
    def __init__(self, baseline, origin, clients, geometry, vt, target_id):
        verify_continuity(baseline, baseline)
        verify_environment(origin, vt)
        geometry = valid_geometry(geometry)
        if len(geometry) != 2 or ids(baseline['outputs']) != ids(geometry):
            raise RuntimeError('Physical hotplug requires exactly two complete initial outputs')
        if ids(geometry).count(target_id) != 1:
            raise RuntimeError('The requested physical hotplug connector is unavailable or ambiguous')
        survivors = [row['id'] for row in geometry if row['id'] != target_id]
        if len(survivors) != 1:
            raise RuntimeError('Physical hotplug requires exactly one surviving output')
        self.baseline, self.origin, self.clients = baseline, origin, clients.copy()
        self.initial_geometry, self.vt = geometry, vt
        self.target_id, self.survivor_id = target_id, survivors[0]
        self.stage = 'initial-input'
        self.message = ('Before disconnecting anything: use this dark window on ' + self.survivor_id + '.\n'
                        'Click, move, scroll and type a key. Hold the left button for 3 seconds, then release.\n'
                        'Wait for NOW DISCONNECT ' + self.target_id + '.')
        self.held = self.released = False
        self.settle_signature = self.settle_since = None
        self.result = None
        self.last_current = baseline

    def advance(self, current, facts, trace, clients, geometry, now):
        records = validate_current(self.baseline, current, facts, trace, geometry, self.vt)
        if any(current[key] < self.last_current[key] for key in (*KINDS, 'sequence')):
            raise RuntimeError('Input counters regressed during physical hotplug')
        self.last_current = current
        if records and hasattr(self, 'initial_complete') and any(
                records[0][key] < self.initial_complete[key] for key in (*KINDS, 'sequence')):
            raise RuntimeError('Input counters regressed at the first topology boundary')
        initial_generation = self.baseline['topologyGeneration']
        if self.stage == 'initial-input':
            if (current['topologyGeneration'] != initial_generation or
                    not same_geometry(geometry, self.initial_geometry)):
                raise RuntimeError('Connector changed before initial physical input was complete')
            verify_continuity(self.baseline, current)
            self.held |= (current.get('buttonKnown') is True and current.get('buttonsDown') is True
                          and current['button'] > self.baseline['button'])
            self.released |= (self.held and current.get('buttonKnown') is True
                              and current.get('buttonsDown') is False)
            if candidate_complete(self.baseline, current, self.origin, facts, self.clients,
                                  clients, self.held, self.released):
                initial_counts = placement.output_counts(self.baseline)
                final_counts = placement.output_counts(current)
                if final_counts[self.survivor_id] <= initial_counts[self.survivor_id]:
                    raise RuntimeError('Initial input did not reach the surviving output')
                self.initial_input = verify_result(
                    self.baseline, current, self.origin, facts,
                    {key: clients[key] - self.clients[key] for key in KINDS},
                    self.held, self.released)
                self.initial_input['outputEventDeltas'] = {
                    name: final_counts[name] - initial_counts[name] for name in initial_counts}
                self.initial_complete = current
                self.stage = 'disconnect'
                self.message = ('NOW DISCONNECT ' + self.target_id + ' only.\n'
                                'Leave ' + self.survivor_id + ' connected. Keep the mouse still and wait.\n'
                                'Do not switch virtual terminals.')
            return

        current_ids = ids(geometry)
        if self.stage == 'disconnect':
            if current['topologyGeneration'] == initial_generation:
                return
            if current_ids == [self.target_id]:
                raise RuntimeError('The surviving output ' + self.survivor_id + ' was disconnected instead of ' + self.target_id)
            if current_ids != [self.survivor_id]:
                self.settle_signature = self.settle_since = None
                return
            signature = (current['topologyGeneration'], json.dumps(geometry, sort_keys=True))
            if signature != self.settle_signature:
                self.settle_signature, self.settle_since = signature, now
                return
            if now - self.settle_since < .75:
                return
            if not any(ids(row['outputGeometries']) == [self.survivor_id] for row in records):
                raise RuntimeError('No synchronous reset captured the target-disconnected topology')
            self.disconnected_baseline = current
            self.disconnected_geometry = geometry
            self.disconnected_generation = current['topologyGeneration']
            self.disconnect_records = records.copy()
            self.stage = 'reconnect'
            self.settle_signature = self.settle_since = None
            self.message = (self.target_id + ' removal and stale-activity invalidation confirmed.\n'
                            'NOW RECONNECT the cable for ' + self.target_id + '.\n'
                            'Keep the mouse still and wait for output recovery.')
            return

        if self.stage == 'reconnect':
            if current['topologyGeneration'] == self.disconnected_generation:
                return
            if set(current_ids) not in ({self.survivor_id}, set(ids(self.initial_geometry))):
                raise RuntimeError('An unexpected physical output identity appeared during reconnect')
            signature = (current['topologyGeneration'], json.dumps(geometry, sort_keys=True))
            if signature != self.settle_signature:
                self.settle_signature, self.settle_since = signature, now
                return
            if now - self.settle_since < .75:
                return
            if not same_geometry(geometry, self.initial_geometry):
                raise RuntimeError('Reconnected outputs did not restore their initial geometry and scale')
            reconnect_records = [row for row in records
                                 if row['topologyGeneration'] > self.disconnected_generation]
            if (not reconnect_records or
                    not any(same_geometry(row['outputGeometries'], self.initial_geometry)
                            for row in reconnect_records)):
                raise RuntimeError('No synchronous reset captured the restored two-output topology')
            self.return_baseline, self.return_origin = current, facts
            self.return_clients = clients.copy()
            self.topology_records = records
            self.result = {
                'targetConnector': self.target_id,
                'survivingConnector': self.survivor_id,
                'initialInput': self.initial_input,
                'initialOutputGeometries': self.initial_geometry,
                'disconnectedBaseline': self.disconnected_baseline,
                'disconnectedOutputGeometries': self.disconnected_geometry,
                'returnedBaseline': self.return_baseline,
                'returnedOutputGeometries': geometry,
                'topologyResets': self.topology_records,
                'disconnectResetCount': len(self.disconnect_records),
                'reconnectResetCount': len(reconnect_records),
            }
            self.stage = 'recovered'
            self.message = 'Two-output geometry recovered. Preparing fresh input checks on both monitors.'


def recovery_stage(directory, env, kwin, client, cycle, stage, index, end):
    event(directory, 'recovery-stage-starting', output=stage['name'], index=index)
    publish_instruction(directory, 'Moving the recovered input window to ' + stage['name'] + '. Please wait.')
    package_id = placement.load_stage(directory, env, index + 20, stage, cycle.initial_geometry)
    ready = None
    placement_end = min(end, time.monotonic() + 10)
    while ready is None:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Recovered input placement aborted: compositor/client exited')
        if time.monotonic() >= placement_end:
            raise RuntimeError('Recovered input placement readiness missing for ' + stage['name'] +
                               '; inspect OWG_DIRECT_SCOPE markers in kwin.log')
        ready = placement.check_log((directory / 'kwin.log').read_text(errors='replace'), index + 20)
        time.sleep(.08)
    frame = placement.validate_frame(ready, stage, cycle.initial_geometry)
    sample = None
    while sample is None:
        if kwin.poll() is not None or client.poll() is not None or time.monotonic() >= end:
            raise RuntimeError('Recovered input baseline was unavailable')
        sample = placement.coherent_state(env)
        time.sleep(.04)
    baseline, origin_before, geometry = sample
    verify_continuity(cycle.return_baseline, baseline)
    verify_environment(origin_before, cycle.vt)
    if not same_geometry(geometry, cycle.initial_geometry):
        raise RuntimeError('Recovered output geometry changed before fresh input')
    path = directory / 'client.log'
    clients_before = client_counts(path)
    publish_instruction(directory, 'RECOVERY ' + str(index + 1) + '/2 on ' + stage['name'] + ':\n'
                        'Click here, move, scroll and type a key.\n'
                        'Hold the left button for 3 seconds, then release.')
    print('DIRECT_HOTPLUG_RECOVERY_READY:', stage['name'], flush=True)
    held = released = False
    while time.monotonic() < end:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Recovered two-output input stage aborted')
        placement.check_log((directory / 'kwin.log').read_text(errors='replace'), index + 20)
        sample = placement.coherent_state(env)
        if sample is None:
            time.sleep(.06)
            continue
        current, facts, geometry = sample
        verify_continuity(baseline, current)
        verify_environment(facts, cycle.vt)
        if not same_geometry(geometry, cycle.initial_geometry):
            raise RuntimeError('Recovered output geometry changed during fresh input')
        clients = client_counts(path)
        held |= (current.get('buttonKnown') is True and current.get('buttonsDown') is True
                 and current['button'] > baseline['button'])
        released |= held and current.get('buttonKnown') is True and current.get('buttonsDown') is False
        progress = {'stage': stage['name'], 'baseline': baseline, 'current': current,
                    'originBefore': origin_before, 'origin': facts,
                    'clientBefore': clients_before, 'client': clients,
                    'heldObserved': held, 'releaseObserved': released,
                    'outputs': geometry}
        checkpoint(directory, 'hotplug-recovery-progress.json', progress)
        if candidate_complete(baseline, current, origin_before, facts, clients_before,
                              clients, held, released):
            evidence = verify_result(
                baseline, current, origin_before, facts,
                {key: clients[key] - clients_before[key] for key in KINDS}, held, released)
            old, new = placement.output_counts(baseline), placement.output_counts(current)
            if new[stage['name']] <= old[stage['name']]:
                raise RuntimeError('Fresh recovered input did not reach its intended output')
            evidence.update(name=stage['name'], frame=frame,
                            expectedOutput=stage['name'],
                            outputEventDeltas={name: new[name] - old[name] for name in old})
            placement.unload_stage(env, package_id)
            evidence['placementScriptUnloaded'] = True
            print('DIRECT_HOTPLUG_RECOVERY_STAGE: PASS', stage['name'], flush=True)
            return evidence, facts
        time.sleep(.08)
    raise RuntimeError('Fresh recovered input was incomplete before the deadline')


def observe(directory, env, kwin, client, seconds, vt, connector='HDMI'):
    event(directory, 'hotplug-observation-starting', connector=connector)
    end = time.monotonic() + seconds
    sample = None
    while sample is None:
        if kwin.poll() is not None or client.poll() is not None or time.monotonic() >= end:
            raise RuntimeError('Physical hotplug initial topology was unavailable')
        sample = placement.coherent_state(env)
        time.sleep(.05)
    initial, origin, geometry = sample
    verify_environment(origin, vt)
    target = select_target(geometry, connector)
    survivor = next(row['id'] for row in geometry if row['id'] != target)
    stages = placement.contained_stages(geometry, centered=True, top_inset=200)
    survivor_stage = next(stage for stage in stages if stage['name'] == survivor)
    package_id = placement.load_stage(directory, env, 19, survivor_stage, geometry)
    ready = None
    while ready is None:
        if kwin.poll() is not None or client.poll() is not None or time.monotonic() >= end:
            raise RuntimeError('Could not place the hotplug instructions on the surviving output')
        ready = placement.check_log((directory / 'kwin.log').read_text(errors='replace'), 19)
        time.sleep(.08)
    placement.validate_frame(ready, survivor_stage, geometry)
    placement.unload_stage(env, package_id)
    sample = None
    while sample is None:
        if kwin.poll() is not None or client.poll() is not None or time.monotonic() >= end:
            raise RuntimeError('Physical hotplug baseline was unavailable before the deadline')
        sample = placement.coherent_state(env)
        time.sleep(.04)
    baseline, origin, geometry = sample
    cycle = HotplugCycle(baseline, origin, client_counts(directory / 'client.log'),
                         geometry, vt, target)
    publish_instruction(directory, cycle.message)
    while time.monotonic() < end and cycle.result is None:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('Physical hotplug compositor/client exited')
        event(directory, 'hotplug-sample-request', stage=cycle.stage)
        sample = placement.coherent_state(env, include_trace=True)
        if sample is None:
            event(directory, 'hotplug-sample-incoherent', stage=cycle.stage)
            time.sleep(.06)
            continue
        current, facts, geometry, trace = sample
        clients = client_counts(directory / 'client.log')
        advance_with_evidence(directory, cycle, current, facts, trace, clients,
                              geometry, time.monotonic())
        publish_instruction(directory, cycle.message)
        time.sleep(.08)
    if cycle.result is None:
        raise RuntimeError('Physical ' + connector + ' disconnect/reconnect did not complete before the deadline')
    recovered = []
    final_facts = cycle.return_origin
    for index, stage in enumerate(placement.contained_stages(
            cycle.initial_geometry, centered=True, top_inset=200)):
        evidence, final_facts = recovery_stage(
            directory, env, kwin, client, cycle, stage, index, end)
        recovered.append(evidence)
    cycle.result['recoveredInputStages'] = recovered
    publish_instruction(directory, 'Physical connector hotplug and two-output recovery passed. Checking cleanup.')
    return {'hotplug': cycle.result, 'environment': final_facts}

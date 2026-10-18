"""Ordered physical VT-cycle evidence checks; no session-changing commands."""
import ast
import json
import math
import subprocess
import time

from direct_warning import publish_instruction
from run_input_direct import KINDS, environment, snapshot, verify_continuity, verify_environment, verify_result

TOPOLOGY_TRACE_LIMIT = 64


def transitions(env):
    reply = subprocess.run(['gdbus', 'call', '--session', '--dest', 'org.kde.KWin',
                            '--object-path', '/org/oledwindowguard/InputProbe', '--method',
                            'org.oledwindowguard.InputProbe1.ActivityTransitions'],
                           env=env, check=True, text=True, capture_output=True, timeout=3)
    return json.loads(ast.literal_eval(reply.stdout.strip())[0])


def identity(baseline, current, allow_topology=False):
    for field in ('epoch',) if allow_topology else ('epoch', 'topologyGeneration'):
        if current.get(field) != baseline.get(field):
            raise RuntimeError('Observer epoch or physical topology changed during VT cycle')
    if (current.get('schema') != 2 or current.get('outputIdentityComplete') is not True or
            not current.get('outputs') or current.get('lockState') in ('locked', 'locking') or
            current.get('sleepState') == 'sleeping'):
        raise RuntimeError('Incomplete, locked or sleeping VT observation')
    if ([row['id'] for row in current['outputs']] != [row['id'] for row in baseline['outputs']] or
            type(current.get('sessionGeneration')) is not int or current['sessionGeneration'] < 0):
        raise RuntimeError('Output identity or session generation became invalid')
    for field in (*KINDS, 'sequence', 'topologyGeneration'):
        if type(current.get(field)) is not int or current[field] < 0:
            raise RuntimeError('Invalid VT event counters')


def output_geometry(baseline, rows):
    if (not isinstance(rows, list) or len(rows) > 16 or
            [r.get('id') for r in rows] != [r['id'] for r in baseline['outputs']]):
        raise RuntimeError('Missing or changed physical output geometry identities')
    for row in rows:
        if (any(type(row.get(k)) not in (int, float) or not math.isfinite(row[k])
                for k in ('x', 'y', 'width', 'height', 'scale')) or
                row['width'] <= 0 or row['height'] <= 0 or not .25 <= row['scale'] <= 8):
            raise RuntimeError('Invalid physical output geometry')
    return rows


def reset_record(baseline, record):
    identity(baseline, record, allow_topology=True)
    if (record.get('buttonKnown') is not False or record.get('buttonsDown') is not False or
            record.get('ageMs') != -1 or any(row.get('events') != 0 for row in record['outputs'])):
        raise RuntimeError('Boundary did not synchronously invalidate old input facts')
    if type(record.get('monotonicMs')) is not int or record['monotonicMs'] < 0:
        raise RuntimeError('Invalid boundary time')
    output_geometry(baseline, record.get('outputGeometries'))


def boundary(baseline, record, active, generation):
    reset_record(baseline, record)
    if (record.get('active') is not active or record['sessionGeneration'] != generation or
            record.get('sessionActivity') != ('active' if active else 'inactive')):
        raise RuntimeError('Unexpected activity boundary')


class VTCycle:
    def __init__(self, baseline, origin, client_before, vt):
        verify_continuity(baseline, baseline)
        verify_environment(origin, vt)
        if vt == 2:
            raise RuntimeError('VT-cycle fixture requires a spare VT distinct from the KDE desktop on VT 2')
        self.baseline, self.origin, self.client_before, self.vt = baseline, origin, client_before, vt
        self.stage = 'input'
        self.message = ('Click here, move, scroll and type a key.\n'
                        'Then HOLD the left mouse button for 3 seconds and keep it held.\n'
                        'Wait for NOW SWITCH TO KDE before switching terminals.')
        self.inactive_samples = 0
        self.inactive_since = None
        self.inactive_span = 0
        self.fresh_held = self.fresh_released = False
        self.result = None
        self.settle_signature = None
        self.initial_outputs = None

    def advance(self, current, facts, trace, clients, now):
        identity(self.baseline, current, allow_topology=True)
        if trace.get('schema') != 1 or trace.get('epoch') != self.baseline['epoch']:
            raise RuntimeError('Missing or stale synchronous activity-transition evidence')
        rows = trace.get('transitions')
        if not isinstance(rows, list) or len(rows) > 8:
            raise RuntimeError('Invalid bounded activity-transition record')
        if any(type(row.get('sessionGeneration')) is not int for row in rows):
            raise RuntimeError('Invalid activity-transition generation')
        rows = [row for row in rows if row['sessionGeneration'] > self.baseline['sessionGeneration']]
        if len(rows) > 2:
            raise RuntimeError('Unexpected extra session transitions')
        for i, row in enumerate(rows):
            boundary(self.baseline, row, i == 1, self.baseline['sessionGeneration'] + i + 1)
        if len(rows) == 2 and rows[1]['monotonicMs'] <= rows[0]['monotonicMs']:
            raise RuntimeError('Activity boundary times are not ordered')
        geometry = output_geometry(self.baseline, trace.get('outputGeometries'))
        if self.initial_outputs is None:
            self.initial_outputs = geometry
        topology = trace.get('topologyTransitions')
        if not isinstance(topology, list) or len(topology) > TOPOLOGY_TRACE_LIMIT:
            raise RuntimeError('Missing bounded topology-reset evidence')
        topology = [r for r in topology if r.get('topologyGeneration', -1) > self.baseline['topologyGeneration']]
        for i, row in enumerate(topology):
            reset_record(self.baseline, row)
            if (row['topologyGeneration'] != self.baseline['topologyGeneration'] + i + 1 or
                    (i and row['monotonicMs'] < topology[i - 1]['monotonicMs'])):
                raise RuntimeError('Missing or unordered topology-reset record')
            if not rows:
                if self.stage == 'away':
                    return  # A poll can land between output reset and logind inactivity.
                raise RuntimeError('Topology reset preceded the armed VT switch')
            generation = row['sessionGeneration']
            if generation == self.baseline['sessionGeneration']:
                valid = (row['sessionActivity'] == self.baseline['sessionActivity'] and
                         row['monotonicMs'] <= rows[0]['monotonicMs'] and
                         row['topologyGeneration'] <= rows[0]['topologyGeneration'] and
                         row['outputGeometries'] == self.initial_outputs and
                         all(row[k] == rows[0][k] for k in (*KINDS, 'sequence')))
            elif generation == rows[0]['sessionGeneration']:
                valid = (row['sessionActivity'] == 'inactive' and
                         row['monotonicMs'] >= rows[0]['monotonicMs'] and
                         (len(rows) == 1 or row['monotonicMs'] <= rows[1]['monotonicMs']) and
                         all(row[k] == rows[0][k] for k in (*KINDS, 'sequence')))
            elif len(rows) == 2 and generation == rows[1]['sessionGeneration']:
                valid = row['sessionActivity'] == 'active' and row['monotonicMs'] >= rows[1]['monotonicMs']
            else:
                valid = False
            if not valid:
                raise RuntimeError('Topology reset did not belong to the observed VT boundary sequence')
        expected_topology = self.baseline['topologyGeneration'] + len(topology)
        expected = self.baseline['sessionGeneration'] + len(rows)
        # D-Bus reads can straddle a boundary. Require a coherent sample before progress.
        if current['sessionGeneration'] < expected or current['topologyGeneration'] < expected_topology:
            return
        if current['topologyGeneration'] != expected_topology:
            raise RuntimeError('Topology transition missing from bounded record')
        if current['sessionGeneration'] != expected:
            raise RuntimeError('Session transition missing from bounded record')
        active = current.get('sessionActivity') != 'inactive'
        if facts.get('sessionActive') is not active:
            return
        verify_environment(facts, self.vt, active=active)
        if self.stage == 'input':
            verify_continuity(self.baseline, current)
            if (all(clients[k] > self.client_before[k] for k in KINDS) and
                    all(current[k] > self.baseline[k] for k in KINDS) and
                    all(facts['libinput'][k] > self.origin['libinput'][k] for k in KINDS) and
                    current.get('buttonKnown') is True and current.get('buttonsDown') is True):
                self.held = current
                self.initial_input = {
                    'observer': {k: current[k] - self.baseline[k] for k in KINDS},
                    'libinput': {k: facts['libinput'][k] - self.origin['libinput'][k] for k in KINDS},
                    'client': {k: clients[k] - self.client_before[k] for k in KINDS}}
                self.stage = 'away'
                self.message = ('NOW SWITCH TO KDE with Ctrl+Alt+F2, keeping the left button held.\n'
                                'When KDE appears, release the button and move the pointer.\n'
                                'Wait 5 seconds, then return with Ctrl+Alt+F' + str(self.vt) + '.')
        elif self.stage in ('away', 'inactive'):
            if not rows:
                verify_continuity(self.baseline, current)
                return
            if len(rows) == 1:
                if current.get('sessionActivity') != 'inactive':
                    return
                if self.inactive_since is None:
                    self.inactive_since = now
                    self.inactive_origin = facts['libinput'].copy()
                    self.inactive = rows[0]
                if (any(current[k] != self.inactive[k] for k in (*KINDS, 'sequence')) or
                        current.get('buttonKnown') is not False or
                        facts['libinput'] != self.inactive_origin):
                    raise RuntimeError('Private observer accepted input while VT was inactive')
                self.inactive_samples += 1
                self.inactive_span = now - self.inactive_since
                self.stage = 'inactive'
            else:
                if self.inactive_samples < 2 or self.inactive_span < 1:
                    raise RuntimeError('Inactive VT was not observed long enough; wait five seconds on KDE')
                if any(rows[1][k] != rows[0][k] for k in (*KINDS, 'sequence')):
                    raise RuntimeError('Input counters changed between inactive and active boundaries')
                if current.get('sessionActivity') != 'active':
                    return
                self.returned = rows[1]
                self.stage = 'settle'
                self.message = 'Back on the test VT. Waiting for output refresh to settle; please wait.'
        if self.stage == 'settle':
            signature = (current['topologyGeneration'], json.dumps(geometry, sort_keys=True))
            if signature != self.settle_signature:
                self.settle_signature, self.settle_since = signature, now
            elif now - self.settle_since >= .5 and current.get('buttonsDown') is not True:
                self.return_baseline, self.return_origin, self.return_clients = current, facts, clients.copy()
                self.return_outputs, self.topology_resets = geometry, topology
                self.stage = 'fresh'
                self.message = ('BACK ON TEST VT: click here, move, scroll and type a key again.\n'
                                'Hold the left button for 3 seconds, then RELEASE.\n'
                                'The test closes automatically when fresh input is confirmed.')
        elif self.stage == 'fresh':
            verify_continuity(self.return_baseline, current)
            if geometry != self.return_outputs:
                raise RuntimeError('Output geometry changed during fresh-input observation')
            self.fresh_held |= (current.get('buttonKnown') is True and current.get('buttonsDown') is True
                                and current['button'] > self.return_baseline['button'])
            self.fresh_released |= (self.fresh_held and current.get('buttonKnown') is True
                                    and current.get('buttonsDown') is False)
            counts = {k: clients[k] - self.return_clients[k] for k in KINDS}
            if (all(counts[k] > 0 and current[k] > self.return_baseline[k] and
                    facts['libinput'][k] > self.return_origin['libinput'][k] for k in KINDS)
                    and self.fresh_released and current.get('buttonsDown') is False):
                resumed = verify_result(self.return_baseline, current, self.return_origin, facts,
                                        counts, self.fresh_held, self.fresh_released)
                self.result = {'initialInput': self.initial_input, 'heldBeforeSwitch': self.held,
                               'inactiveBoundary': self.inactive, 'activeBoundary': self.returned,
                               'inactiveSamples': self.inactive_samples,
                               'inactiveObservedMs': round(self.inactive_span * 1000),
                               'returnBaseline': self.return_baseline, 'resumedInput': resumed,
                               'initialOutputGeometries': self.initial_outputs,
                               'returnedOutputGeometries': self.return_outputs,
                               'vtTopologyResets': self.topology_resets}
                self.stage = 'done'
                self.message = 'VT-cycle check passed. Waiting for cleanup.'


def observe(directory, env, kwin, client, seconds, vt):
    baseline, origin = snapshot(env), environment(env)
    path = directory / 'client.log'
    def counts():
        log = path.read_text(errors='replace')
        return {kind: log.count('CLIENT_EVENT ' + kind) for kind in KINDS}
    check = VTCycle(baseline, origin, counts(), vt)
    publish_instruction(directory, check.message)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if kwin.poll() is not None or client.poll() is not None:
            raise RuntimeError('VT check aborted or private compositor/client exited')
        current, facts = snapshot(env), environment(env)
        trace, clients = transitions(env), counts()
        progress = {'stage': check.stage, 'baseline': baseline, 'current': current,
                    'environment': facts, 'transitions': trace, 'clientCounters': clients,
                    'heldBeforeSwitch': getattr(check, 'held', None),
                    'returnBaseline': getattr(check, 'return_baseline', None),
                    'returnOrigin': getattr(check, 'return_origin', None),
                    'returnClients': getattr(check, 'return_clients', None),
                    'inactiveSamples': check.inactive_samples,
                    'inactiveObservedMs': round(check.inactive_span * 1000)}
        temporary = directory / 'vt-progress.tmp'
        temporary.write_text(json.dumps(progress, indent=2) + '\n')
        temporary.replace(directory / 'vt-progress.json')
        check.advance(current, facts, trace, clients, time.monotonic())
        publish_instruction(directory, check.message)
        if check.result:
            return {'vtCycle': check.result, 'environment': facts}
        time.sleep(.1)
    raise RuntimeError('VT-cycle sequence incomplete before deadline; artifacts preserve failure')

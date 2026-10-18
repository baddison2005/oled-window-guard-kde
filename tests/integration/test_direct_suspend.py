"""Real-suspend evidence must reject synthetic clocks, lost resets and stale input."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from direct_suspend import suspended_ms, validate_trace, suspend_preflight
from test_direct_lock import fixture
from test_input_direct import backend


def data():
    baseline, geometry, rows, _ = fixture()
    rows = rows[:2]
    for row, reason in zip(rows, ('sleeping', 'awake')):
        row.update(reason=reason, sleepState=reason, lockState=baseline['lockState'])
    trace = dict(schema=1, epoch=baseline['epoch'], lifecycleTraceCapacity=16,
                 topologyTraceCapacity=64, lifecycleTransitions=rows, topologyTransitions=[],
                 outputGeometries=geometry)
    return baseline, geometry, rows, trace


class SuspendTests(unittest.TestCase):
    def setUp(self):
        self.baseline, self.geometry, self.rows, self.trace = data()
        self.current = deepcopy(self.rows[-1])

    def check(self):
        return validate_trace(self.baseline, self.current, self.trace, self.geometry)

    def test_complete_two_output_sleep_wake(self):
        self.assertEqual(self.check(), (self.rows, []))

    def test_optional_inactive_active_resets(self):
        inactive = deepcopy(self.rows[0]); inactive.update(reason='inactive', sessionActivity='inactive',
                                                          sessionGeneration=2, monotonicMs=1500)
        self.rows[-1].update(sessionActivity='inactive', sessionGeneration=3, monotonicMs=2000)
        active = deepcopy(self.rows[-1]); active.update(reason='active', sessionActivity='active',
                                                       sessionGeneration=4, monotonicMs=2500)
        self.trace['lifecycleTransitions'] = [self.rows[0], inactive, self.rows[-1], active]
        self.current = deepcopy(active)
        self.assertEqual(len(self.check()[0]), 4)

    def test_extra_cycle_or_lock_refused(self):
        for reason in ('sleeping', 'locked', 'locking', 'unlocked', 'inactive'):
            with self.subTest(reason=reason):
                self.setUp(); self.rows[-1]['reason'] = reason
                if reason == 'inactive':
                    self.rows[-1]['sessionActivity'] = 'inactive'
                with self.assertRaises(RuntimeError): self.check()

    def test_missing_or_reordered_sleep(self):
        self.trace['lifecycleTransitions'] = self.rows[1:]
        with self.assertRaises(RuntimeError): self.check()

    def test_missing_generation_and_overflow(self):
        for field in ('lifecycleTransitions', 'topologyTransitions'):
            with self.subTest(field=field):
                self.setUp(); self.trace[field] = [deepcopy(self.rows[0])] * 65
                with self.assertRaises(RuntimeError): self.check()
        self.setUp(); self.current['sessionGeneration'] += 1
        with self.assertRaises(RuntimeError): self.check()

    def test_stale_boundary_fields(self):
        for key, value in (('buttonKnown', True), ('buttonsDown', True), ('ageMs', 0),
                           ('outputIdentityComplete', False), ('epoch', 'stale'),
                           ('monotonicMs', -1), ('lockState', 'locked'), ('keyboard', -1)):
            with self.subTest(key=key):
                self.setUp(); self.rows[0][key] = value
                with self.assertRaises(RuntimeError): self.check()

    def test_second_output_stale_activity_refused(self):
        self.rows[0]['outputs'][1]['events'] = 1
        with self.assertRaises(RuntimeError): self.check()

    def test_observer_cannot_accept_input_between_sleep_and_wake(self):
        self.rows[-1]['keyboard'] += 1
        with self.assertRaisesRegex(RuntimeError, 'accepted input'): self.check()

    def test_sleeping_poll_must_stay_clear(self):
        self.trace['lifecycleTransitions'] = self.rows[:1]
        self.current = deepcopy(self.rows[0]); self.current['buttonKnown'] = True
        with self.assertRaises(RuntimeError): self.check()

    def test_topology_reset_retained_at_resume(self):
        topology = deepcopy(self.rows[-1]); topology.pop('reason')
        topology.update(topologyGeneration=1, monotonicMs=2500)
        self.trace['topologyTransitions'] = [topology]
        self.current = deepcopy(topology)
        self.assertEqual(self.check()[1], [topology])
        self.trace['topologyTransitions'] = []
        with self.assertRaises(RuntimeError): self.check()

    def test_topology_reset_cannot_accept_sleeping_input(self):
        row = deepcopy(self.rows[0]); row.update(topologyGeneration=1, monotonicMs=1500, keyboard=99)
        self.trace['topologyTransitions'] = [row]
        self.rows[-1]['topologyGeneration'] = 1
        self.current['topologyGeneration'] = 1
        with self.assertRaises(RuntimeError): self.check()

    def test_lifecycle_cannot_invent_topology_generation(self):
        self.rows[0]['topologyGeneration'] = 10
        with self.assertRaises(RuntimeError): self.check()

    def test_straddled_generation_read_retried(self):
        self.current['sessionGeneration'] = 1
        self.assertIsNone(self.check())

    def test_current_epoch_or_injection_scope_not_accepted(self):
        self.current['epoch'] = 'replacement'
        with self.assertRaises(RuntimeError): self.check()

    def test_real_suspended_duration_requires_boot_clock_gap(self):
        before = dict(bootID='same', monotonicNs=1000000000, boottimeNs=1000000000)
        after = dict(bootID='same', monotonicNs=2000000000, boottimeNs=7000000000)
        self.assertEqual(suspended_ms(before, after), 5000)
        for change in ({'boottimeNs':2000000000}, {'bootID':'new'}, {'monotonicNs':0},
                       {'boottimeNs':4000000000}):
            with self.subTest(change=change):
                with self.assertRaises(RuntimeError): suspended_ms(before, dict(after, **change))

    def test_preflight_only_checks_permission(self):
        with patch('direct_suspend.Path.read_text', return_value='freeze mem disk'), \
                patch('direct_suspend.clock_sample'), patch('direct_suspend.subprocess.run') as run:
            run.return_value.stdout = "('yes',)"
            suspend_preflight()
            self.assertIn('org.freedesktop.login1.Manager.CanSuspend', run.call_args.args[0])
            run.return_value.stdout = "('challenge',)"
            with self.assertRaises(RuntimeError): suspend_preflight()

    def test_cli_requires_durable_recording(self):
        import run_input_direct as runner
        with patch.object(sys, 'argv', ['runner', '--suspend-cycle']), \
                patch.object(runner, 'preflight') as preflight, patch('sys.stderr'):
            with self.assertRaises(SystemExit): runner.main()
            preflight.assert_not_called()

    def test_worker_receives_explicit_suspend_flag(self):
        import run_input_direct as runner
        with patch.object(sys, 'argv', ['runner', '--suspend-cycle', '--worker', '/tmp/private']), \
                patch.object(runner, 'worker') as work:
            self.assertEqual(runner.main(), 0)
            self.assertTrue(work.call_args.args[-1])
            self.assertFalse(work.call_args.args[-2])

    def test_observation_requests_one_suspend_and_checks_each_recovered_output(self):
        import direct_suspend as module
        baseline, geometry, rows, trace = data()
        held = deepcopy(rows[0])
        held.update(sessionGeneration=baseline['sessionGeneration'], sleepState=baseline['sleepState'],
                    buttonKnown=True, buttonsDown=True, ageMs=0)
        initial_facts = backend()
        input_facts = backend(); input_facts['libinput'] = {k: 2 for k in module.KINDS}
        initial_trace = dict(trace, lifecycleTransitions=[])
        resumed = False
        ticks = iter(i * .5 for i in range(1000))
        def request(*args, **kwargs):
            nonlocal resumed
            self.assertTrue(held['buttonsDown'])
            self.assertEqual(args[0], ['systemctl', '--no-ask-password', 'suspend'])
            resumed = True
            return Mock(returncode=0, stdout='', stderr='')
        def coherent(_):
            return (deepcopy(rows[-1]), input_facts, geometry) if resumed else (baseline, initial_facts, geometry)
        clocks = [dict(bootID='same', monotonicNs=1000000000, boottimeNs=1000000000),
                  dict(bootID='same', monotonicNs=2000000000, boottimeNs=7000000000)]
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module.placement, 'coherent_state', side_effect=coherent), \
                patch.object(module, 'snapshot', side_effect=lambda _: deepcopy(rows[-1] if resumed else held)), \
                patch.object(module, 'environment', return_value=input_facts), \
                patch.object(module, 'transitions', side_effect=lambda _: trace if resumed else initial_trace), \
                patch.object(module, 'client_counts', side_effect=lambda _: {k: 2 for k in module.KINDS}), \
                patch.object(module, 'publish_instruction'), patch.object(module, 'checkpoint'), \
                patch.object(module, 'event'), patch.object(module, 'clock_sample', side_effect=clocks), \
                patch.object(module.time, 'monotonic', side_effect=lambda: next(ticks)), \
                patch.object(module.time, 'sleep'), \
                patch.object(module.subprocess, 'run', side_effect=request) as run, \
                patch.object(module, 'recovery_stage', return_value=({'pass':True}, input_facts)) as recover:
            # First read is the client baseline; later reads contain fresh events.
            module.client_counts.side_effect = [{k:0 for k in module.KINDS}] + [{k:2 for k in module.KINDS}]*100
            result = module.observe(Path(temporary), {}, Mock(poll=lambda: None), Mock(poll=lambda: None), 120, 3)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(recover.call_count, 2)
        self.assertEqual({call.args[5]['name'] for call in recover.call_args_list}, {r['id'] for r in geometry})
        self.assertEqual(result['suspendCycle']['suspendedMs'], 5000)

    def test_incomplete_input_never_suspends_system(self):
        import direct_suspend as module
        baseline, geometry, _, trace = data()
        trace['lifecycleTransitions'] = []
        facts = backend()
        ticks = iter(range(100))
        with tempfile.TemporaryDirectory() as temporary, \
                patch.object(module.placement, 'coherent_state', return_value=(baseline, facts, geometry)), \
                patch.object(module, 'snapshot', return_value=baseline), \
                patch.object(module, 'environment', return_value=facts), \
                patch.object(module, 'transitions', return_value=trace), \
                patch.object(module, 'client_counts', return_value={k:0 for k in module.KINDS}), \
                patch.object(module, 'publish_instruction'), patch.object(module, 'checkpoint'), \
                patch.object(module.time, 'monotonic', side_effect=lambda: next(ticks)), \
                patch.object(module.time, 'sleep'), patch.object(module.subprocess, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'incomplete before deadline'):
                module.observe(Path(temporary), {}, Mock(poll=lambda: None), Mock(poll=lambda: None), 15, 3)
            run.assert_not_called()


if __name__ == '__main__': unittest.main()

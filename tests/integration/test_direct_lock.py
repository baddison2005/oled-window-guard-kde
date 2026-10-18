"""Lifecycle acceptance rejects lost resets and input accepted during lock."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parent))
from direct_lock import prepare_authentication, screen_saver, validate_trace
from test_direct_vt import data


def fixture():
    baseline, held, _, _ = data()
    geometry = [{'id':'DP-1','x':1920,'y':0,'width':3413,'height':1440,'scale':1.5},
                {'id':'HDMI-A-1','x':0,'y':0,'width':1920,'height':1080,'scale':1}]
    for state in (baseline, held):
        state['outputs'].append({'id':'HDMI-A-1','events':0})
    rows = []
    for i, reason in enumerate(('locking','locked','unlocked'),1):
        r=deepcopy(held)
        r.update(sessionGeneration=i, reason=reason, lockState=reason,
                 buttonKnown=False, buttonsDown=False, ageMs=-1, monotonicMs=i*1000,
                 outputGeometries=geometry)
        r['outputs']=[{'id':g['id'],'events':0} for g in geometry]
        rows.append(r)
    trace={'schema':1,'epoch':baseline['epoch'],'lifecycleTraceCapacity':16,
           'lifecycleTransitions':rows}
    return baseline, geometry, rows, trace


class LockTests(unittest.TestCase):
    def setUp(self):
        self.baseline,self.geometry,self.rows,self.trace=fixture()
        self.current=deepcopy(self.rows[-1])

    def check(self):
        return validate_trace(self.baseline,self.current,self.trace,self.geometry)

    def test_complete_two_output_reset_sequence(self):
        self.assertEqual(self.check(),self.rows)

    def test_greeter_startup_false_notification_stays_locking(self):
        repeat=deepcopy(self.rows[0]);repeat["sessionGeneration"]=2;repeat["monotonicMs"]=1500
        self.rows[1]["sessionGeneration"]=3;self.rows[2]["sessionGeneration"]=4
        self.trace["lifecycleTransitions"]=[self.rows[0],repeat,*self.rows[1:]]
        self.current=deepcopy(self.rows[-1])
        self.assertEqual(len(self.check()),4)

    def test_missing_or_reordered_boundary(self):
        for order in ([0,2],[1,0,2],[0,1,1,2]):
            with self.subTest(order=order):
                self.trace['lifecycleTransitions']=[self.rows[i] for i in order]
                with self.assertRaises(RuntimeError):self.check()

    def test_boundary_safety_fields(self):
        changes={'buttonKnown':True,'buttonsDown':True,'ageMs':0,'epoch':'stale',
                 'sessionGeneration':8,'sessionActivity':'inactive','sleepState':'sleeping',
                 'lockState':'unlocked','reason':'awake','topologyGeneration':99,
                 'outputIdentityComplete':False,'monotonicMs':-1,'keyboard':-1}
        for key,value in changes.items():
            with self.subTest(key=key):
                self.setUp();self.rows[1][key]=value
                with self.assertRaises(RuntimeError):self.check()

    def test_stale_second_output(self):
        self.rows[1]['outputs'][1]['events']=1
        with self.assertRaises(RuntimeError):self.check()

    def test_input_during_lock(self):
        self.rows[2]['keyboard']+=1
        with self.assertRaisesRegex(RuntimeError,'accepted input'):self.check()

    def test_changed_scale(self):
        self.rows[1]['outputGeometries']=deepcopy(self.geometry)
        self.rows[1]['outputGeometries'][1]['scale']=1.25
        with self.assertRaises(RuntimeError):self.check()

    def test_missing_current_generation(self):
        self.current['sessionGeneration']=4
        with self.assertRaises(RuntimeError):self.check()

    def test_straddled_reads_retry(self):
        self.current['sessionGeneration']=2
        self.assertIsNone(self.check())

    def test_locked_poll_must_remain_clear(self):
        self.trace['lifecycleTransitions']=self.rows[:2]
        self.current=deepcopy(self.rows[1]);self.current['keyboard']+=1
        with self.assertRaises(RuntimeError):self.check()

    def test_changed_current_epoch_or_outputs(self):
        for key,value in (("epoch","other"),("outputIdentityComplete",False),("outputs",[])):
            with self.subTest(key=key):
                self.setUp();self.current[key]=value
                with self.assertRaises(RuntimeError):self.check()

    def test_worker_receives_lock_flag_without_parent_record_option(self):
        import run_input_direct as runner
        with patch.object(sys,"argv",["runner","--lock-cycle","--worker","/tmp/private"]), patch.object(runner,"worker") as work:
            self.assertEqual(runner.main(),0)
            self.assertTrue(work.call_args.args[-2])

    def test_parent_refuses_missing_durable_destination(self):
        import run_input_direct as runner
        with patch.object(sys,"argv",["runner","--lock-cycle"]), patch.object(runner,"preflight") as preflight, patch("sys.stderr"):
            with self.assertRaises(SystemExit):runner.main()
            preflight.assert_not_called()

    def test_overflow_refused(self):
        self.trace['lifecycleTransitions']=self.rows*6
        with self.assertRaises(RuntimeError):self.check()

    def test_unexpected_session_boundary_refused(self):
        self.rows[1]['reason']='inactive'
        with self.assertRaises(RuntimeError):self.check()

    def test_private_authentication_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir();prepare_authentication(root)
            config=(root/'config/kscreenlockerrc').read_text()
            self.assertIn('RequirePassword=true',config);self.assertIn('LockGrace=0',config)

    def test_private_dbus_boolean_parsing(self):
        for reply,expected in [('(true,)',(True,)),('(false,)',(False,))]:
            with patch('direct_lock.subprocess.run') as run:
                run.return_value.stdout=reply
                self.assertEqual(screen_saver({},'GetActive'),expected)
        with patch('direct_lock.subprocess.run') as run:
            run.return_value.stdout='unknown'
            with self.assertRaises(RuntimeError):screen_saver({},'GetActive')


if __name__=='__main__':unittest.main()

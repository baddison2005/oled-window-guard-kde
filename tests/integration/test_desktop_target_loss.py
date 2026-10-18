import json
from pathlib import Path
import tempfile
import unittest
from tools.desktop_target_loss import check_fixture_scope, close_targets


class TargetLossTests(unittest.TestCase):
    def test_requires_two_visible_windows_on_each_output(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'fixture.jsonl'
            windows=[dict(visible=True,screen=s) for s in ('DP-1','DP-1','HDMI-A-1','HDMI-A-1')]
            p.write_text(json.dumps(dict(windows=windows))+'\n')
            check_fixture_scope(p,'DP-1')
            windows[0]['visible']=False
            p.write_text(json.dumps(dict(windows=windows))+'\n')
            with self.assertRaisesRegex(RuntimeError,'two visible'):
                check_fixture_scope(p,'DP-1')

    def exercise(self,acknowledge=True,closed=2,stale=False):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'fixture.jsonl'
            now=[0]
            self.rows=[]
            def sleep(seconds):
                now[0]+=seconds
                if acknowledge:
                    request=json.loads(p.with_suffix('.close-output').read_text())
                    p.with_suffix('.close-ack').write_text(json.dumps(dict(requestId='old' if stale else request['requestId'],ok=True,closed=closed,output='DP-1')))
            close_targets(p,'DP-1',lambda k,v:self.rows.append(k),lambda:now[0],sleep)

    def test_current_matching_ack_required(self):
        self.exercise()
        self.assertEqual(self.rows,['target-close-request','target-close-ack'])

    def test_missing_or_stale_ack_times_out(self):
        for params in (dict(acknowledge=False),dict(stale=True)):
            with self.subTest(params=params),self.assertRaisesRegex(RuntimeError,'timed out'):
                self.exercise(**params)

    def test_wrong_close_count_is_not_accepted(self):
        with self.assertRaisesRegex(RuntimeError,'refused'):
            self.exercise(closed=1)


import unittest
from tools.desktop_topology_round import observe


class TopologyTests(unittest.TestCase):
    def exercise(self,unsafe=False,premature=False,ready=True):
        now=[0]
        self.rows=[]
        def status():
            t=now[0]
            names=['DP-1','HDMI-A-1']
            if 1<=t<(4 if premature else 7):names.remove('DP-1')
            if 8<=t<14:names.remove('HDMI-A-1')
            return dict(paused=not(unsafe and t>=2),transactionActive=False,movementReady=False,
                        restoreAvailable=False,resumeReady=ready,displays=[dict(name=n,id=n) for n in names])
        def sleep(seconds):now[0]+=seconds
        observe(status,lambda k,v:self.rows.append((k,v)),lambda:now[0],sleep)

    def test_two_disconnects_and_recoveries_stay_paused(self):
        self.exercise()
        self.assertEqual([v['output'] for k,v in self.rows if k=='output-disconnected'],['DP-1','HDMI-A-1'])
        self.assertEqual([v['output'] for k,v in self.rows if k=='output-recovered'],['DP-1','HDMI-A-1'])
        self.assertEqual(self.rows[-1][0],'round-result')

    def test_unpaused_state_fails_immediately(self):
        with self.assertRaisesRegex(RuntimeError,'did not stay paused'):
            self.exercise(unsafe=True)

    def test_short_disconnect_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'five-second dwell'):
            self.exercise(premature=True)

    def test_reconnect_without_ready_facts_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'not ready'):
            self.exercise(ready=False)


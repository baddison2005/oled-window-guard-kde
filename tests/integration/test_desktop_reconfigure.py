import unittest
from tools.desktop_reconfigure_warning import reconfigure_during_warning, reload_configuration, require_fixed_installation
from tools.desktop_reconfigure_batch import verify_round


class ReconfigureTests(unittest.TestCase):
    def test_unverified_installation_never_starts_child_round(self):
        import json
        from pathlib import Path
        import tempfile
        from unittest.mock import patch
        from tools import desktop_reconfigure_batch as batch
        with tempfile.TemporaryDirectory() as folder:
            receipt=Path(folder)/'receipt.jsonl'
            with patch('sys.argv',['batch','--receipt',str(receipt)]), \
                 patch.object(batch.driver,'main') as child, \
                 patch.object(batch,'require_fixed_installation',side_effect=RuntimeError('unverified installation')), \
                 patch.object(batch,'call_host',return_value={'ok':True}) as intent:
                batch.main()
                child.assert_not_called()
                intent.assert_called_once_with('Pause')
            rows=[json.loads(line) for line in receipt.read_text().splitlines()]
            self.assertEqual([r['kind'] for r in rows],['error','final-pause'])

    def test_real_desktop_reload_requires_verified_installation(self):
        from unittest.mock import patch
        with patch('tools.desktop_reconfigure_warning.call') as call, \
             patch('tools.desktop_reconfigure_warning.require_fixed_installation',side_effect=RuntimeError('unverified installation')):
            with self.assertRaisesRegex(RuntimeError,'unverified installation'):
                reload_configuration()
            call.assert_not_called()

    def test_installed_teardown_components_must_match_verified_source(self):
        from pathlib import Path
        import tempfile
        import shutil
        from unittest.mock import patch
        source=Path(__file__).resolve().parents[2]/'packages/kwin-script/contents/ui'
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)
            for name in ('main.qml','GuardedWriter.qml','OrderedGuardedWriter.qml'):
                shutil.copy2(source/name,target/name)
            with patch('tools.desktop_reconfigure_warning.installed_adapter',return_value=target/'main.qml'):
                require_fixed_installation()
                for name in ('main.qml','GuardedWriter.qml','OrderedGuardedWriter.qml'):
                    (target/name).write_text('old component')
                    with self.assertRaisesRegex(RuntimeError,'verified teardown fix'):
                        require_fixed_installation()
                    shutil.copy2(source/name,target/name)

    def exercise(self,late=False,restart=False,unsafe=False,unload=True):
        now=[0.0]; changed=[False]; self.events=[]
        displays=[{'id':'lg'},{'id':'hdmi'}]
        def probe():
            return dict(owner='new' if restart and changed[0] else 'original',
                        loaded=not (unload and changed[0] and now[0]<1))
        def reload():
            changed[0]=True; self.events.append(('reload',now[0]))
        def status():
            ready=now[0]>=1
            return dict(paused=ready and not unsafe,movementReady=ready and unsafe,
                        transactionActive=not ready,skipAvailable=not ready,
                        restoreAvailable=False,lastResult='none',displays=displays,
                        nativeObservation={'available':ready})
        def sleep(seconds):now[0]+=seconds
        return reconfigure_during_warning(dict(transactionActive=True,skipAvailable=True,
                 warningRemainingSeconds=4 if late else 7,displays=displays),status,
                 lambda k,v:self.events.append((k,now[0])),probe,reload,lambda:now[0],sleep)

    def test_same_controller_recovers_paused_and_watches_old_warning_expiry(self):
        fresh=self.exercise()
        self.assertTrue(fresh['paused'])
        self.assertGreaterEqual(self.events[-1][1],9)
        self.assertEqual(self.events[-1][0],'reconfigure-recovery-verified')

    def test_late_warning_does_not_reload(self):
        with self.assertRaisesRegex(RuntimeError,'active warning'):self.exercise(late=True)
        self.assertEqual(self.events,[])

    def test_controller_restart_is_not_observer_recovery(self):
        with self.assertRaisesRegex(RuntimeError,'Controller restarted'):self.exercise(restart=True)

    def test_automatic_rearming_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'not verified'):self.exercise(unsafe=True)

    def test_no_observer_unload_cannot_pass(self):
        with self.assertRaisesRegex(RuntimeError,'not verified'):self.exercise(unload=False)

    def test_batch_requires_recovery_and_pause_evidence(self):
        rows=[dict(kind='reconfigure-recovery-verified',value=dict(observerUnloaded=True,
                    sameController=True,oldWarningExpired=True)),dict(kind='final-pause',value={'ok':True})]
        verify_round(rows)
        for bad in (rows[:-1],rows[1:],rows+[dict(kind='error',value='failed')]):
            with self.assertRaises(RuntimeError):verify_round(bad)


if __name__=='__main__':unittest.main()

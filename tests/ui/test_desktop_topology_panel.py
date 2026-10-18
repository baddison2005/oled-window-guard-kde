import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from tools.desktop_acceptance_control import Control


class TopologyPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def test_selected_output_reaches_runner_and_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            for selected,other in (('DP-1','HDMI-A-1'),('HDMI-A-1','DP-1')):
                panel=Control(Path(folder),output=selected,test='topology-warning-both')
                self.assertIn('First target: '+selected,panel.label.text())
                self.assertIn('Put this panel on '+other,panel.label.text())
                with patch.object(panel.process,'start') as start:
                    panel.begin()
                    start.assert_called_once()
                args=panel.process.arguments()
                self.assertEqual(args[args.index('--output')+1],selected)
                self.assertIn('desktop_topology_warning_batch.py',args[0])
                panel.timer.stop()
                panel.deleteLater()
                self.app.processEvents()

    def test_resize_refusal_uses_dedicated_runner_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='resize-refusal-both')
            self.assertIn('No warning or movement expected',panel.label.text())
            with patch.object(panel.process,'start'):
                panel.begin()
            self.assertIn('desktop_resize_refusal.py',panel.process.arguments()[0])
            from PySide6.QtCore import QProcess
            with patch.object(panel.process,'state',return_value=QProcess.ProcessState.Running), \
                 patch.object(panel.process,'terminate') as terminate, \
                 patch.object(panel.process,'kill') as kill,patch.object(panel,'pause'):
                panel.abort();terminate.assert_called_once();kill.assert_not_called()
            panel.timer.stop();panel.deleteLater();self.app.processEvents()

    def test_gtk_panel_selects_new_runner_and_move_restore_instructions(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='gtk-both')
            self.assertIn('Two GTK windows per monitor',panel.label.text())
            self.assertIn('GTK',panel.windowTitle())
            with patch.object(panel.process,'start'):
                panel.begin()
            self.assertIn('desktop_gtk_batch.py',panel.process.arguments()[0])
            panel.timer.stop();panel.deleteLater();self.app.processEvents()

    def test_gtk4_panel_selects_version_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='gtk4-both')
            with patch.object(panel.process,'start'):panel.begin()
            self.assertEqual(panel.process.arguments()[-2:],['--gtk-version','4'])
            from PySide6.QtCore import QProcess
            with patch.object(panel.process,'state',return_value=QProcess.ProcessState.Running),patch.object(panel.process,'terminate') as terminate,patch.object(panel.process,'kill') as kill,patch.object(panel,'pause'):
                panel.abort();terminate.assert_called_once();kill.assert_not_called()
            panel.timer.stop();panel.deleteLater();self.app.processEvents()

    def test_shrink_panel_selects_dedicated_runner_and_graceful_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='shrink-both')
            self.assertIn('shrinks two pixels',panel.label.text())
            with patch.object(panel.process,'start'):panel.begin()
            self.assertIn('desktop_shrink_batch.py',panel.process.arguments()[0])
            from PySide6.QtCore import QProcess
            with patch.object(panel.process,'state',return_value=QProcess.ProcessState.Running),patch.object(panel.process,'terminate') as terminate,patch.object(panel.process,'kill') as kill,patch.object(panel,'pause'):
                panel.abort();terminate.assert_called_once();kill.assert_not_called()
            panel.timer.stop();panel.deleteLater();self.app.processEvents()

    def test_stability_panel_selects_continuous_runner_and_live_counts(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='stability')
            self.assertIn('285-second cutoff',panel.label.text())
            with patch.object(panel.process,'start'):panel.begin()
            self.assertIn('desktop_stability.py',panel.process.arguments()[0])
            panel.timer.stop();panel.deleteLater();self.app.processEvents()

    def test_paused_only_panel_never_selects_full_stability_run(self):
        with tempfile.TemporaryDirectory() as folder:
            panel=Control(Path(folder),test='stability-paused')
            self.assertIn('No warning or movement',panel.label.text())
            with patch.object(panel.process,'start'):panel.begin()
            self.assertIn('--paused-only',panel.process.arguments())
            panel.timer.stop();panel.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()

import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from oled_window_guard.dashboard import ControllerIntentClient, Dashboard, linux_application_ids
from oled_window_guard.autostart import LoginStartup


class LayoutStub:
    def read_snapshot(self):
        return {"status": "missing", "diagnostics": ["not configured"], "groups": [], "layouts": []}


class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.calls = []
        self.client = ControllerIntentClient(lambda action, payload: self.calls.append((action, payload)))
        self.window = Dashboard(self.client, settings_path=Path(self.temp.name) / "settings.json",
                                layout_provider=LayoutStub(), app_ids=[("org.example.Editor", "Editor")],
                                login_startup=LoginStartup(self.temp.name))

    def finish_reads(self):
        deadline = time.monotonic() + 3
        while self.window._reads and time.monotonic() < deadline:
            self.app.processEvents(); self.window._finish_reads(); time.sleep(0.005)
        self.assertFalse(self.window._reads)

    def tearDown(self):
        self.window._read_executor.shutdown(wait=True, cancel_futures=True)
        self.window.deleteLater()
        self.temp.cleanup()
        self.app.processEvents()

    def test_tabs_and_unknown_capabilities_fail_closed(self):
        self.assertEqual([self.window.tabs.tabText(i) for i in range(self.window.tabs.count())],
                         ["Overview", "Movement", "Safety & alerts", "Dimming", "Layout groups", "About"])
        self.assertFalse(self.window.pause_resume_button.isEnabled())
        self.assertFalse(self.window.move_now_button.isEnabled())
        self.assertFalse(self.window.restore_brightness_button.isEnabled())
        self.window._send("Resume")
        self.window._send("MoveNow")
        self.assertEqual(self.calls, [])

    def test_independent_header_controls_and_startup_options(self):
        self.assertIn('startMinimized', self.window.controls)
        self.assertIn('resumeAfterUnlock', self.window.controls)
        self.window.update_status(dict(readiness='ready', paused=False, movementPaused=False,
            brightnessPaused=True, movementResumeReady=True, brightnessResumeReady=True, canPause=True))
        self.assertEqual(self.window.movement_guard_button.text(), 'Pause movement guarding')
        self.assertEqual(self.window.brightness_guard_button.text(), 'Start brightness guarding')
        self.window.movement_guard_button.click()
        self.window.brightness_guard_button.click()
        self.assertEqual([c[0] for c in self.calls], ['PauseMovement', 'ResumeBrightness'])
        self.window.update_status(dict(readiness='unsupported', paused=False, movementPaused=True,
            brightnessPaused=False, movementResumeReady=False, brightnessResumeReady=False, canPause=True))
        self.assertFalse(self.window.movement_guard_button.isEnabled())
        self.assertTrue(self.window.brightness_guard_button.isEnabled())
        self.window.brightness_guard_button.click()
        self.assertEqual(self.calls[-1][0], 'PauseBrightness')
        self.assertTrue(all(metric.parent().alignment() & Qt.AlignmentFlag.AlignHCenter
                            for metric in self.window.overview_metrics))

    def test_guard_lights_distinguish_independent_active_paused_and_waiting(self):
        cases = [
            ({"readiness": "unknown", "paused": True}, "unavailable", "unavailable"),
            ({"readiness": "ready", "movementPaused": False, "brightnessPaused": True}, "active", "paused"),
            ({"readiness": "ready", "movementPaused": True, "brightnessPaused": False}, "paused", "active"),
            ({"readiness": "ready", "movementPaused": True, "brightnessPaused": True,
              "movementResumePending": True}, "waiting", "paused"),
            ({"readiness": "ready", "movementPaused": False, "brightnessPaused": False}, "active", "active"),
        ]
        for status, movement, brightness in cases:
            with self.subTest(status=status):
                self.window.update_status(status)
                for kind, expected in (("movement", movement), ("brightness", brightness)):
                    light, label = self.window.guard_indicators[kind]
                    self.assertEqual(label.text(), f"{kind.title()}: {expected}")
                    self.assertEqual(light.accessibleName(), label.text())
                    self.assertIn("#39a869" if expected == "active" else
                                  "#8a8f98" if expected == "paused" else "#d99a23", light.styleSheet())

    def test_ready_actions_are_intents_and_restore_brightness_needs_capability(self):
        self.window.settings["movementDisplays"] = ["DP-1"]
        self.window.set_displays([{"id":"DP-1","name":"Display 1"}])
        self.window.update_status({"readiness": "ready", "paused": True, "resumeReady": True,
                                   "movementReady": True, "dimmingReady": True,
                                   "capabilities": {"restoreBrightness": True, "shortcutRegistered": False},
                                   "shortcutConflict": "already assigned", "suspendedReasons": []})
        self.window._send("Resume")
        self.window.update_status({**self.window.status, "paused": False})
        self.window._send("MoveNow")
        self.window._send("RestoreBrightness")
        self.assertEqual([name for name, _ in self.calls], ["Resume", "MoveNow", "RestoreBrightness"])
        self.window.update_status({"readiness": "unknown", "paused": True, "resumeReady": False,
                                   "movementReady": True, "dimmingReady": True,
                                   "capabilities": {}, "suspendedReasons": ["missing input observer"]})
        self.window._send("Resume"); self.window._send("RestoreBrightness")
        self.assertEqual(len(self.calls), 3)
        self.window._send("MoveNow")
        self.assertEqual(len(self.calls), 3)

    def test_brightness_button_remains_usable_during_held_input_report(self):
        self.window.update_status(dict(readiness="unsupported",paused=True,resumeReady=False,
            movementReady=False,dimmingReady=False,capabilities={"restoreBrightness":True},
            suspendedReasons=["input-state-unknown-or-held"]))
        self.assertFalse(self.window.move_now_button.isEnabled())
        self.assertFalse(self.window.pause_resume_button.isEnabled())
        self.assertTrue(self.window.restore_brightness_button.isEnabled())
        self.window.restore_brightness_button.click()
        self.assertEqual([name for name,payload in self.calls],["RestoreBrightness"])
        self.window.update_status(dict(self.window.status,capabilities={"restoreBrightness":False}))
        self.assertFalse(self.window.restore_brightness_button.isEnabled())

    def test_read_only_preview_uses_selected_display_without_enabling_movement(self):
        self.window.settings["movementDisplays"]=["DP-1"]
        self.window.set_displays([{"id":"DP-1","name":"Display 1"}])
        status={"readiness":"unsupported","paused":True,"planningReady":True,"movementReady":False,
                "planningResult":{"status":"none"}}
        self.window.update_status(status)
        self.assertTrue(self.window.preview_button.isEnabled())
        self.assertFalse(self.window.move_now_button.isEnabled())
        self.window._send("PlanPreview")
        self.assertEqual(self.calls,[("PlanPreview",{"outputId":"DP-1"})])
        self.window.update_status({**status,"planningResult":{"status":"planning"}})
        self.window._send("PlanPreview")
        self.assertEqual(len(self.calls),1)
        self.window.update_status({**status,"planningResult":{"status":"ready","moves":2}})
        self.assertIn("2 proposed moves",self.window.preview_label.text())
        self.assertIn("No windows were moved",self.window.preview_label.text())
        self.window.update_status({**status,"planningReady":False})
        self.assertFalse(self.window.preview_button.isEnabled())

    def test_overview_preview_actions_follow_display_and_restore_scope(self):
        self.window.settings["movementDisplays"] = ["DP-1", "HDMI-1"]
        self.window.set_displays([{"id":"DP-1","name":"LG"},{"id":"HDMI-1","name":"HDMI"}])
        status = dict(readiness="ready",paused=False,resumeReady=True,movementReady=True,
                      planningReady=True,transactionActive=False,restoreAvailable=True,restoreOutputId="DP-1")
        rect = dict(x=0,y=0,width=1000,height=500)
        drawing = dict(outputId="DP-1",geometry=rect,windows=[dict(x=10,y=20,width=100,height=80)],totalWindows=1,
                       destinations=[dict(**{"from":dict(x=10,y=20,width=100,height=80),"to":dict(x=300,y=20,width=100,height=80)})])
        self.window.update_status(dict(status,planningResult=dict(status="ready",moves=1,preview=drawing)))
        self.assertIsNotNone(self.window.movement_preview.data)
        self.assertTrue(self.window.restore_button.isEnabled())
        self.window._send("Restore")
        self.assertEqual(self.calls[-1],("Restore",{"outputId":"DP-1"}))
        self.window.preview_display.setCurrentIndex(1)
        self.assertIsNone(self.window.movement_preview.data)
        self.assertFalse(self.window.restore_button.isEnabled())
        self.window._send("Restore")
        self.window._send("MoveNow")
        self.assertEqual(self.calls[-1],("MoveNow",{"outputId":"HDMI-1"}))
        self.window.update_status(dict(status,transactionActive=True))
        self.assertFalse(self.window.preview_button.isEnabled())
        self.assertFalse(self.window.preview_display.isEnabled())
        self.window.update_status(dict(status,planningResult=dict(status="discarded",moves=0)))
        self.assertIsNone(self.window.movement_preview.data)

    def test_preview_snapshot_survives_invalidation_and_can_be_refreshed(self):
        self.window.settings["movementDisplays"]=["DP-1"]
        self.window.set_displays([dict(id="DP-1",name="LG")])
        rect=dict(x=0,y=0,width=1000,height=600)
        drawing=dict(outputId="DP-1",geometry=rect,windows=[rect],totalWindows=1,destinations=[])
        self.window.update_status(dict(planningReady=True,paused=True,planningResult=dict(status="no-plan",moves=0,preview=drawing)))
        self.window.update_status(dict(planningReady=True,paused=True,planningResult=dict(status="discarded",reason="desktop-changed",moves=0)))
        self.assertIsNotNone(self.window.movement_preview.data)
        self.assertIn("Last preview snapshot",self.window.preview_label.text())
        self.window._send("PlanPreview")
        self.assertEqual(self.calls[-1],("PlanPreview",{"outputId":"DP-1"}))
        self.window.set_displays([])
        self.assertFalse(self.window._preview_drawings)
        self.assertIsNone(self.window.movement_preview.data)

    def test_desktop_click_policy_persists_and_can_be_overridden_per_display(self):
        self.window.controls["desktopClickBehavior"].setCurrentIndex(1)
        self.assertEqual(self.window.settings["desktopClickBehavior"],"restart-timers")
        self.window.set_displays([dict(id="DP-1",name="LG")])
        self.window.dimming_override_display.setCurrentIndex(1)
        self.window.dimming_override_controls["desktopClickBehavior"].setCurrentIndex(0)
        self.assertEqual(self.window.settings["dimmingOverrides"]["DP-1"]["desktopClickBehavior"],"stay-bright")

    def test_mode_cards_persist_same_settings_as_enum_control(self):
        selector = self.window.controls["mode"]
        selector.group.button(selector.findData("swap")).click()
        self.assertEqual(self.window.settings["mode"], "swap")
        self.window._set_setting("mode", "group")
        self.assertEqual(selector.currentData(), "group")

    def test_launch_at_login_is_independent_of_movement_settings(self):
        before=dict(self.window.settings)
        self.assertFalse(self.window.launch_at_login.isChecked())
        self.window.launch_at_login.click()
        self.assertTrue(self.window.login_startup.read().enabled)
        self.assertEqual(self.window.settings,before)
        self.assertEqual(self.calls,[])
        self.window.launch_at_login.click()
        self.assertFalse(self.window.login_startup.path.exists())
        self.assertIn("paused",self.window.login_status.text())

    def test_launch_at_login_error_reverts_checkbox_and_explains_failure(self):
        with patch.object(self.window.login_startup,'set_enabled',side_effect=OSError('write refused')):
            self.window.launch_at_login.click()
        self.assertFalse(self.window.launch_at_login.isChecked())
        self.assertIn('write refused',self.window.login_status.text())
        self.assertEqual(self.calls,[])

    def test_movement_options_follow_style_and_preserve_other_values(self):
        self.window.controls["mode"].setCurrentIndex(1)
        self.assertTrue(self.window.range_card.isHidden())
        self.assertFalse(self.window.size_card.isHidden())
        self.assertTrue(self.window.controls["shiftHorizontalOrder"].isHidden())
        self.assertFalse(self.window.controls["keepSimilarWindowsTogether"].isHidden())
        self.window.controls["mode"].setCurrentIndex(2)
        self.assertFalse(self.window.group_card.isHidden())
        self.window.controls["mode"].setCurrentIndex(0)
        self.assertTrue(self.window.group_card.isHidden())
        self.assertTrue(self.window.size_card.isHidden())

    def test_settings_revision_intent_and_app_exclusions(self):
        item = self.window.excluded_apps.item(0)
        item.setCheckState(Qt.CheckState.Checked)
        self.app.processEvents()
        self.assertIn("SettingsChanged", [name for name, _ in self.calls])
        payload = next(data for name, data in self.calls if name == "SettingsChanged")
        self.assertEqual(set(payload), {"revision"})
        self.assertTrue(payload["revision"])

    def test_display_overrides_are_keyed_by_stable_id_and_new_outputs_start_unselected(self):
        self.window.set_displays([{"id": "edid:panel-a", "name": "Panel A"}])
        self.assertEqual(self.window._checked_ids(self.window.movement_display_list), [])
        self.window.movement_override_display.setCurrentIndex(1)
        self.window.override_interval.setValue(12)
        self.assertEqual(self.window.settings["movementOverrides"]["edid:panel-a"]["intervalMinutes"], 12)
        self.window.dimming_override_display.setCurrentIndex(1)
        self.window.dim_override_display_percent.setValue(15)
        self.assertEqual(self.window.settings["dimmingOverrides"]["edid:panel-a"]["displayDimPercent"], 15)

    def test_close_hides_and_does_not_quit_app(self):
        self.window.show(); self.window.close(); self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.app.quitOnLastWindowClosed())
        self.assertNotIn("Quit", [name for name, _ in self.calls])

    def test_imported_group_selection_uses_stable_id_and_keeps_missing_selection(self):
        self.finish_reads()
        from types import SimpleNamespace
        self.window.layout_provider = SimpleNamespace(read_snapshot=lambda: {
            "status": "available", "executionReady": True, "diagnostics": [], "layouts": [],
            "groups": [{"id": "custom-pair", "name": "Custom Pair", "layoutIds": ["left", "right"]}]})
        self.window.refresh_layouts(); self.finish_reads()
        self.window.group_combo.setCurrentIndex(self.window.group_combo.findData("kde:custom-pair"))
        self.assertEqual(self.window.settings["groupId"], "kde:custom-pair")
        self.assertIn("SettingsChanged", [name for name, _ in self.calls])
        self.window.layout_provider = LayoutStub()
        self.window.refresh_layouts(); self.finish_reads()
        self.assertEqual(self.window.group_combo.currentData(), "kde:custom-pair")
        self.assertTrue(self.window.group_combo.currentText().startswith("Unavailable:"))

    def test_live_theme_change_updates_text_contrast(self):
        from PySide6.QtGui import QPalette, QColor
        original = self.app.palette()
        palette = QPalette(original)
        palette.setColor(QPalette.ColorRole.WindowText, QColor("#ececec"))
        palette.setColor(QPalette.ColorRole.Window, QColor("#202428"))
        try:
            self.app.setPalette(palette); self.window._finish_reads()
            self.assertEqual(self.window.readiness_label.palette().color(QPalette.ColorRole.WindowText),QColor("#ececec"))
        finally:
            self.app.setPalette(original); self.window._finish_reads()

    def test_fractional_fade_and_fractional_interval_persist(self):
        from oled_window_guard import preferences
        self.window.controls["windowDimFade"].setValue(0.5)
        self.window.controls["intervalMinutes"].setValue(1.5)
        values = preferences.load_settings(self.window.settings_path).values
        self.assertEqual(values["windowDimFade"], 0.5)
        self.assertEqual(values["intervalMinutes"], 1.5)
        self.window.controls["windowDimFade"].setValue(0.7)
        self.assertEqual(self.window.controls["windowDimFade"].value(), 0.5)
        self.assertIsNone(self.window.settings_error)

    def test_override_reset_inheritance_and_all_fields(self):
        from oled_window_guard import preferences
        self.window.set_displays([{"id":"display-a", "name":"A"}])
        for kind in ("movement", "dimming"):
            controls = getattr(self.window, kind + "_override_controls")
            self.assertEqual(set(controls), set(preferences.schema_metadata()["$defs"][kind + "Override"]["properties"]))
            getattr(self.window, kind + "_override_display").setCurrentIndex(1)
        self.window.dimming_override_controls["windowDimFade"].setValue(1.5)
        self.window.controls["displayDimDelay"].setValue(12.5)
        self.assertEqual(self.window.dimming_override_controls["displayDimDelay"].value(),12.5)
        self.assertEqual(self.window.settings["dimmingOverrides"]["display-a"], {"windowDimFade":1.5})
        self.window._reset_override("dimming")
        self.assertEqual(self.window.settings["dimmingOverrides"], {})
        self.assertEqual(self.window.dimming_override_controls["windowDimFade"].value(),self.window.settings["windowDimFade"])

    def test_disconnected_selection_survives_edit(self):
        self.window.settings["movementDisplays"] = ["unplugged"]
        self.window.set_displays([{"id":"connected","name":"Connected"}])
        self.window.movement_display_list.item(0).setCheckState(Qt.CheckState.Checked)
        self.assertEqual(set(self.window.settings["movementDisplays"]), {"connected","unplugged"})

    def test_missing_app_exclusion_is_visible_and_preserved(self):
        from oled_window_guard import preferences
        path = Path(self.temp.name) / "other.json"
        preferences.save_settings(path,dict(preferences.defaults(),excludedApps=["missing.app"]))
        other = Dashboard(self.client,settings_path=path,layout_provider=LayoutStub(),app_ids=[("installed.app","Installed")])
        try:
            self.assertEqual(other.excluded_apps.count(),2)
            for i in range(2):
                item = other.excluded_apps.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == "installed.app": item.setCheckState(Qt.CheckState.Checked)
            self.assertEqual(set(other.settings["excludedApps"]), {"missing.app","installed.app"})
        finally:
            other._read_executor.shutdown(wait=True); other.deleteLater()

    def test_local_edit_preserves_external_settings_changes(self):
        from oled_window_guard import preferences
        preferences.save_settings(self.window.settings_path,dict(preferences.defaults(),warningSeconds=17))
        self.window.controls["windowDimFade"].setValue(1.5)
        self.assertEqual(preferences.load_settings(self.window.settings_path).values["warningSeconds"],17)
        self.assertEqual(self.window.controls["warningSeconds"].value(),17)

    def test_warning_alerts_once_per_warning_and_opt_in(self):
        warning = {"transactionActive":True,"warningRemainingSeconds":9,"capabilities":{}}
        with patch.object(self.window.warning_audio,"play") as beep, patch.object(self.window.tray,"isVisible",return_value=True), patch.object(self.window.tray,"showMessage") as notification:
            self.window.update_status(warning)
            beep.assert_not_called(); notification.assert_not_called()
            self.window.update_status({"transactionActive":False})
            self.window.settings.update(soundEnabled=True,systemNotifications=True)
            self.window.update_status(warning)
            self.window.update_status(dict(warning,warningRemainingSeconds=8))
            beep.assert_called_once(); notification.assert_not_called()
            self.window.update_status({"transactionActive":False})
            self.window.update_status(warning)
            self.assertEqual(beep.call_count,2)

    def test_sound_button_does_not_change_settings_or_request_movement(self):
        original = dict(self.window.settings)
        calls = list(self.calls)
        with patch.object(self.window.warning_audio, "play") as play:
            self.window.test_sound.click()
            play.assert_called_once()
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.window.settings, original)

    def test_notification_waits_for_paused_idle_without_inverse(self):
        self.window.settings.update(systemNotifications=True)
        with patch.object(self.window.tray,"isVisible",return_value=True), patch.object(self.window.tray,"showMessage") as notification:
            self.window.update_status(dict(transactionActive=True,warningRemainingSeconds=10,paused=False))
            for state in [dict(transactionActive=False,paused=False,restoreAvailable=True),
                          dict(transactionActive=True,paused=False,warningRemainingSeconds=10),
                          dict(transactionActive=False,paused=False,restoreAvailable=False),
                          dict(transactionActive=False,paused=True,restoreAvailable=True),
                          dict(paused=True), {}]:
                self.window.update_status(state)
                notification.assert_not_called()
            paused = dict(transactionActive=False,paused=True,restoreAvailable=False)
            self.window.update_status(paused)
            self.window.update_status(paused)
            notification.assert_called_once()

    def test_notification_opt_out_discards_pending_summary(self):
        self.window.settings.update(systemNotifications=True)
        with patch.object(self.window.tray,"showMessage") as notification:
            self.window.update_status(dict(transactionActive=True,warningRemainingSeconds=10))
            self.window.settings["systemNotifications"] = False
            self.window.update_status(dict(transactionActive=False,paused=True,restoreAvailable=False))
            self.assertFalse(self.window._notification_pending)
            notification.assert_not_called()

    def test_layout_refresh_keeps_event_loop_responsive(self):
        import threading
        from types import SimpleNamespace
        from PySide6.QtCore import QTimer
        self.finish_reads()
        release = threading.Event()
        self.window.layout_provider = SimpleNamespace(read_snapshot=lambda: (release.wait(2), LayoutStub().read_snapshot())[1])
        ticks = []
        timer = QTimer(); timer.setInterval(5); timer.timeout.connect(lambda: ticks.append(True)); timer.start()
        try:
            started = time.monotonic(); self.window.refresh_layouts()
            self.assertLess(time.monotonic()-started,0.1)
            until = time.monotonic()+0.1
            while time.monotonic()<until: self.app.processEvents(); time.sleep(0.005)
            self.assertGreater(len(ticks),3)
            self.assertIn("layouts", self.window._reads)
        finally:
            release.set(); timer.stop(); self.finish_reads()

    def test_rejected_intent_revokes_stale_ui_capabilities(self):
        self.window.update_status({"readiness":"ready","paused":False,"movementReady":True})
        self.window.intent_client = ControllerIntentClient(lambda a,p: {"ok":False,"reason":"unavailable","status":{"readiness":"unsupported","paused":True,"movementReady":False}})
        self.window._send("MoveNow")
        self.assertFalse(self.window.move_now_button.isEnabled())

    def test_app_picker_reads_xdg_data_dirs_and_nested_ids(self):
        root=Path(self.temp.name)
        nested=root / "shared" / "applications" / "vendor"
        nested.mkdir(parents=True)
        (nested / "editor.desktop").write_text("[Desktop Entry]\nName=Nested Editor\n",encoding="utf-8")
        with patch.dict(os.environ,{"XDG_DATA_HOME":str(root / "user"),"XDG_DATA_DIRS":str(root / "shared")}):
            self.assertIn(("vendor-editor","Nested Editor"),linux_application_ids())

    def test_warning_countdown_and_skip_use_same_intent(self):
        self.window.update_status({"readiness":"ready","paused":False,"canPause":True,
                                   "transactionActive":True,"skipAvailable":True,
                                   "warningRemainingSeconds":7,"capabilities":{}})
        self.assertIn("7 seconds",self.window.warning_label.text())
        self.window._send("Skip")
        self.assertEqual(self.calls[-1][0],"Skip")

    def test_next_move_countdown_and_waiting_states(self):
        self.window.set_displays([dict(id='DP-1',name='LG',connector='DP-1')])
        status=dict(paused=False,transactionActive=False,nextMoveCheck=dict(outputId='DP-1',remainingSeconds=61.2))
        self.window.update_status(status)
        self.assertIn('1:02',self.window.next_move_label.text())
        self.assertIn('DP-1',self.window.next_move_label.text())
        self.window.update_status(dict(status,nextMoveCheck=dict(outputId='DP-1',remainingSeconds=0)))
        self.assertIn('safe conditions',self.window.next_move_label.text())
        self.window.update_status(dict(status,paused=True))
        self.assertIn('paused',self.window.next_move_label.text())
        self.window.update_status(dict(status,transactionActive=True))
        self.assertIn('in progress',self.window.next_move_label.text())

    def test_live_positions_are_visible_without_planning_or_write_authority(self):
        self.window.set_displays([dict(id='DP-1',name='LG')])
        rect=dict(x=0,y=0,width=500,height=300)
        drawing=dict(outputId='DP-1',geometry=rect,windows=[rect],destinations=[],totalWindows=1)
        self.window.update_status(dict(paused=True,livePreview=drawing,planningResult=dict(status='none')))
        self.assertIsNotNone(self.window.movement_preview.data)
        self.assertIn('Current window positions',self.window.preview_label.text())
        self.assertFalse(self.window.move_now_button.isEnabled())

    def test_last_move_card_includes_confirmed_window_count(self):
        self.window.update_status(dict(lastResult='committed',committedWindows=2,plannedWindows=2))
        self.assertEqual('Committed: 2 windows moved',self.window.overview_metrics[2].text())
        self.window.update_status(dict(lastResult='committed',committedWindows=1))
        self.assertEqual('Committed: 1 window moved',self.window.overview_metrics[2].text())

    def test_uncertain_movement_distinguishes_visible_change_from_verified_commits(self):
        self.window.update_status(dict(lastResult='failed-uncertain: partial-or-external-geometry',committedWindows=0,unverifiedWindows=1))
        text=self.window.overview_metrics[2].text()
        self.assertIn('1 window changed; unverified',text)
        self.assertNotIn('0 windows',text)

    def test_restore_preview_overrides_forward_proposal_and_clears_on_cancel(self):
        self.window.settings['movementDisplays']=['DP-1']
        self.window.set_displays([dict(id='DP-1',name='LG')])
        a=dict(x=10,y=20,width=150,height=100);b=dict(a,x=350)
        forward=dict(outputId='DP-1',geometry=dict(x=0,y=0,width=1000,height=600),windows=[a],totalWindows=1,destinations=[{'from':a,'to':b}])
        restore=dict(forward,windows=[b],destinations=[{'from':b,'to':a}])
        live=dict(forward,windows=[b],destinations=[])
        base=dict(planningResult=dict(status='ready',moves=1,preview=forward),livePreview=live)
        self.window.update_status(dict(base,transactionActive=True,restorePreview=restore))
        self.assertEqual(self.window.movement_preview.data['destinations'],restore['destinations'])
        self.assertIn('Restore preview',self.window.preview_label.text())
        self.window.update_status(dict(base,transactionActive=False,restorePreview=None,planningResult=dict(status='discarded')))
        self.assertEqual(self.window.movement_preview.data['destinations'],[])
        self.assertNotIn('Restore preview',self.window.preview_label.text())

    def test_preview_uses_desktop_names_and_wm_class_aliases(self):
        self.window.application_ids=[('org.example.Editor','Editor'),('com.valvesoftware.Steam','Steam')]
        self.window.application_aliases={'com.valvesoftware.Steam':['Steam']}
        self.window.set_displays([dict(id='DP-1',name='LG')])
        a=dict(x=10,y=20,width=150,height=100)
        drawing=dict(outputId='DP-1',geometry=dict(x=0,y=0,width=1000,height=600),windows=[a],totalWindows=1,destinations=[],
                     windowInfo=[dict(appId='steam',eligibility='excluded',reason='excluded_app',moveNumber=0)])
        self.window.update_status(dict(livePreview=drawing))
        self.assertEqual(self.window.movement_preview.window_label(0)[:2],('Steam','Excluded'))


if __name__ == "__main__":
    unittest.main()

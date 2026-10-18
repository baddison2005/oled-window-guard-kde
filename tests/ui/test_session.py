import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import tempfile
import json
import time
import unittest
from pathlib import Path

from oled_window_guard import preferences
from oled_window_guard.dashboard import create_application
from oled_window_guard.session import SessionHost, revision


class SessionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = create_application([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.json"
        self.host = SessionHost(self.app, settings_path=self.path)

    def tearDown(self):
        self.host.layout_executor.shutdown(wait=True, cancel_futures=True)
        self.host.observer_timer.stop()
        self.host.dashboard.tray.hide()
        self.host.dashboard.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def test_startup_and_guarded_dock_intents(self):
        self.assertFalse(self.app.windowIcon().isNull())
        self.assertEqual(self.app.desktopFileName(), "oled-window-guard")
        self.assertTrue(self.host.status["paused"])
        self.assertFalse(self.host.status["resumeReady"])
        for action in ("Resume", "MoveNow", "Restore", "RestoreBrightness"):
            self.assertEqual(self.host.submit(action)["reason"], "capability-unavailable")
        self.assertTrue(self.host.submit("Open")["ok"])
        self.assertTrue(self.host.dashboard.isVisible())
        self.host.dashboard.close()
        self.assertFalse(self.host.dashboard.isVisible())
        self.assertTrue(self.host.submit("Pause")["ok"])
        self.assertTrue(preferences.load_settings(self.path).values["paused"])

    def test_minimized_startup_stays_paused_and_explicit_open_restores(self):
        values = dict(preferences.defaults(), startMinimized=True, resumeAfterUnlock=True, paused=False)
        preferences.save_settings(self.path, values)
        before = self.path.read_bytes()
        self.host.show_startup()
        self.assertTrue(self.host.dashboard.isMinimized())
        self.assertTrue(self.host.status['movementPaused'])
        self.assertTrue(self.host.status['brightnessPaused'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(self.host.submit('Open')['ok'])
        self.assertFalse(self.host.dashboard.isMinimized())
        self.assertTrue(self.host.dashboard.isVisible())

    def test_component_pause_is_available_without_readiness(self):
        for action in ('PauseMovement', 'PauseBrightness'):
            self.assertTrue(self.host.submit(action)['ok'])
            self.assertEqual(self.host.runtime_command['action'], action)
        self.assertTrue(preferences.load_settings(self.path).values['paused'])

    def test_brightness_commands_preserve_active_movement_preview_and_revision(self):
        self.select_output()
        values = dict(preferences.load_settings(self.path).values, paused=False,
                      dimWindows=True, dimmingDisplays=['DP-1'])
        preferences.save_settings(self.path, values)
        self.product_exchange(2, armed=True, movementArmed=True, brightnessArmed=False,
                              movementReady=True, brightnessResumeReady=True, restoreAvailable=True)
        before = self.path.read_bytes()
        self.host.preview_pending = 'preview-1'
        self.host.preview_started = time.monotonic()
        self.host.runtime_pending = 'move-1'
        self.host.runtime_started = time.monotonic()
        self.assertTrue(self.host.submit('ResumeBrightness')['ok'])
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.host.preview_pending, 'preview-1')
        self.assertTrue(self.host.status['restoreAvailable'])
        self.assertTrue(self.host.submit('PauseBrightness')['ok'])
        self.assertEqual(self.host.preview_pending, 'preview-1')
        self.assertEqual(self.path.read_bytes(), before)

    def test_scoped_movement_actions_do_not_change_runtime_settings_generation(self):
        self.select_output()
        preferences.save_settings(self.path, dict(preferences.load_settings(self.path).values,
            paused=False, dimWindows=True, dimmingDisplays=['DP-1']))
        self.product_exchange(2, armed=True, movementArmed=False, brightnessArmed=True,
                              movementResumeReady=True)
        before = self.host.control_generation
        self.assertTrue(self.host.submit('ResumeMovement')['ok'])
        self.assertEqual(self.host.control_generation, before)
        self.assertTrue(self.host.submit('PauseMovement')['ok'])
        self.assertEqual(self.host.control_generation, before)

    def test_component_status_and_malformed_flags(self):
        self.select_output()
        preferences.save_settings(self.path, dict(preferences.load_settings(self.path).values,
            paused=False, dimWindows=True, dimmingDisplays=['DP-1']))
        self.product_exchange(2, armed=True, movementArmed=False, brightnessArmed=True,
                              movementReady=False, dimmingReady=True, restoreBrightness=True)
        self.assertTrue(self.host.status['movementPaused'])
        self.assertFalse(self.host.status['brightnessPaused'])
        self.assertFalse(self.host.status['paused'])
        self.assertTrue(self.host.status['dimmingReady'])
        self.assertTrue(self.host.status['capabilities']['restoreBrightness'])
        self.product_exchange(3, armed=True, movementArmed=True, brightnessArmed=False,
                              movementReady=True, dimmingReady=True, restoreBrightness=True)
        self.assertFalse(self.host.status['movementPaused'])
        self.assertTrue(self.host.status['brightnessPaused'])
        self.assertFalse(self.host.status['dimmingReady'])
        self.assertFalse(self.host.status['capabilities']['restoreBrightness'])
        for sequence, key in enumerate(('movementArmed','brightnessArmed','movementResumeReady','brightnessResumeReady'), 4):
            self.assertFalse(self.product_exchange(sequence, **{key:1})['ok'])
            self.assertTrue(self.host.status['movementPaused'])
            self.assertTrue(self.host.status['brightnessPaused'])

    def test_monitor_port_rebinding_is_saved_before_runtime_readiness(self):
        old = json.dumps(['DP-1','LG','OLED','LG-serial'])
        new = json.dumps(['HDMI-A-1','LG','OLED','LG-serial'])
        values = dict(preferences.defaults(), movementDisplays=[old], dimmingDisplays=[old], dimWindows=True,
                      movementOverrides={old:{'mode':'swap'}}, dimmingOverrides={old:{'windowDimDelay':17}})
        preferences.save_settings(self.path,values)
        reply=self.exchange(outputs=[dict(id=new,connector='HDMI-A-1')])
        self.assertTrue(reply['ok'])
        saved=preferences.load_settings(self.path).values
        self.assertEqual(saved['movementDisplays'],[new]);self.assertEqual(saved['dimmingDisplays'],[new])
        self.assertEqual(saved['movementOverrides'],{new:{'mode':'swap'}})
        self.assertEqual(saved['dimmingOverrides'],{new:{'windowDimDelay':17}})
        self.assertEqual(reply['settings'],saved)
        self.assertEqual(self.host.dashboard.settings,saved)
        self.assertTrue(self.host.status['paused'])
        raw=self.path.read_bytes();self.exchange(2,outputs=[dict(id=new,connector='HDMI-A-1')])
        self.assertEqual(raw,self.path.read_bytes())

    def test_malformed_armed_display_lists_revoke_runtime(self):
        self.select_output()
        for sequence, ids in enumerate(([''], ['DP-1','DP-1'], [None], 'DP-1'), 2):
            self.assertFalse(self.product_exchange(sequence, movementActiveDisplays=ids)['ok'])
            self.assertTrue(self.host.status['paused'])

    def test_session_waiting_keeps_pause_controls_without_authorizing_writes(self):
        self.select_output()
        preferences.save_settings(self.path,dict(preferences.load_settings(self.path).values,paused=False,
            resumeAfterUnlock=True,dimWindows=True,dimmingDisplays=['DP-1']))
        self.product_exchange(2,ready=False,armed=False,movementArmed=False,brightnessArmed=False,
            movementResumePending=True,brightnessResumePending=True)
        self.assertFalse(self.host.status['paused'])
        self.assertFalse(self.host.status['movementPaused']);self.assertFalse(self.host.status['brightnessPaused'])
        self.assertTrue(self.host.status['movementResumePending'])
        self.assertFalse(self.host.status['movementReady']);self.assertFalse(self.host.status['dimmingReady'])
        self.assertFalse(self.host.status['capabilities']['restoreBrightness'])
        self.assertTrue(self.host.dashboard.movement_guard_button.isEnabled())
        self.assertEqual(self.host.dashboard.movement_guard_button.text(),'Pause movement guarding')
        self.assertIn('waiting for unlock or wake',self.host.dashboard.paused_label.text())
        self.assertTrue(self.host.submit('PauseMovement')['ok'])
        self.host.clear_runtime();self.assertFalse(self.host.status['movementResumePending'])
        for sequence,bad in enumerate((1,'true',[]),3):
            self.assertFalse(self.product_exchange(sequence,movementResumePending=bad)['ok'])

    def test_revision_exchange_reads_saved_settings(self):
        values=preferences.defaults()
        values["intervalMinutes"]=11
        preferences.save_settings(self.path,values)
        self.assertEqual(self.host.submit("SettingsChanged",{"revision":"stale"})["reason"],
                         "settings-revision-mismatch")
        result=self.host.submit("SettingsChanged",{"revision":revision(values)})
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"]["settingsRevision"],revision(values))

    def test_running_adapter_source_is_diagnostic_and_does_not_grant_readiness(self):
        source='file:///usr/share/kwin/scripts/oled-window-guard-adapter/contents/build_example/ui/main.qml'
        self.assertTrue(self.exchange(adapterSource=source)['ok'])
        self.assertEqual(self.host.status['adapterSource'],source)
        self.assertEqual(self.host.status['observerEpoch'],'observer-1')
        self.assertFalse(self.host.status['resumeReady'])
        for sequence,source in enumerate(['https://example.com/main.qml',{},'file:///'+('a'*4096)],2):
            self.assertEqual(self.exchange(sequence,adapterSource=source)['reason'],'invalid-adapter-source')

    def test_production_handoff_rejects_stale_code_and_previous_armed_state(self):
        self.select_output()
        source='file:///tmp/installed-owg/ui/main.qml'
        self.host.expected_adapter_source=source
        values=preferences.load_settings(self.path).values
        preferences.save_settings(self.path,dict(values,paused=False))
        native=dict(source='compositor-local',immediateCancellation=True,available=True)
        previous=self.runtime(armed=True,movementReady=True,controllerEpoch='previous-controller')
        self.assertTrue(self.exchange(2,adapterSource=source,native=native,runtime=previous)['ok'])
        self.assertTrue(self.host.status['paused'])
        self.assertFalse(self.host.status['resumeReady'])
        self.assertFalse(self.host.status['movementReady'])
        self.assertEqual(self.exchange(3,adapterSource='file:///tmp/stale/ui/main.qml',native=native,runtime=previous)['reason'],'installed-adapter-mismatch')
        current=dict(previous,armed=False,controllerEpoch=self.host.controller_epoch)
        self.assertTrue(self.exchange(4,adapterSource=source,native=native,runtime=current)['ok'])
        self.assertTrue(self.host.status['paused'])
        self.assertFalse(self.host.status['movementReady'])

    def exchange(self, sequence=1, epoch="observer-1", **changes):
        payload={"outputs":[{"id":"DP-1","connector":"DP-1"}],"available":True,"planning":"idle","report":None}
        payload.update(changes)
        return json.loads(self.host.ObserverExchange(epoch,sequence,json.dumps(payload)))

    def select_output(self):
        values=preferences.defaults()
        values["movementDisplays"]=["DP-1"]
        preferences.save_settings(self.path,values)
        self.assertTrue(self.exchange()["ok"])

    def test_observer_preview_is_explicit_one_shot_and_preserves_unavailable_writes(self):
        self.select_output()
        self.assertIsNone(self.exchange(2)["command"])
        response=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.assertTrue(response["ok"])
        self.assertEqual(self.host.submit("PlanPreview",{"outputId":"DP-1"})["reason"],"planning-busy")
        reply=self.exchange(3)
        self.assertEqual(reply["command"]["id"],response["requestId"])
        self.assertIsNone(self.exchange(4)["command"])
        self.exchange(5,report={"id":response["requestId"],"status":"ready","moves":2,"reason":None})
        self.assertEqual(self.host.status["planningResult"]["moves"],2)
        self.assertIsNone(self.host.preview_pending)
        self.assertTrue(self.host.status["paused"])
        for key in ("movementReady","dimmingReady","resumeReady"):
            self.assertFalse(self.host.status[key])

    def test_old_movement_report_cannot_complete_a_new_preview_request(self):
        self.select_output()
        request=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        old=dict(id="older-movement",purpose="movement",status="discarded",moves=0,reason="geometry")
        self.exchange(2,report=old)
        self.assertEqual(self.host.preview_pending,request["requestId"])
        self.assertEqual(self.host.status["planningResult"]["id"],request["requestId"])
        self.exchange(3,report=dict(id=request["requestId"],status="ready",moves=0,reason=None))
        self.assertIsNone(self.host.preview_pending)

    def test_preview_drawing_is_bounded_sanitized_and_discarded_with_plan(self):
        self.select_output()
        response=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.exchange(2)
        rect=dict(x=0,y=0,width=1000,height=600)
        drawing=dict(outputId="DP-1",geometry=rect,windows=[rect],totalWindows=1,
                     destinations=[{"from":rect,"to":rect}],windowTitle="must not leave validator")
        report=dict(id=response["requestId"],status="ready",moves=1,reason=None,preview=drawing)
        self.assertTrue(self.exchange(3,report=report)["ok"])
        self.assertNotIn("windowTitle",self.host.status["planningResult"]["preview"])
        self.exchange(4,report=dict(report,status="discarded",moves=0,preview=None))
        self.assertIsNone(self.host.status["planningResult"]["preview"])

    def test_malformed_preview_drawing_refuses_exchange(self):
        self.select_output()
        response=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.exchange(2)
        drawing=dict(outputId="DP-1",geometry=dict(x=0,y=0,width=float('nan'),height=600),windows=[],totalWindows=0,destinations=[])
        reply=self.exchange(3,report=dict(id=response["requestId"],status="no-plan",moves=0,reason=None,preview=drawing))
        self.assertFalse(reply["ok"])
        self.assertFalse(self.host.status["movementReady"])

    def test_restore_drawing_is_warning_only_and_clears_after_completion(self):
        self.select_output()
        rect=dict(x=10,y=10,width=100,height=100)
        drawing=dict(outputId='DP-1',geometry=dict(x=0,y=0,width=1000,height=600),windows=[rect],totalWindows=1,
                     destinations=[{'from':rect,'to':dict(rect,x=300)}])
        self.assertTrue(self.product_exchange(2,busy=True,skipAvailable=True,restorePreview=drawing)['ok'])
        self.assertEqual(self.host.status['restorePreview']['destinations'],drawing['destinations'])
        self.assertTrue(self.product_exchange(3)['ok'])
        self.assertIsNone(self.host.status['restorePreview'])
        self.assertFalse(self.product_exchange(4,busy=False,skipAvailable=False,restorePreview=drawing)['ok'])
        self.assertIsNone(self.host.status['restorePreview'])

    def test_preview_requires_present_selected_output(self):
        self.assertEqual(self.host.submit("PlanPreview",{"outputId":"DP-1"})["reason"],"planning-unavailable")
        self.exchange()
        self.assertEqual(self.host.submit("PlanPreview",{"outputId":"DP-1"})["reason"],"output-not-selected")

    def test_pause_invalidates_even_when_already_paused_and_revision_unchanged(self):
        self.select_output()
        before=self.exchange(2)
        response=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.host.submit("Pause")
        after=self.exchange(3)
        self.assertEqual(before["revision"],after["revision"])
        self.assertGreater(after["controlGeneration"],before["controlGeneration"])
        self.assertIsNone(after["command"])
        self.exchange(4,report={"id":response["requestId"],"status":"ready","moves":2,"reason":None})
        self.assertNotEqual(self.host.status["planningResult"]["status"],"ready")

    def test_observer_replacement_and_stale_sequence_rejected(self):
        self.select_output()
        self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.assertTrue(self.exchange(epoch="observer-2")["ok"])
        self.assertIsNone(self.host.preview_pending)
        self.assertFalse(self.exchange(2,epoch="observer-1")["ok"])
        self.assertFalse(self.exchange(epoch="observer-2")["ok"])

    def test_settings_save_and_observer_timeout_cancel_pending_preview(self):
        self.select_output()
        self.host.submit("PlanPreview",{"outputId":"DP-1"})
        values=preferences.load_settings(self.path).values
        values["maximumWindows"]=3
        preferences.save_settings(self.path,values)
        self.assertIsNone(self.exchange(2)["command"])
        self.assertIsNone(self.host.preview_pending)
        self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.host.observer_seen=time.monotonic()-2
        self.host.refresh_observer()
        self.assertFalse(self.host.status["planningReady"])
        self.assertEqual(self.host.status["displays"],[])
        self.assertIsNone(self.host.preview_pending)

    def test_malformed_observation_and_oversized_report_fail_closed(self):
        self.select_output()
        response=self.host.submit("PlanPreview",{"outputId":"DP-1"})
        self.assertFalse(self.exchange(2,report={"id":response["requestId"],"status":"ready","moves":13,"reason":None})["ok"])
        self.assertFalse(self.host.status["planningReady"])
        self.assertIsNone(self.host.preview_pending)
        self.assertFalse(self.exchange(3,outputs=[{"id":"DP-1","connector":"DP-1"}]*17)["ok"])
        self.assertFalse(json.loads(self.host.ObserverExchange("observer-1",4," "*16385))["ok"])

    def test_native_cancellation_diagnostics_do_not_enable_actions_and_expire(self):
        native={"source":"compositor-local","immediateCancellation":True,"available":True}
        self.assertTrue(self.exchange(native=native)["ok"])
        self.assertEqual(self.host.status["nativeObservation"],native)
        for key in ("movementReady","dimmingReady","resumeReady"):
            self.assertFalse(self.host.status[key])
        # A working local hook and unavailable activity snapshot are distinct.
        self.assertTrue(self.exchange(2,native=dict(native,available=False))["ok"])
        self.host.observer_seen=time.monotonic()-2
        self.host.refresh_observer()
        self.assertFalse(self.host.status["nativeObservation"]["immediateCancellation"])

    def test_malformed_native_status_clears_previously_reported_capability(self):
        good={"source":"compositor-local","immediateCancellation":True,"available":True}
        bad_values=[None,{},dict(good,source="diagnostic-probe"),dict(good,available=1),
                    dict(good,immediateCancellation="true"),dict(good,source="unknown")]
        sequence=1
        for bad in bad_values:
            self.assertTrue(self.exchange(sequence,native=good)["ok"])
            self.assertFalse(self.exchange(sequence+1,native=bad)["ok"])
            self.assertFalse(self.host.status["nativeObservation"]["immediateCancellation"])
            sequence+=2

    def runtime(self, **changes):
        row = dict(protocol=1, ready=True, armed=False, movementReady=False, busy=False,
                   skipAvailable=False, restoreAvailable=False, warningRemainingSeconds=0,
                   result=None, commandReport=None)
        row.update(changes)
        return row

    def product_exchange(self, sequence, **changes):
        return self.exchange(sequence, native=dict(source="compositor-local", immediateCancellation=True, available=True),
                             runtime=self.runtime(**changes))

    def test_cancellation_trace_preserves_input_then_topology_without_granting_movement(self):
        self.select_output()
        first = dict(reason="native-input", at=1000, armed=True, warningActive=True,
                     commandId="move-1", epoch="native-1", generation=8,
                     topologyGeneration=2, sequence=5, pointer=3, button=2, axis=0, keyboard=0)
        second = dict(first, reason="native-topology", at=1001, commandId=None,
                      warningActive=False, generation=9, topologyGeneration=3)
        self.assertTrue(self.product_exchange(2, cancellationTrace=[first, second],
            result=dict(status="cancelled", reason="native-input"))["ok"])
        self.assertEqual(self.host.status["cancellationTrace"], [first, second])
        self.assertEqual(self.host.status["lastResult"], "cancelled: native-input")
        self.assertTrue(self.host.status["paused"])
        self.assertFalse(self.host.status["movementReady"])
        for sequence, trace in enumerate(([first]*17, [dict(first, generation=-1)],
                [dict(first, armed=1)], [dict(first, at=float('nan'))]), 3):
            self.assertFalse(self.product_exchange(sequence, cancellationTrace=trace)["ok"])

    def test_uncertain_geometry_is_bounded_retained_and_logged_once(self):
        self.select_output()
        frame=dict(x=1,y=2,width=300,height=200)
        geometry=dict(appId='steam',**{'from':frame,'requested':dict(frame,x=100),'observed':dict(frame,x=101)},identity='not-exported')
        failure=dict(status='failed-uncertain',reason='partial-or-external-geometry',committedWindows=0,
                     plannedWindows=2,unverifiedWindows=1,failureBoundary='destination-not-accepted',commandId='move-7',geometryFailure=geometry)
        with self.assertLogs('oled_window_guard.session',level='WARNING') as logs:
            self.assertTrue(self.product_exchange(2,result=failure)['ok'])
            self.assertTrue(self.product_exchange(3,result=failure)['ok'])
        self.assertEqual(len(logs.output),1)
        kept=self.host.status['lastMovementFailure']
        self.assertEqual(kept['geometry']['appId'],'steam')
        self.assertNotIn('identity',kept['geometry'])
        self.assertEqual(kept['unverifiedWindows'],1)
        self.assertTrue(self.product_exchange(4,result=dict(status='committed',committedWindows=2))['ok'])
        self.assertEqual(self.host.status['lastMovementFailure'],kept)
        self.host.clear_runtime()
        self.assertEqual(self.host.status['lastMovementFailure'],kept)
        for sequence,bad in enumerate((dict(failure,unverifiedWindows=13),dict(failure,geometryFailure=dict(geometry,observed=dict(frame,x=float('nan')))),dict(failure,failureBoundary='x'*257)),5):
            self.assertFalse(self.product_exchange(sequence,result=bad)['ok'])

    def test_product_resume_and_move_transport_require_fresh_runtime_and_explicit_arm(self):
        self.select_output()
        values = preferences.load_settings(self.path).values
        values["paused"] = False
        preferences.save_settings(self.path, values)
        self.assertTrue(self.product_exchange(2)["ok"])
        self.assertTrue(self.host.status["paused"])
        self.assertFalse(self.host.status["movementReady"])
        self.assertTrue(self.host.status["resumeReady"])
        self.assertFalse(self.host.submit("MoveNow")["ok"])
        start = self.host.submit("Resume")
        self.assertTrue(start["ok"])
        command = self.product_exchange(3)["runtimeCommand"]
        self.assertEqual(command["id"], start["requestId"])
        self.assertIsNone(self.product_exchange(4)["runtimeCommand"])
        self.product_exchange(5, armed=True, movementReady=True,
                              commandReport=dict(id=start["requestId"], status="completed", reason=None))
        move = self.host.submit("MoveNow", {"outputId": "DP-1"})
        self.assertTrue(move["ok"])
        self.assertEqual(self.host.submit("MoveNow")["reason"], "runtime-busy")
        self.assertEqual(self.product_exchange(6, armed=True, movementReady=True)["runtimeCommand"]["action"], "MoveNow")
        self.host.submit("Pause")
        self.assertIsNone(self.product_exchange(7)["runtimeCommand"])
        self.assertFalse(self.host.status["movementReady"])
        self.assertFalse(self.host.status["dimmingReady"])

    def test_restore_target_mismatch_preserves_available_inverse(self):
        self.select_output()
        values=preferences.load_settings(self.path).values
        values["paused"]=False; preferences.save_settings(self.path,values)
        self.product_exchange(2,armed=True,movementReady=True,restoreAvailable=True,restoreOutputId="DP-1")
        self.assertEqual(self.host.submit("Restore",{"outputId":"HDMI-1"})["reason"],"restore-output-mismatch")
        self.assertTrue(self.host.status["restoreAvailable"])
        self.assertIsNone(self.host.runtime_command)
        self.assertTrue(self.host.submit("Restore",{"outputId":"DP-1"})["ok"])
        self.assertEqual(self.host.runtime_command["outputId"],"DP-1")

    def test_dimming_only_resume_and_restore_brightness_require_native_capability(self):
        self.select_output()
        values=preferences.load_settings(self.path).values
        values.update(movementDisplays=[],dimmingDisplays=["DP-1"],dimWindows=True)
        preferences.save_settings(self.path,values)
        self.product_exchange(2)
        self.assertTrue(self.host.status["resumeReady"])
        self.assertFalse(self.host.submit("RestoreBrightness")["ok"])
        response=self.host.submit("Resume")
        self.assertTrue(response["ok"])
        command=response["requestId"]
        self.product_exchange(3,armed=True,movementReady=False,dimmingReady=True,restoreBrightness=True,
            commandReport=dict(id=command,status="completed",reason=None))
        self.assertTrue(self.host.status["dimmingReady"])
        self.assertFalse(self.host.status["movementReady"])
        self.assertTrue(self.host.status["capabilities"]["restoreBrightness"])
        bright=self.host.submit("RestoreBrightness")
        self.assertTrue(bright["ok"])
        self.assertEqual(self.product_exchange(4,armed=True,dimmingReady=True,restoreBrightness=True)["runtimeCommand"]["action"],"RestoreBrightness")
        self.host.clear_runtime()
        self.assertFalse(self.host.status["dimmingReady"])
        self.assertFalse(self.host.status["capabilities"]["restoreBrightness"])

    def test_brightness_clear_is_allowed_during_input_hold_without_write_authority(self):
        self.select_output()
        values=preferences.load_settings(self.path).values
        values.update(paused=False,dimWindows=True,dimmingDisplays=["DP-1"])
        preferences.save_settings(self.path,values)
        self.product_exchange(2,ready=False,armed=True,movementReady=False,dimmingReady=False,
            restoreBrightness=True,reason="input-state-unknown-or-held")
        self.assertFalse(self.host.status["movementReady"])
        self.assertFalse(self.host.status["dimmingReady"])
        self.assertTrue(self.host.status["capabilities"]["restoreBrightness"])
        self.assertFalse(self.host.submit("Resume")["ok"])
        self.assertFalse(self.host.submit("MoveNow")["ok"])
        self.assertTrue(self.host.submit("RestoreBrightness")["ok"])
        self.assertEqual(self.product_exchange(3,ready=False,armed=True,restoreBrightness=True)["runtimeCommand"]["action"],"RestoreBrightness")
        self.product_exchange(4,ready=False,armed=False,restoreBrightness=True)
        self.assertFalse(self.host.status["capabilities"]["restoreBrightness"])
        self.assertFalse(self.host.submit("RestoreBrightness")["ok"])
        self.host.clear_runtime()
        self.assertFalse(self.host.status["capabilities"]["restoreBrightness"])

    def test_invalid_dimming_status_cannot_enable_brightness(self):
        self.select_output()
        self.assertFalse(self.product_exchange(2,dimmingReady="true")["ok"])
        self.assertFalse(self.host.status["dimmingReady"])
        self.assertFalse(self.product_exchange(3,dimming=dict(healthy=True,maskCount=257,reason="planned"))["ok"])

    def test_product_commands_do_not_compete_with_a_pending_preview(self):
        self.select_output()
        values = preferences.load_settings(self.path).values
        values["paused"] = False
        preferences.save_settings(self.path, values)
        self.product_exchange(2, armed=True, movementReady=True, restoreAvailable=True)
        self.assertTrue(self.host.submit("PlanPreview", {"outputId": "DP-1"})["ok"])
        for action in ("MoveNow", "Restore"):
            self.assertEqual(self.host.submit(action)["reason"], "planning-busy")
        self.assertIsNone(self.host.runtime_command)

    def test_runtime_malformed_or_expired_status_revokes_actions(self):
        self.select_output()
        self.product_exchange(2)
        self.assertTrue(self.host.status["resumeReady"])
        self.assertFalse(self.product_exchange(3, ready=1)["ok"])
        self.assertFalse(self.host.status["resumeReady"])
        self.product_exchange(4)
        self.host.observer_seen = time.monotonic()-2
        self.host.refresh_observer()
        self.assertFalse(self.host.submit("Resume")["ok"])

    def test_runtime_safety_reason_is_reported_and_malformed_reason_is_refused(self):
        self.select_output()
        self.assertTrue(self.product_exchange(2,ready=False,reason="session-not-authoritatively-ready")["ok"])
        self.assertEqual(self.host.status["suspendedReasons"],["session-not-authoritatively-ready"])
        self.assertFalse(self.product_exchange(3,reason=["invalid"])["ok"])
        self.assertFalse(self.host.status["resumeReady"])

    def test_layout_reads_are_async_unique_and_bound_to_observer(self):
        import threading
        ready = threading.Event()
        class Provider:
            def read_snapshot(self):
                ready.wait(1)
                return {"status": "missing", "executionReady": False}
        self.select_output()
        self.host.layout_provider = Provider()
        before = time.monotonic()
        first = json.loads(self.host.LayoutSnapshot("observer-1", "observer-1:1"))
        self.assertTrue(first["pending"])
        self.assertLess(time.monotonic()-before, .1)
        old_future=self.host.layout_future
        self.assertTrue(json.loads(self.host.LayoutSnapshot("observer-1", "observer-1:2"))["pending"])
        self.assertIs(self.host.layout_future, old_future)
        ready.set()
        self.host.layout_future.result(timeout=1)
        result = json.loads(self.host.LayoutSnapshot("observer-1", "observer-1:1"))
        self.assertFalse(result["pending"])
        self.assertFalse(json.loads(self.host.LayoutSnapshot("observer-1", "observer-1:1"))["ok"])
        self.assertFalse(json.loads(self.host.LayoutSnapshot("wrong", "wrong:2"))["ok"])

    def test_next_check_is_bounded_diagnostic_and_cleared_on_loss(self):
        self.select_output()
        next_check=dict(outputId='DP-1',remainingSeconds=61.2)
        self.assertTrue(self.product_exchange(2,nextMoveCheck=next_check)['ok'])
        self.assertEqual(self.host.status['nextMoveCheck'],next_check)
        self.assertFalse(self.host.status['movementReady'])
        self.host.clear_runtime()
        self.assertIsNone(self.host.status['nextMoveCheck'])
        for sequence,bad in enumerate([dict(next_check,remainingSeconds=True),dict(next_check,remainingSeconds=-1),
                dict(next_check,remainingSeconds=float('inf')),dict(next_check,outputId='other'),
                dict(next_check,remainingSeconds=14401)],3):
            self.assertFalse(self.product_exchange(sequence,nextMoveCheck=bad)['ok'])


if __name__ == "__main__":
    unittest.main()

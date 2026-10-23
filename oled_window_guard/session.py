"""Single-instance, default-paused UI session and narrow D-Bus intent endpoint.

The compositor owns guarded transactions. This host supplies settings, unique
intents and fresh read-only layout snapshots; dimming runs in the compositor.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Mapping
import time
import uuid

from PySide6.QtCore import QObject, QTimer, Slot
from PySide6.QtDBus import QDBusConnection, QDBusInterface

from . import preferences
from .layout_provider import LayoutProvider
from .dependencies import check_dependencies
from .preview_data import drawing_preview, rectangle

SERVICE = "org.oledwindowguard.Controller1"
PATH = "/org/oledwindowguard/Controller1"
INTERFACE = SERVICE
MAX_PAYLOAD_BYTES = 16384


def revision(values: Mapping[str, Any]) -> str:
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def unavailable_status(settings_path: Path | str | None = None) -> dict[str, Any]:
    path = Path(settings_path) if settings_path else preferences.default_settings_path()
    loaded = preferences.load_settings(path)
    reasons = ["Guarded compositor runtime has not reported readiness; dimming renderer unavailable"]
    dependencies = check_dependencies()
    reasons.extend(dependencies["issues"])
    if loaded.error:
        reasons.append(f"Settings: {loaded.error}")
    return {"readiness": "unsupported", "paused": True, "suspendedReasons": reasons,
            "lastResult": "none", "resumeReady": False, "movementReady": False,
            "dimmingReady": False, "movementPaused": True, "brightnessPaused": True,
            "movementActiveDisplays": [], "brightnessActiveDisplays": [],
            "movementResumePending": False, "brightnessResumePending": False,
            "movementResumeReady": False, "brightnessResumeReady": False, "transactionActive": False, "skipAvailable": False,
            "restoreAvailable": False, "canPause": True,
            "capabilities": {"restoreBrightness": False},
            "settingsRevision": revision(loaded.values), "displays": [], "planningReady": False,
            "planningResult": {"status": "none", "moves": 0},
            "nativeObservation": {"source": "unavailable", "immediateCancellation": False, "available": False},
            "dependencies": dependencies}


class SessionHost(QObject):
    def __init__(self, app: Any, *, settings_path: Path | str | None = None, expected_adapter_source: str | None = None):
        super().__init__()
        from .dashboard import ControllerIntentClient, Dashboard

        self.app = app
        self.settings_path = Path(settings_path) if settings_path else preferences.default_settings_path()
        self.expected_adapter_source = expected_adapter_source
        self.status = unavailable_status(self.settings_path)
        self.controller_epoch = uuid.uuid4().hex
        self.layout_provider = LayoutProvider(timeout=0.2)
        self.layout_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="owg-layout")
        self.layout_request = None
        self.layout_future = None
        self.layout_started = 0.0
        self.layout_sequence = 0
        self.runtime_command = None
        self.runtime_pending = None
        self.runtime_started = 0.0
        self.control_generation = 0
        self.observer_epoch = None
        self.observer_sequence = 0
        self.observer_seen = 0.0
        self.retired_observers = set()
        self.preview_command = None
        self.preview_pending = None
        self.preview_started = 0.0
        self.preview_output = None
        self.preview_seeds = {}
        self.alias_key = None
        self.application_aliases = {}
        self.exchange_failure = None
        self.exchange_failure_at = 0.0
        self.observer_timer = QTimer(self)
        self.observer_timer.setInterval(250)
        self.observer_timer.timeout.connect(self.refresh_observer)
        self.observer_timer.start()
        self.dashboard = Dashboard(ControllerIntentClient(self.submit), settings_path=self.settings_path)
        self.dashboard.previewDisplayChanged.connect(self.select_preview_output)
        self.dashboard.update_status(self.status)
        self.dashboard.set_displays(self.status["displays"])

    def show_startup(self) -> None:
        loaded = preferences.load_settings(self.settings_path)
        if not loaded.error and loaded.values["startMinimized"]:
            self.dashboard.showMinimized()
        else:
            self.submit("Open")

    def select_preview_output(self, output: str) -> None:
        self.preview_output = output

    def submit(self, action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(payload or {})
        resume_actions = ("Resume", "ResumeMovement", "ResumeBrightness")
        component_pauses = ("PauseMovement", "PauseBrightness")
        if action in (*resume_actions, *component_pauses, "MoveNow", "Restore", "Skip", "RestoreBrightness", "DimDisplayNow", "DimAllDisplaysNow"):
            self.refresh_observer()
            loaded = preferences.load_settings(self.settings_path)
            flag = {"Resume": "resumeReady", "ResumeMovement":"movementResumeReady", "ResumeBrightness":"brightnessResumeReady", "MoveNow": "movementReady", "Restore": "restoreAvailable", "Skip": "skipAvailable"}.get(action)
            permitted = True if action in component_pauses else self.status.get("capabilities", {}).get("restoreBrightness") if action == "RestoreBrightness" else self.status.get(flag)
            if action in ("DimDisplayNow", "DimAllDisplaysNow"):
                permitted = bool(self.status.get("dimDisplayOutputs")) and self.status.get("brightnessPaused") is False and self.status.get("capabilities", {}).get("restoreBrightness") is True
            if loaded.error or permitted is not True:
                return {"ok": False, "reason": "capability-unavailable", "status": self.status}
            if self.preview_pending and action in ("MoveNow", "Restore"):
                return {"ok": False, "reason": "planning-busy", "status": self.status}
            if self.runtime_pending and action not in (*component_pauses, "ResumeMovement", "ResumeBrightness", "Skip", "RestoreBrightness"):
                return {"ok": False, "reason": "runtime-busy", "status": self.status}
            output = payload.get("outputId")
            if action == "RestoreBrightness" and output is not None and output not in self.status.get("restoreBrightnessOutputs", []):
                return {"ok": False, "reason": "display-not-dimmed", "status": self.status}
            if action == "DimDisplayNow":
                if output not in self.status.get("dimDisplayOutputs", []):
                    return {"ok": False, "reason": "display-not-brightness-guarded", "status": self.status}
            if action == "MoveNow":
                outputs = [row["id"] for row in self.status["displays"] if row["id"] in loaded.values["movementDisplays"]]
                output = output or (self.preview_output if self.preview_output in outputs else None) or (outputs[0] if outputs else None)
                if output not in outputs:
                    return {"ok": False, "reason": "output-not-selected", "status": self.status}
            if action == "Restore" and output is not None and output != self.status.get("restoreOutputId"):
                return {"ok": False, "reason": "restore-output-mismatch", "status": self.status}
            if action in resume_actions:
                values = dict(loaded.values, paused=False)
                if loaded.values["paused"] or action == "Resume":
                    preferences.save_settings(self.settings_path, values)
                self.dashboard.settings = values
                if action == "Resume":
                    self.cancel_preview("resumed")
                self.status["settingsRevision"] = revision(values)
            elif action == "PauseMovement":
                self.cancel_preview("movement-paused", invalidate_runtime=False)
            elif action == "Skip":
                # Replace a pending movement transport, if any, with cancellation.
                self.runtime_command = None
            command = {"id": uuid.uuid4().hex, "action": action, "outputId": output}
            if action == "MoveNow":
                command["seed"] = self.preview_seeds.get(output, int.from_bytes(os.urandom(4), "big"))
            self.runtime_command = command
            self.runtime_pending = command["id"]
            self.runtime_started = time.monotonic()
            self.status["transactionActive"] = True
            if action not in ("ResumeBrightness", "PauseBrightness", "RestoreBrightness", "DimDisplayNow", "DimAllDisplaysNow"):
                self.status["restoreAvailable"] = False
            self.dashboard.update_status(self.status)
            return {"ok": True, "requestId": command["id"], "status": self.status}
        if action == "PlanPreview":
            self.refresh_observer()
            loaded = preferences.load_settings(self.settings_path)
            output_id = payload.get("outputId")
            if loaded.error or not self.status.get("planningReady"):
                return {"ok": False, "reason": "planning-unavailable", "status": self.status}
            if self.preview_pending or self.runtime_pending or self.status.get("transactionActive"):
                return {"ok": False, "reason": "planning-busy", "status": self.status}
            if output_id not in loaded.values["movementDisplays"] or not any(
                    row["id"] == output_id for row in self.status["displays"]):
                return {"ok": False, "reason": "output-not-selected", "status": self.status}
            command = {"id": uuid.uuid4().hex, "outputId": output_id,
                       "revision": revision(loaded.values), "seed": int.from_bytes(os.urandom(4), "big")}
            self.preview_command = command
            self.preview_seeds[output_id] = command["seed"]
            self.preview_pending = command["id"]
            self.preview_started = time.monotonic()
            self.status["planningResult"] = {"id": command["id"], "status": "queued", "moves": 0}
            return {"ok": True, "requestId": command["id"], "status": self.status}
        if action == "Open":
            self.dashboard.showNormal()
            self.dashboard.raise_()
            self.dashboard.activateWindow()
            return {"ok": True, "status": self.status}
        if action == "Pause":
            loaded = preferences.load_settings(self.settings_path)
            if loaded.error:
                return {"ok": False, "reason": "settings-unavailable", "status": self.status}
            values = dict(loaded.values)
            values["paused"] = True
            preferences.save_settings(self.settings_path, values)
            self.dashboard.settings = values
            self.cancel_preview("paused")
            self.status = unavailable_status(self.settings_path)
            self.dashboard.update_status(self.status)
            return {"ok": True, "status": self.status}
        if action == "SettingsChanged":
            loaded = preferences.load_settings(self.settings_path)
            actual = revision(loaded.values)
            if loaded.error or payload.get("revision") != actual:
                return {"ok": False, "reason": "settings-revision-mismatch", "status": self.status}
            self.cancel_preview("settings-changed")
            self.status = unavailable_status(self.settings_path)
            self.dashboard.settings = loaded.values
            self.dashboard.update_status(self.status)
            return {"ok": True, "status": self.status}
        if action == "Quit":
            self.dashboard.cancel_update_download()
            self.dashboard.dock_menu.stop()
            self.cancel_preview("quit")
            self.dashboard.warning_audio.stop()
            self.layout_executor.shutdown(wait=False, cancel_futures=True)
            self.dashboard.tray.hide()
            self.dashboard.hide()
            QTimer.singleShot(0, self.app.quit)
            return {"ok": True, "status": self.status}
        return {"ok": False, "reason": "capability-unavailable", "status": self.status}

    def clear_runtime(self) -> None:
        self.status["nextMoveCheck"] = None
        self.status["restorePreview"] = None
        self.status.update(readiness="unsupported", paused=True, resumeReady=False, movementReady=False,
                           dimmingReady=False, transactionActive=False, skipAvailable=False, restoreAvailable=False)
        self.status.update(movementPaused=True, brightnessPaused=True, movementResumeReady=False, brightnessResumeReady=False,
                           movementActiveDisplays=[], brightnessActiveDisplays=[], movementResumePending=False, brightnessResumePending=False)
        self.status["capabilities"]["restoreBrightness"] = False
        self.status["dimDisplayOutputs"] = []
        self.status["restoreBrightnessOutputs"] = []
        self.status["suspendedReasons"] = ["Guarded runtime unavailable"]

    def apply_runtime(self, runtime: Any, values: Mapping[str, Any]) -> None:
        if runtime is None:
            self.clear_runtime()
            return
        keys = ("ready", "armed", "movementReady", "busy", "skipAvailable", "restoreAvailable")
        if not isinstance(runtime, dict) or type(runtime.get("protocol")) is not int or runtime["protocol"] != 1 or any(type(runtime.get(k)) is not bool for k in keys):
            raise ValueError("invalid-runtime-status")
        reason=runtime.get("reason")
        if reason is not None and (not isinstance(reason,str) or not 1<=len(reason)<=128):
            raise ValueError("invalid-runtime-reason")
        restore_output = runtime.get("restoreOutputId")
        if restore_output is not None and (not isinstance(restore_output, str) or len(restore_output) > 1024):
            raise ValueError("invalid-restore-output")
        self.status["restoreOutputId"] = restore_output
        next_check = runtime.get("nextMoveCheck")
        if next_check is not None:
            if (not isinstance(next_check, dict) or not isinstance(next_check.get("outputId"), str)
                    or not 1 <= len(next_check["outputId"]) <= 1024
                    or type(next_check.get("remainingSeconds")) not in (int, float)
                    or not math.isfinite(next_check["remainingSeconds"])
                    or not 0 <= next_check["remainingSeconds"] <= 14400
                    or next_check["outputId"] not in values["movementDisplays"]):
                raise ValueError("invalid-next-move-check")
            next_check = {key: next_check[key] for key in ("outputId", "remainingSeconds")}
        self.status["nextMoveCheck"] = next_check
        restore = runtime.get("restorePreview")
        if restore is not None and (not isinstance(restore, dict) or not isinstance(restore.get('destinations'), list)
                                    or runtime['busy'] is not True or runtime['skipAvailable'] is not True):
            raise ValueError('invalid-restore-preview')
        self.status['restorePreview'] = drawing_preview(restore,
            [row['id'] for row in self.status['displays']],len(restore['destinations'])) if restore is not None else None
        remaining = runtime.get("warningRemainingSeconds")
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 <= remaining <= 60:
            raise ValueError("invalid-runtime-warning")
        trace = runtime.get("cancellationTrace", [])
        if not isinstance(trace, list) or len(trace) > 16:
            raise ValueError("invalid-cancellation-trace")
        for event in trace:
            if (not isinstance(event, dict) or not isinstance(event.get("reason"), str) or
                    not 1 <= len(event["reason"]) <= 128 or
                    type(event.get("armed")) is not bool or type(event.get("warningActive")) is not bool):
                raise ValueError("invalid-cancellation-trace")
            for key in ("at", "generation", "topologyGeneration", "sequence", "pointer", "button", "axis", "keyboard"):
                value = event.get(key)
                if value is None and key != "at":
                    continue
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 9007199254740991 or int(value) != value:
                    raise ValueError("invalid-cancellation-trace")
            for key in ("epoch", "commandId"):
                value = event.get(key)
                if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 256):
                    raise ValueError("invalid-cancellation-trace")
        # Diagnostic metadata cannot supply readiness or movement permission.
        self.status["cancellationTrace"] = [{key: event.get(key) for key in
            ("reason", "at", "armed", "warningActive", "epoch", "commandId", "generation",
             "topologyGeneration", "sequence", "pointer", "button", "axis", "keyboard")} for event in trace]
        native = self.status["nativeObservation"]
        for key in ("dimmingReady", "restoreBrightness"):
            if key in runtime and type(runtime[key]) is not bool:
                raise ValueError("invalid-dimming-status")
        for key in ("dimDisplayOutputs", "restoreBrightnessOutputs"):
            ids=runtime.get(key, [])
            if (not isinstance(ids,list) or len(ids)>16 or
                    any(not isinstance(v,str) or v not in values['dimmingDisplays'] or
                        not any(row['id']==v for row in self.status['displays']) for v in ids) or
                    len(set(ids))!=len(ids)):
                raise ValueError('invalid-dim-display-outputs')
            self.status[key]=ids
        dimming=runtime.get("dimming")
        if dimming is not None:
            if (not isinstance(dimming,dict) or type(dimming.get("healthy")) is not bool or
                    type(dimming.get("maskCount")) is not int or not 0<=dimming["maskCount"]<=256 or
                    not isinstance(dimming.get("reason"),str) or len(dimming["reason"])>128):
                raise ValueError("invalid-dimming-status")
            self.status["dimming"]=dict(dimming)
        movement_selected=any(row["id"] in values["movementDisplays"] for row in self.status["displays"])
        dimming_selected=(values["dimWindows"] or values["dimDisplays"]) and any(row["id"] in values["dimmingDisplays"] for row in self.status["displays"])
        ready = self.status["dependencies"]["supportedMatrix"] and runtime["ready"] and native["available"] and native["immediateCancellation"] and (movement_selected or dimming_selected)
        for key in ("movementArmed", "brightnessArmed", "movementResumeReady", "brightnessResumeReady", "movementResumePending", "brightnessResumePending"):
            if key in runtime and type(runtime[key]) is not bool:
                raise ValueError("invalid-guarding-status")
        for key, selection in (("movementActiveDisplays", "movementDisplays"), ("brightnessActiveDisplays", "dimmingDisplays")):
            ids = runtime.get(key)
            if ids is not None and (not isinstance(ids, list) or len(ids)>16 or
                    any(not isinstance(v,str) or not 1<=len(v)<=1024 for v in ids) or len(set(ids))!=len(ids)):
                raise ValueError("invalid-guarding-displays")
            self.status[key] = [v for v in (ids or []) if v in values[selection] and any(row['id']==v for row in self.status['displays'])]
        active = ready and runtime["armed"] and values["paused"] is False
        movement_active = active and runtime.get("movementArmed", runtime["armed"])
        brightness_active = active and runtime.get("brightnessArmed", runtime["armed"])
        resume_allowed=(values["resumeAfterUnlock"] and not values["paused"] and
                        native["available"] and native["immediateCancellation"])
        movement_waiting=resume_allowed and runtime.get("movementResumePending",False)
        brightness_waiting=resume_allowed and runtime.get("brightnessResumePending",False)
        self.status.update(readiness="ready" if ready else "unsupported", resumeReady=ready,
                           movementResumePending=movement_waiting,brightnessResumePending=brightness_waiting,
                           movementPaused=not (movement_active or movement_waiting), brightnessPaused=not (brightness_active or brightness_waiting),
                           movementResumeReady=ready and movement_selected and runtime.get("movementResumeReady", True),
                           brightnessResumeReady=ready and dimming_selected and runtime.get("brightnessResumeReady", True),
                           paused=not (movement_active or brightness_active or movement_waiting or brightness_waiting), movementReady=movement_active and movement_selected and runtime["movementReady"], dimmingReady=brightness_active and dimming_selected and runtime.get("dimmingReady",False),
                           transactionActive=runtime["busy"] or self.runtime_pending is not None, skipAvailable=runtime["busy"] and runtime["skipAvailable"],
                           restoreAvailable=movement_active and not runtime["busy"] and runtime["restoreAvailable"],
                           warningRemainingSeconds=remaining,
                           suspendedReasons=[] if ready else [reason or "Input/session facts or selected displays are unavailable"])
        # Removing masks acquires no geometry or dimming authority. A fresh,
        # armed renderer may restore brightness even while input holds make
        # readiness false; observer loss, Pause and disarm still revoke it.
        self.status["capabilities"]["restoreBrightness"] = (
            self.status["dependencies"]["supportedMatrix"] and native["available"] and
            native["immediateCancellation"] and runtime.get("brightnessArmed", runtime["armed"]) and values["paused"] is False and
            runtime.get("restoreBrightness",False))
        for key in ("result", "commandReport"):
            report = runtime.get(key)
            if report is not None:
                if not isinstance(report, dict) or not isinstance(report.get("status"), str) or len(report["status"])>64 or (report.get("reason") is not None and (not isinstance(report["reason"], str) or len(report["reason"])>256)):
                    raise ValueError("invalid-runtime-result")
        if runtime.get("result"):
            result = runtime["result"]
            self.status["lastResult"] = result["status"] + (": " + result["reason"] if result.get("reason") else "")
            for field in ("committedWindows", "plannedWindows", "unverifiedWindows"):
                count = runtime["result"].get(field)
                if count is not None and (type(count) is not int or not 0 <= count <= 12):
                    raise ValueError("invalid-movement-count")
                self.status[field] = count
            boundary = result.get("failureBoundary")
            command_id = result.get("commandId")
            if any(value is not None and (not isinstance(value, str) or len(value) > 256)
                   for value in (boundary, command_id)):
                raise ValueError("invalid-movement-diagnostic")
            geometry = result.get("geometryFailure")
            if geometry is not None:
                if (not isinstance(geometry, dict) or not isinstance(geometry.get("appId"), str)
                        or len(geometry["appId"]) > 128):
                    raise ValueError("invalid-movement-diagnostic")
                geometry = dict(appId=''.join(c for c in geometry['appId'] if c.isprintable()),
                                **{key: rectangle(geometry.get(key)) for key in ('from', 'requested', 'observed')})
            if result['status'] in ('failed-uncertain', 'cancelled-write-outcome-unknown'):
                failure = dict(status=result['status'], reason=result.get('reason'),
                               failureBoundary=boundary, commandId=command_id, geometry=geometry,
                               **{key: self.status[key] for key in ('committedWindows', 'plannedWindows', 'unverifiedWindows')})
                if failure != self.status.get('lastMovementFailure'):
                    # A single bounded, title-free record survives subsequent
                    # results in status and the controller's system journal.
                    logging.getLogger(__name__).warning('OWG_MOVEMENT_UNCERTAIN %s', json.dumps(failure, separators=(',', ':')))
                    self.status['lastMovementFailure'] = failure
        report = runtime.get("commandReport")
        if report and report.get("id") == self.runtime_pending and report["status"] in ("completed", "rejected"):
            self.runtime_pending = None
            self.runtime_command = None
            self.status["transactionActive"] = runtime["busy"]

    @Slot(str, str, result=str)
    def LayoutSnapshot(self, epoch: str, request_id: str) -> str:
        """Poll a bounded, unique fresh read on a single background worker."""
        def reply(**fields):
            return json.dumps(dict(epoch=epoch, id=request_id, **fields), separators=(",", ":"))
        try:
            if epoch != self.observer_epoch or not self.observer_seen or time.monotonic()-self.observer_seen >= 1.5:
                raise ValueError("observer-unavailable")
            prefix = epoch+":"
            if not request_id.startswith(prefix) or len(request_id)>160:
                raise ValueError("invalid-library-request")
            number = int(request_id[len(prefix):])
            key = (epoch, request_id)
            if self.layout_request != key:
                if number <= self.layout_sequence:
                    raise ValueError("stale-library-request")
                if self.layout_future and not self.layout_future.done():
                    # A canceled compositor callback may leave its bounded read
                    # draining. Poll until it ends, then start a genuinely new
                    # sample; never reuse that old result or queue another job.
                    return reply(ok=True, pending=True)
                self.layout_sequence = number
                self.layout_request = key
                self.layout_started = time.monotonic()
                self.layout_future = self.layout_executor.submit(self.layout_provider.read_snapshot)
            if time.monotonic()-self.layout_started >= 2.5:
                raise ValueError("library-timeout")
            if not self.layout_future.done():
                return reply(ok=True, pending=True)
            library = self.layout_future.result()
            result = reply(ok=True, pending=False, library=library)
            # A completed receipt is delivered once, never reusable as a new read.
            self.layout_request = None
            if len(result.encode())>65536:
                raise ValueError("library-too-large")
            return result
        except (ValueError, TypeError, OSError) as exc:
            return reply(ok=False, reason=str(exc))

    def cancel_preview(self, reason: str, *, invalidate_runtime: bool = True) -> None:
        if invalidate_runtime:
            self.control_generation += 1
        self.runtime_command = None
        self.runtime_pending = None
        result_id = self.preview_pending or self.status.get("planningResult", {}).get("id")
        if result_id:
            self.status["planningResult"] = {"id": result_id, "status": "discarded", "reason": reason, "moves": 0}
        self.preview_command = None
        self.preview_pending = None

    def refresh_observer(self) -> None:
        now = time.monotonic()
        if self.observer_seen and now - self.observer_seen >= 1.5:
            self.clear_runtime()
            self.status["planningReady"] = False
            self.status["nativeObservation"] = {"source": "unavailable", "immediateCancellation": False, "available": False}
            self.status["displays"] = []
            self.cancel_preview("observer-unavailable")
            self.observer_seen = 0.0
            self.dashboard.update_status(self.status)
            self.dashboard.set_displays([])
        if self.runtime_pending and now-self.runtime_started >= 120:
            self.cancel_preview("runtime-timeout")
            self.clear_runtime()
        if self.preview_pending and now - self.preview_started >= 4:
            self.cancel_preview("planning-timeout")

    @Slot(str, int, str, result=str)
    def ObserverExchange(self, epoch: str, sequence: int, payload_json: str) -> str:
        """Bounded heartbeat, capability reports and unique product intents.

        Executable plans and inverse records stay inside the compositor.
        """
        try:
            if not epoch or len(epoch) > 128 or sequence < 1 or len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                raise ValueError("invalid-observer-envelope")
            payload = json.loads(payload_json)
            if not isinstance(payload, dict) or not isinstance(payload.get("outputs"), list) or len(payload["outputs"]) > 16:
                raise ValueError("invalid-observer-outputs")
            outputs, ids = [], set()
            for row in payload["outputs"]:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] or len(row["id"]) > 1024 or row["id"] in ids:
                    raise ValueError("invalid-observer-output")
                if not isinstance(row.get("connector"), str) or len(row["connector"]) > 256:
                    raise ValueError("invalid-observer-connector")
                ids.add(row["id"])
                outputs.append({"id": row["id"], "name": row["connector"]})
            if epoch in self.retired_observers or (epoch == self.observer_epoch and sequence <= self.observer_sequence):
                raise ValueError("stale-observer")
            if epoch != self.observer_epoch:
                if self.observer_epoch:
                    if len(self.retired_observers) >= 64:
                        raise ValueError("observer-restart-limit")
                    self.retired_observers.add(self.observer_epoch)
                self.cancel_preview("observer-replaced")
                self.observer_epoch = epoch
                self.layout_request = None
                self.layout_sequence = 0
            self.observer_sequence = sequence
            self.observer_seen = time.monotonic()
            source = payload.get("adapterSource")
            if source is not None and (not isinstance(source, str) or not source.startswith("file:///") or len(source) > 4096):
                raise ValueError("invalid-adapter-source")
            if self.expected_adapter_source is not None and source != self.expected_adapter_source:
                raise ValueError("installed-adapter-mismatch")
            # Diagnostic only: this cannot supply readiness or writer authority.
            self.status["adapterSource"] = source
            self.status["observerEpoch"] = epoch
            loaded = preferences.load_settings(self.settings_path)
            if loaded.error:
                self.cancel_preview("settings-unavailable")
                raise ValueError("settings-unavailable")
            from .display_identity import rebind_displays
            rebound, port_changes = rebind_displays(loaded.values, outputs)
            if port_changes:
                preferences.save_settings(self.settings_path, rebound)
                loaded = preferences.load_settings(self.settings_path)
                self.dashboard.settings = loaded.values
                self.preview_output = port_changes.get(self.preview_output, self.preview_output)
            actual = revision(loaded.values)
            alias_key = tuple(sorted(set(loaded.values["excludedApps"] + loaded.values["dimmingExcludedApps"])))
            if alias_key != self.alias_key:
                from .application_identity import exclusion_aliases
                self.application_aliases = exclusion_aliases(alias_key)
                self.alias_key = alias_key
                self.dashboard.application_aliases = self.application_aliases
            if actual != self.status["settingsRevision"]:
                self.cancel_preview("settings-changed")
            self.status["settingsRevision"] = actual
            displays_changed = self.status["displays"] != outputs
            self.status["displays"] = outputs
            self.status["planningReady"] = payload.get("available") is True
            native = payload.get("native", {"source": "unavailable", "immediateCancellation": False, "available": False})
            if (not isinstance(native, dict) or native.get("source") not in ("unavailable", "diagnostic-probe", "compositor-local") or
                    type(native.get("immediateCancellation")) is not bool or type(native.get("available")) is not bool or
                    (native["immediateCancellation"] and native["source"] != "compositor-local")):
                raise ValueError("invalid-native-observation-status")
            self.status["nativeObservation"] = {key: native[key] for key in ("source", "immediateCancellation", "available")}
            runtime = payload.get("runtime")
            if self.expected_adapter_source is not None and (not isinstance(runtime, dict) or runtime.get("controllerEpoch") != self.controller_epoch):
                # A retained script can poll between registration and replacement.
                # Its previous armed state must never activate this new host.
                self.clear_runtime()
            else:
                self.apply_runtime(runtime, loaded.values)
            self.status["livePreview"] = drawing_preview(payload.get("livePreview"), ids, 0)
            report = payload.get("report")
            if isinstance(report, dict) and report.get("id") and ((not self.preview_pending and report.get("purpose") in ("movement", "warning-preview")) or report.get("id") == self.preview_pending or
                    (report.get("id") == self.status.get("planningResult", {}).get("id") and report.get("status") == "discarded")):
                result_status = report.get("status")
                moves = report.get("moves")
                reason = report.get("reason")
                if result_status not in ("planning", "ready", "no-plan", "discarded", "rejected") or type(moves) is not int or not 0 <= moves <= 12 or (reason is not None and (not isinstance(reason, str) or len(reason) > 256)):
                    raise ValueError("invalid-planning-report")
                preview = drawing_preview(report.get("preview"), ids, moves) if result_status in ("ready", "no-plan") else None
                self.status["planningResult"] = {"id": report["id"], "status": result_status, "reason": reason, "moves": moves, "preview": preview}
                if report.get("purpose") in ("preview", "movement", "warning-preview"):
                    self.status["planningResult"]["purpose"] = report["purpose"]
                if result_status != "planning" and report.get("id") == self.preview_pending:
                    self.preview_pending = None
            command = self.preview_command
            self.preview_command = None
            self.dashboard.update_status(self.status)
            if displays_changed or port_changes:
                self.dashboard.set_displays(outputs)
            response = json.dumps({"ok": True, "epoch": epoch, "sequence": sequence, "revision": actual,
                                   "settings": loaded.values, "controlGeneration": self.control_generation, "controllerEpoch": self.controller_epoch, "monotonicMs": time.monotonic()*1000, "command": command,
                                   "runtimeCommand": self.runtime_command, "previewOutputId": self.preview_output,
                                   "applicationAliases": self.application_aliases}, separators=(",", ":"))
            self.runtime_command = None
            if len(response.encode("utf-8")) > 65536:
                raise ValueError("observer-settings-too-large")
            return response
        except (ValueError, TypeError, OSError, preferences.SettingsError) as exc:
            failure = str(exc)[:256]
            now = time.monotonic()
            if failure != self.exchange_failure or now-self.exchange_failure_at >= 5:
                logging.getLogger(__name__).warning("OWG_OBSERVER_EXCHANGE_REJECTED %s bytes=%d", failure, len(payload_json.encode('utf-8')))
                self.exchange_failure, self.exchange_failure_at = failure, now
            self.clear_runtime()
            self.dashboard.update_status(self.status)
            self.status["planningReady"] = False
            self.status["nativeObservation"] = {"source": "unavailable", "immediateCancellation": False, "available": False}
            self.cancel_preview("observer-exchange-failed")
            return json.dumps({"ok": False, "reason": str(exc)}, separators=(",", ":"))

    @Slot(str, str, result=str)
    def Intent(self, action: str, payload_json: str) -> str:
        try:
            if len(payload_json.encode("utf-8")) > MAX_PAYLOAD_BYTES:
                return json.dumps({"ok": False, "reason": "payload-too-large"})
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                return json.dumps({"ok": False, "reason": "invalid-payload"})
            return json.dumps(self.submit(action, payload), separators=(",", ":"))
        except (ValueError, TypeError, preferences.SettingsError, OSError) as exc:
            return json.dumps({"ok": False, "reason": str(exc)})

    @Slot(result=str)
    def Status(self) -> str:
        return json.dumps(self.status, separators=(",", ":"))


def register_host(host: SessionHost) -> bool:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected() or not bus.registerService(SERVICE):
        return False
    if not bus.registerObject(PATH, INTERFACE, host, QDBusConnection.RegisterOption.ExportAllSlots):
        bus.unregisterService(SERVICE)
        return False
    return True


def call_host(action: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        return None
    interface = QDBusInterface(SERVICE, PATH, INTERFACE, bus)
    if not interface.isValid():
        return None
    message = interface.call("Intent", action, json.dumps(dict(payload or {}), separators=(",", ":")))
    if message.type() == message.MessageType.ErrorMessage or not message.arguments():
        return None
    try:
        reply = json.loads(str(message.arguments()[0]))
        return reply if isinstance(reply, dict) else None
    except (ValueError, TypeError):
        return None

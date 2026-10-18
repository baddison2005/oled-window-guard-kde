# Controller/compositor interface — review reference

Copyright © 2026 Dr. Brett Addison. SPDX-License-Identifier: MIT.
The owner approved MIT terms for these interface definitions and the shared
settings schema. See `packaging/licenses/MIT.txt`. This document describes the
existing protocol; it does not change it or bypass its safety checks.

All messages use the session bus. The controller service and interface are
`org.oledwindowguard.Controller1`; its object path is
`/org/oledwindowguard/Controller1`. JSON messages carry plain data. They contain
no executable QML/JS, native pointers, memory handles or geometry-write plans.

| Method | Input | Reply and role |
| --- | --- | --- |
| `Intent` | action string, JSON object string | JSON `{ok, status, ...}`; user/controller actions |
| `Status` | no arguments | JSON capability/readiness and lifecycle status |
| `ObserverExchange` | observer epoch string, sequence integer, JSON string | configuration, controller generation/epoch and pending command data |
| `LayoutSnapshot` | observer epoch string, unique request ID string | pending/error or fresh imported layout-library data |

`Intent` accepts Open, Pause, Quit, Resume, ResumeMovement, PauseMovement,
ResumeBrightness, PauseBrightness, MoveNow, Skip, Restore,
RestoreBrightness, SettingsChanged and PlanPreview only as permitted by the
controller's actual capability checks. The availability of a method is not
movement authorization. SettingsChanged carries a revision; PlanPreview and
MoveNow identify an output. The settings fields/defaults/ranges are defined in
`schema/settings.schema.json`, licensed under MIT alongside this document.

Movement and brightness guarding have separate volatile arming flags. Scoped
start/pause intents affect only their component; Pause and Quit stop both.
Runtime status includes movementArmed, brightnessArmed, movementResumeReady and
brightnessResumeReady. Public status exposes movementPaused and brightnessPaused.
Fresh controller/compositor startup always leaves both paused, including when
startMinimized or resumeAfterUnlock is enabled. Resume after unlock is opt-in:
only components active before the observed lock may resume, after authoritative
unlocked/awake/active session and released-input checks. Controller/observer loss,
settings changes and unknown input revoke that session-local intent. Temporary
lock/sleep/inactive-session interruptions preserve previously armed output scopes
through display-power topology changes. Stale heartbeats while interrupted
retain intent without authorizing writes; fresh same-controller contact is
required, with a five-second contact recovery window after return. Awake
controller loss still revokes guarding. movementResumePending and
brightnessResumePending distinguish waiting intent from manual pause. No arming state is saved in preferences.

Runtime movementActiveDisplays/brightnessActiveDisplays identify the armed,
connected scopes. Topology changes invalidate in-flight geometry authority and
inverses, but keep ongoing guarding and check deadlines on surviving targets.
Disconnected targets drop out of the armed scopes and require explicit start
to rejoin. Session-resume intent preserves its original output identities across temporary
display-power topology changes.
Persisted selections/overrides may migrate across connectors only when the
manufacturer/model/serial identity has one unambiguous match without conflicting
saved overrides. A matching connector alone never identifies a monitor.

A movement check with no eligible windows completes as
`skipped: no-eligible-windows` before showing a warning. Scheduled checks advance
the display's deadline and remain armed, so eligibility can recover later.

The compositor sends `ObserverExchange` approximately every 100 ms. Its JSON
contains output identities, planning availability/report, runtime capability and
completion/cancellation status, and native observation availability. The
controller validates bounds, epoch and sequence and returns `ok`, echoed epoch
and sequence, settings/revision, controlGeneration, controllerEpoch,
monotonicMs, optional planning command and optional runtime command. Incoming controller payloads are bounded to 16,384 bytes; compositor-side
reply handling and controller output bound JSON replies to 65,536 bytes; version/epoch/freshness checks remain authoritative.

Executable movement plans, window-registry objects and Restore inverse records
remain inside the compositor. The Python controller does not import the GPL JS
planner or load the native observer into its own address space. Its adapter
loader requests that KWin load the package through KWin's scripting D-Bus API.
The native observer is loaded in KWin's process and links to KWin/Qt libraries.

`LayoutSnapshot` is a unique asynchronous provider request associated with the
current observer epoch. The ID begins with that epoch plus a colon and increasing
serial. The reply echoes the epoch/ID and returns `ok`, `pending`, a library or
an error reason. It reads Window Layouts configuration; execution-time layout
and geometry checks remain inside the compositor.

There is no vendor secret, production signing-key verification or closed-code
execution in this protocol. An independently written controller can implement
the documented service, subject to the same schema and safety validation.
Both original controller and compositor implementations are licensed under
GPL-3.0-or-later; the earlier evaluation/GPL licensing split is superseded.
The MIT interface grant does not change the implementation license.

Authoritative implementation references: `oled_window_guard/session.py`,
`packages/kwin-script/contents/ui/PlanningController.qml`,
`ImportedLayouts.qml` and `RuntimeController.qml`. This is a descriptive review
reference, not a complete separately versioned API specification or a tested
replacement controller.

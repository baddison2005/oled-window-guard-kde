#!/usr/bin/env python3
"""Operator-started control panel for a disposable-window physical test."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QVBoxLayout, QWidget

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oled_window_guard.session import call_host


class Control(QWidget):
    def __init__(self, receipt_dir, output="DP-1", test="mouse"):
        super().__init__()
        self.receipt_dir=receipt_dir
        self.receipt=None
        self.setWindowTitle("OWG physical test — Restore after manual movement" if test in ("stale-restore","stale-resize") else
                            "OWG physical test — Imported Minis Rotation and Restore" if test=="imported-rotation-restore" else
                            "OWG physical test — Imported Minis Move and Restore" if test=="imported-group-restore" else
                            "OWG physical test — Group Rotation and Restore" if test=="group-rotation-restore" else
                            "OWG physical test — Two-window group Move and Restore" if test=="group-restore" else
                            "OWG physical test — Lock during warning" if test=="lock-warning" else
                            "OWG physical test — Cable disconnect during warning" if test=="topology-warning-both" else
                            "OWG physical test — Fixed-size resize refusal" if test=="resize-refusal-both" else
                            "OWG physical test — GTK Wayland Move and Restore" if test in ("gtk-both","gtk4-both") else
                            "OWG physical test — Continuous scheduled movement" if test=="stability" else
                            "OWG physical test — Paused readiness observation" if test=="stability-paused" else
                            "OWG physical test — KDE reload during warning" if test=="reconfigure-both" else
                            "OWG physical test — Fractional display scaling" if test=="scaling-both" else
                            "OWG physical test — Fixed and bounded window sizes" if test=="constraints-both" else
                            "OWG physical test — XWayland Move and Restore on both monitors" if test=="xwayland-both" else
                            "OWG physical test — Imported library changes on both monitors" if test=="imported-change-both" else
                            "OWG physical test — Paused monitor reconnect" if test=="topology-both" else
                            "OWG physical test — Scheduled movement on both monitors" if test=="scheduled-both" else
                            "OWG physical test — Target disappearance" if test=="target-loss" else
                            "OWG physical test — Abrupt controller loss" if test=="controller-loss" else
                            "OWG physical test — Quit during warning" if test=="quit-warning" else
                            "OWG physical test — Lock and unlock (paused)" if test=="lock-unlock" else
                            "OWG physical test — Cross-monitor cancellation" if test=="cross-monitor" else
                            "OWG physical test — Move and Restore" if test=="move-restore" else "OWG physical test — start here")
        layout=QVBoxLayout(self)
        instructions=QLabel("Use the empty test desktop. Normally put this panel on the monitor being tested.\n"
                            "Keep the four disposable windows there, two on each monitor.\n"
                            "Click/release once on each monitor, then click Start test.\n"
                            "For Mouse, move the mouse when the warning appears.\n"
                            "For Keyboard, press/release Shift without moving the mouse.\n"
                            "For Cross-monitor, put this panel on the OTHER monitor before Start.\n"
                            "Keep the mouse there; move it within that monitor when the target warning appears.\n"
                            "For Skip/Pause, stay idle; the selected action is sent during the warning.\n"
                            "For Move and Restore, stay idle through both countdowns: one window should move and return.\n"
                            "Every round ends paused. Cancellation rounds auto-Skip an unhandled warning.")
        instructions.setWordWrap(True)
        if test=="rpm-smoke":
            self.setWindowTitle("OWG installed RPM — consolidated check")
            instructions.setText("Use empty test desktops with TWO GTK4 disposable windows on EACH monitor, visible and uncovered.\n"
                                 "Put this panel and the OWG dashboard on HDMI first. Click/release on both desktops, then Start.\n"
                                 "1. Stay idle: one LG window moves after warning and returns after Restore warning.\n"
                                 "2. When prompted, move this panel AND dashboard to LG, click/release on both desktops, then Continue. Stay idle while HDMI moves and restores.\n"
                                 "3. The NEXT HDMI warning tests cancellation: MOVE THE MOUSE WITHIN LG as soon as instructed. All windows should stay put.\n"
                                 "4. Then stay idle: one final HDMI warning disappears when OWG quits automatically and reopens paused. No window should move.\n"
                                 "Warnings are 10 seconds. Allow about 2–3 minutes; the check stops at 4 minutes. Wait for the final result.")
        elif test in ("stale-restore","stale-resize"):
            instructions.setText("Use the empty test desktop, one target fully inside each left/right half of the selected monitor.\n"
                                 "Keep targets uncovered and put this panel on the OTHER monitor. Click/release on both outputs, then Start.\n"
                                 "Stay idle through the FIRST warning until both targets move.\n"
                                 "When prompted, drag ONE target to a visibly different position, then click 'I moved one window — check Restore'.\n"
                                 "Stay idle. Restore should be refused WITHOUT a second warning or any further movement.\n"
                                 "The windows remain at the post-move/manual positions. This round ends paused.")
        elif test in ("imported-group-restore","imported-rotation-restore"):
            instructions.setText("Use the empty test desktop. This uses your existing imported Minis group without editing it.\n"
                                 "Resize targets to about 600 × 300 on LG, or 420 × 180 on HDMI.\n"
                                 "Put one target fully inside the TOP-LEFT third by third, and the other inside the TOP-RIGHT third by third of the selected monitor.\n"
                                 "Keep windows uncovered and away from zone boundaries; put this panel on the OTHER monitor.\n"
                                 "Click/release on both monitors, then Start and stay idle through BOTH warnings.\n"
                                 "Both targets should drift inside their zones, then return after Restore. Other-monitor windows should stay unchanged.")
            if test=="imported-rotation-restore":
                instructions.setText(instructions.text().replace("drift inside their zones", "move to different corner zones"))
        elif test in ("group-restore","group-rotation-restore"):
            instructions.setText("Use the empty test desktop. Put one disposable target window fully inside each left/right half of the selected monitor.\n"
                                 "Keep both visible, uncovered, non-overlapping and away from screen edges.\n"
                                 "Put this panel on the OTHER monitor. Click/release on both monitors, then Start.\n"
                                 "Stay idle through BOTH warnings: both target windows should move, then return after Restore.\n"
                                 "The other monitor's disposable windows should stay in place. This round ends paused.")
            if test=="group-rotation-restore":
                instructions.setText(instructions.text().replace("both target windows should move, then return after Restore.",
                                     "both target windows should move to the opposite half, then return after Restore."))
        elif test=="lock-warning":
            instructions.setText("Use the empty test desktop with the disposable windows visible and uncovered.\n"
                                 "Click/release once on both monitors, then Start and stay idle.\n"
                                 "The test AUTOMATICALLY LOCKS THE DESKTOP about 3 seconds into the warning.\n"
                                 "Leave the lock screen visible for about 5 seconds, then unlock normally.\n"
                                 "Click/release once on each monitor again. Do not Resume.\n"
                                 "No disposable window should move. Wait for the panel result before returning to the CLI.")
        elif test=="lock-unlock":
            instructions.setText("Paused lock/unlock check. Keep Window Guard paused and leave the disposable windows open.\n"
                                 "Click/release on each monitor, then click Start test.\n"
                                 "Wait for the panel to say to lock, then lock normally (Meta+L).\n"
                                 "Leave the lock screen visible for about 5 seconds and unlock normally.\n"
                                 "Click/release once on each monitor again. Do not Resume.\n"
                                 "No movement warning or window movement is expected. Return to the CLI when finished.")
        elif test=="topology-warning-both":
            instructions.setText("Cable removal during a warning: selected monitor first, then the other monitor. Put this panel on the other monitor.\n"
                                 "Keep two disposable windows visible on each empty test desktop. Have ONLY the selected display cable ready to unplug.\n"
                                 "Click/release on both monitors, Start, then keep mouse/keyboard still.\n"
                                 "As soon as the selected monitor's warning appears, unplug that display cable. The warning must cancel.\n"
                                 "Leave it disconnected until the panel says to reconnect (about 12 seconds).\n"
                                 "Reconnect, let displays settle, then click/release on EACH desktop. Do not Resume.\n"
                                 "At the setup checkpoint, move this panel to the recovered monitor and restore two uncovered fixtures per monitor, then Continue.\n"
                                 "Repeat for the other monitor when its warning appears. KDE may relocate windows during disconnection; OWG must not complete its planned move.\n"
                                 "Missed disconnects are skipped before warning expiry. Wait for the final paused result.")
        elif test=="stability-paused":
            instructions.setText("Finish the paused readiness observation only: about 75 seconds.\n"
                                 "Keep the four GTK windows visible on the empty test desktops, two per monitor.\n"
                                 "Click/release on both desktops, then Start and stay idle until the final result.\n"
                                 "No warning or movement is expected. No Resume or MoveNow will be sent.\n"
                                 "The test waits briefly for fresh compositor facts after Pause, then observes 70 seconds of ready, paused operation.\n"
                                 "Original OWG settings are restored afterward. The run has a 90-second cutoff.\n"
                                 "Your six completed movements remain verified and are not repeated.")
        elif test=="stability":
            instructions.setText("Continuous scheduling on BOTH monitors: about 4¾ minutes, one Start.\n"
                                 "Keep two GTK disposable windows on each empty test desktop, separate and uncovered.\n"
                                 "Put this panel wherever it does not cover any target. Click/release on both desktops, then Start.\n"
                                 "Stay idle for the entire run. The first automatic warning appears after about one minute.\n"
                                 "There will be THREE scheduled moves per monitor, each after a 3-second test warning.\n"
                                 "Moves stay in place; this test does not Restore between cycles.\n"
                                 "After six moves, the app pauses and watches for 70 seconds to verify movement stops across the next deadlines.\n"
                                 "The run has a 285-second cutoff to leave time before five-minute idle suspension.\n"
                                 "Wait for the FINAL paused result before returning to the CLI. Original OWG settings are restored paused.\n"
                                 "Stop test and Pause cancels the run and restores its settings; report any unexpected movement or warning after Pause.")
        elif test in ("gtk-both","gtk4-both"):
            instructions.setText(("GTK4" if test=="gtk4-both" else "GTK3")+" Wayland Move and Restore: one Start checks LG, then HDMI.\n"
                                 "Put TWO GTK disposable windows on EACH empty test desktop, separate and uncovered.\n"
                                 "Start with this panel on HDMI, clear of the HDMI windows. Click/release on both desktops, then Start.\n"
                                 "Stay idle through two 10-second warnings: one LG window should move, then return after Restore.\n"
                                 "At the checkpoint move this panel to LG, click/release on both desktops, and Continue.\n"
                                 "One HDMI window should move and return through the same warnings. Other-monitor windows should stay put.\n"
                                 "Wait for the final paused result and report original-position return and unchanged sizes on both displays.")
        elif test=="shrink-both":
            instructions.setText("GTK4 two-pixel shrink and Restore: LG, then HDMI.\n"
                                 "Keep TWO GTK4 targets on EACH empty test desktop, separate and uncovered.\n"
                                 "Put this panel on HDMI; click/release both desktops, then Start.\n"
                                 "One LG window should move and shrink slightly after the 10-second warning, then return to its original position and size after Restore.\n"
                                 "At the checkpoint move the panel to LG, click/release both desktops and Continue for HDMI.\n"
                                 "The two-pixel resize is measured automatically; other windows should stay put.\n"
                                 "Each round takes about 35 seconds. Temporary layout/settings recover paused; the batch has a 285-second cutoff.")
        elif test=="resize-refusal-both":
            instructions.setText("Fixed-size shrink refusal on LG, then HDMI. Start with this panel on HDMI.\n"
                                 "Put one [fixed] and one [bounded] disposable window on each empty test desktop, visible and uncovered.\n"
                                 "Place them apart with some open space; exact zone alignment is unnecessary.\n"
                                 "Click/release on both desktops, Start, and stay idle. No warning or movement is expected.\n"
                                 "The test temporarily creates zones requiring a two-pixel shrink of the fixed window, checks refusal, and restores your layout settings.\n"
                                 "At the checkpoint move this panel to LG, click/release on both desktops, and Continue for HDMI.\n"
                                 "Wait for the final paused result and report whether all windows kept their position and size.")
        elif test=="reconfigure-both":
            instructions.setText("One Start checks KDE configuration reload during a warning on LG, then HDMI.\n"
                                 "Use the empty test desktops, two uncovered disposable windows on each monitor. Keep this panel clear of targets.\n"
                                 "Click/release once on both monitors, Start, then stay idle. Do not change display settings.\n"
                                 "About three seconds into the LG warning, KDE's existing configuration reloads automatically.\n"
                                 "The warning should disappear, no windows should move, and Window Guard should recover PAUSED.\n"
                                 "When prompted, click/release on both monitors and Continue for HDMI. Stay idle until the final result.\n"
                                 "Warnings are 10 seconds. Each check watches past the original warning expiry; allow about 40 seconds plus the input checkpoint.")
        elif test=="scaling-both":
            instructions.setText("While Window Guard is PAUSED, set LG scale to 125% and HDMI to 150% in Display Configuration and Apply.\n"
                                 "Rearrange the existing [fixed]/[bounded] targets: one fully within each left/right half on each monitor.\n"
                                 "Put this panel on HDMI. Click/release on both monitors, then Start and stay idle.\n"
                                 "Both LG targets should move and Restore without resizing. Then move the panel to LG and Continue for HDMI.\n"
                                 "When both rounds finish paused, follow the prompt to restore BOTH scales to 100% and the original display arrangement.\n"
                                 "No corner zones are needed. Warnings are 10 seconds. Wait for the final result after restoring display settings.")
        elif test=="constraints-both":
            instructions.setText("Two-window constraint test: one [fixed] and one [bounded] window per monitor.\n"
                                 "Put one fully in each left/right half, visible and uncovered, away from edges.\n"
                                 "Start with this panel on HDMI. Click/release on both monitors, then Start and stay idle.\n"
                                 "Both LG targets should move after warning, then return after Restore warning without resizing.\n"
                                 "When prompted, move this panel to LG and press the Continue button.\n"
                                 "Then both HDMI targets should move and return unchanged in size. Other-monitor windows should stay put.")
        elif test=="xwayland-both":
            instructions.setText("One Start runs XWayland movement and Restore on LG, then HDMI. Allow about one minute.\n"
                                 "Use the four new windows labelled (XWayland), two per monitor on the empty test desktops.\n"
                                 "Keep them visible and uncovered, away from edges, and keep this panel clear of all targets.\n"
                                 "No corner-zone placement is needed. Click/release on both monitors, Start and stay idle.\n"
                                 "One LG window should move after a 10-second warning, then return after Restore warning.\n"
                                 "Then the same runs on HDMI. Other-monitor windows should stay put. Wait for the final paused result.")
        elif test=="imported-change-both":
            instructions.setText("One Start runs four checks: change during warning, then before Restore, on LG and HDMI.\n"
                                 "Use small windows: about 600 × 300 on LG and 420 × 180 on HDMI.\n"
                                 "Put one target fully within each TOP-LEFT and TOP-RIGHT corner third on each display, with margins.\n"
                                 "Keep the panel clear of all targets. Click/release on both monitors, Start, then stay idle.\n"
                                 "Padding changes by one pixel temporarily and is restored automatically after each check. Do not edit layouts during the batch.\n"
                                 "First warning should end without movement. Next warning moves both targets; changed-library Restore should refuse WITHOUT another warning.\n"
                                 "Then HDMI repeats. Windows stay at their forward positions in the Restore-refusal checks. Wait for final paused result.")
        elif test=="topology-both":
            instructions.setText("This test stays PAUSED throughout. No movement warnings are expected.\n"
                                 "Click/release once on both monitors, then Start and follow the panel prompts.\n"
                                 "First put this panel on HDMI and unplug only the LG display cable.\n"
                                 "Wait for the reconnect prompt, reconnect LG, then click/release on both monitors.\n"
                                 "Then move this panel to LG and repeat for HDMI when prompted.\n"
                                 "KDE may rearrange windows during disconnection. Check normal desktop recovery and that Window Guard stays paused.")
        elif test=="scheduled-both":
            instructions.setText("One Start runs LG, then HDMI, automatically. Allow about THREE minutes.\n"
                                 "Use the empty test desktops with two visible, uncovered disposable windows on each monitor.\n"
                                 "Keep this panel clear of targets. Click/release on both monitors, then Start and stay idle.\n"
                                 "After about one minute, one LG window should move after its warning, then return after Restore warning.\n"
                                 "Then the same sequence runs on HDMI. Other-monitor windows should stay put during each round.\n"
                                 "Wait until the panel reports both rounds finished and paused before returning to the CLI.")
        elif test=="target-loss":
            instructions.setText("Use only the four new disposable windows on the empty test desktop.\n"
                                 "Put two on each monitor, visible and uncovered. Put this panel on the selected monitor.\n"
                                 "Click/release on both monitors, then Start and stay idle.\n"
                                 "About 3 seconds into the warning, BOTH selected-monitor disposable windows close themselves.\n"
                                 "The move must be cancelled/refused without a Restore becoming available.\n"
                                 "Other-monitor windows and the dashboard must remain open and unmoved. Wait for the paused result.")
        elif test=="controller-loss":
            instructions.setText("Use the empty test desktop with four visible, uncovered disposable windows.\n"
                                 "Put this panel on the selected monitor. Click/release on both monitors, then Start.\n"
                                 "Stay idle. The test abruptly stops ONLY Window Guard about 3 seconds into the warning.\n"
                                 "The warning should disappear promptly and NO windows should move.\n"
                                 "Wait about 20 seconds more: the harness then unloads the orphan adapter and reopens paused.\n"
                                 "This panel and all disposable windows must stay open. Report any movement or lingering warning.")
        elif test=="quit-warning":
            instructions.setText("Use the empty test desktop with the four disposable windows visible and uncovered.\n"
                                 "Put this panel on the selected monitor. Click/release on each monitor, then Start.\n"
                                 "Stay idle. The warning should appear, then disappear when the test sends Quit after about 3 seconds.\n"
                                 "The dashboard closes and automatically reopens paused. This test panel stays open.\n"
                                 "No disposable window should move. Wait for the final result before returning to the CLI.")
        if test=="stale-resize":
            instructions.setText(instructions.text().replace("drag ONE target to a visibly different position", "resize ONE target to a visibly different size").replace("I moved one window", "I resized one window"))
            self.setWindowTitle("OWG physical test — Restore after manual resize")
        layout.addWidget(instructions)
        self.output=QComboBox()
        self.output.addItem("LG (DP-1)","DP-1")
        self.output.addItem("Cintiq (HDMI-A-1)","HDMI-A-1")
        self.output.setCurrentIndex(self.output.findData(output))
        layout.addWidget(self.output)
        self.input_kind=QComboBox()
        self.input_kind.addItem("Paused readiness only — no movements (about 75 seconds)","stability-paused")
        self.input_kind.addItem("Continuous scheduling — 3 moves per monitor (under 5 minutes)","stability")
        self.input_kind.addItem("GTK3 Wayland Move and Restore — LG then HDMI","gtk-both")
        self.input_kind.addItem("GTK4 Wayland Move and Restore — LG then HDMI","gtk4-both")
        self.input_kind.addItem("GTK4 two-pixel shrink and Restore — LG then HDMI","shrink-both")
        self.input_kind.addItem("Fixed-size resize refusal — LG then HDMI","resize-refusal-both")
        self.input_kind.addItem("Installed RPM consolidated check","rpm-smoke")
        self.input_kind.addItem("Mouse cancellation","mouse")
        self.input_kind.addItem("Keyboard cancellation (Shift)","keyboard")
        self.input_kind.addItem("Cross-monitor mouse cancellation (panel on OTHER monitor)","cross-monitor")
        self.input_kind.addItem("Skip during warning (stay idle)","skip")
        self.input_kind.addItem("Pause during warning (stay idle)","pause")
        self.input_kind.addItem("Move and Restore (stay idle through both warnings)","move-restore")
        self.input_kind.addItem("Lock and unlock recovery (stays paused)","lock-unlock")
        self.input_kind.addItem("Quit during warning (stay idle; reopens paused)","quit-warning")
        self.input_kind.addItem("Fractional scaling — LG 125%, HDMI 150%, then restore","scaling-both")
        self.input_kind.addItem("KDE reload during warning — LG then HDMI","reconfigure-both")
        self.input_kind.addItem("Cable disconnect during warning — selected monitor first","topology-warning-both")
        self.input_kind.addItem("Fixed/bounded window sizes — two targets per monitor","constraints-both")
        self.input_kind.addItem("XWayland Move and Restore — LG then HDMI","xwayland-both")
        self.input_kind.addItem("Imported layout changes — four checks on both monitors","imported-change-both")
        self.input_kind.addItem("Paused monitor disconnect/reconnect — LG then HDMI","topology-both")
        self.input_kind.addItem("Scheduled movement and Restore — LG then HDMI (about 3 minutes)","scheduled-both")
        self.input_kind.addItem("Target disappearance during warning (closes selected fixtures)","target-loss")
        self.input_kind.addItem("Abrupt controller loss during warning (stay idle; harness recovers)","controller-loss")
        self.input_kind.addItem("Lock during warning (automatically locks desktop)","lock-warning")
        self.input_kind.addItem("Two-window group Move and Restore (stay idle)","group-restore")
        self.input_kind.addItem("Group Rotation and Restore (stay idle)","group-rotation-restore")
        self.input_kind.addItem("Imported Minis Move and Restore (stay idle)","imported-group-restore")
        self.input_kind.addItem("Imported Minis Rotation and Restore (stay idle)","imported-rotation-restore")
        self.input_kind.addItem("Restore refusal after manually moving one window","stale-restore")
        self.input_kind.addItem("Restore refusal after manually resizing one window","stale-resize")
        self.input_kind.setCurrentIndex(self.input_kind.findData(test))
        layout.addWidget(self.input_kind)
        self.label=QLabel("Waiting for you. No test has started.")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.start=QPushButton("Start test")
        self.start.clicked.connect(self.begin)
        layout.addWidget(self.start)
        self.confirm_move=QPushButton("I moved one window — check Restore")
        self.confirm_move.clicked.connect(self.confirm_manual_move)
        self.confirm_move.setVisible(False)
        layout.addWidget(self.confirm_move)
        self.stop=QPushButton("Stop test and Pause")
        self.stop.clicked.connect(self.abort)
        self.stop.setEnabled(False)
        layout.addWidget(self.stop)
        self.process=QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.ForwardedChannels)
        self.process.finished.connect(self.finished)
        self.process.errorOccurred.connect(self.failed)
        self.timer=QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self.progress)
        self.resize(530,260)
        self.output.currentIndexChanged.connect(self.selection_changed)
        self.input_kind.currentIndexChanged.connect(self.selection_changed)
        self.selection_changed()

    def selection_changed(self,*unused):
        if self.input_kind.currentData()=="stability-paused":
            self.label.setText("Paused-only finish: click/release on both desktops, Start, and stay idle for about 75 seconds. No warning or movement expected.")
        elif self.input_kind.currentData()=="stability":
            self.label.setText("Both monitors selected. Keep all GTK windows uncovered, click/release on both desktops, Start, then stay idle until the FINAL paused result (about 4¾ minutes; 285-second cutoff).")
        elif self.input_kind.currentData() in ("gtk-both","gtk4-both"):
            self.label.setText("LG first, then HDMI (output selector unused). Two GTK windows per monitor; panel on HDMI. Click/release on both desktops, Start, then stay idle through Move and Restore warnings.")
        elif self.input_kind.currentData()=="shrink-both":
            self.label.setText("LG then HDMI. Keep two GTK4 windows per monitor uncovered; panel on HDMI. Click/release both desktops, then Start. One window moves and shrinks two pixels, then restores.")
        elif self.input_kind.currentData()=="resize-refusal-both":
            self.label.setText("LG first, then HDMI (output selector unused). Put this panel on HDMI, keep fixed/bounded targets uncovered, click/release on both desktops, then Start. No warning or movement expected.")
        elif self.input_kind.currentData() in ("stale-restore","stale-resize"):
            self.label.setText("Waiting for Start. Put this panel on the other monitor. After the forward commit, "+
                               ("resize one target" if self.input_kind.currentData()=="stale-resize" else "drag one target")+
                               ", then use the confirmation button. Restore should refuse without moving windows.")
        elif self.input_kind.currentData() in ("imported-group-restore","imported-rotation-restore"):
            other="HDMI / Cintiq" if self.output.currentData()=="DP-1" else "LG"
            self.label.setText("Before Start: place targets fully inside the upper-left and upper-right corner thirds. "
                               "Use small floating windows: about 600 × 300 on LG or 420 × 180 on HDMI. "
                               "Put this panel on "+other+". Stay idle through both warnings; both targets should "+
                               ("move to different corner zones and return." if self.input_kind.currentData()=="imported-rotation-restore" else "drift and return."))
        elif self.input_kind.currentData() in ("group-restore","group-rotation-restore"):
            other="HDMI / Cintiq" if self.output.currentData()=="DP-1" else "LG"
            self.label.setText("Before Start: one target window in each left/right half of the selected monitor. "
                               "Put this panel on "+other+". Stay idle through both warnings; both targets should "+
                               ("move to the opposite half and return." if self.input_kind.currentData()=="group-rotation-restore" else "move and return."))
        elif self.input_kind.currentData()=="lock-warning":
            self.label.setText("Waiting for Start. This round automatically locks your desktop during the warning. "
                               "Stay idle until locked; unlock normally, then click/release on both monitors. Do not Resume.")
        elif self.input_kind.currentData()=="target-loss":
            self.label.setText("Waiting for Start. Both selected-monitor fixtures will close during the warning. "
                               "Stay idle; other-monitor windows must stay open and unmoved.")
        elif self.input_kind.currentData()=="controller-loss":
            self.label.setText("Waiting for Start. Stay idle through controller loss and the full countdown dwell. "
                               "No windows should move. Dashboard reopens paused after harness recovery.")
        elif self.input_kind.currentData()=="quit-warning":
            self.label.setText("Waiting for Start. Stay idle through the warning; Quit and paused reopen are automatic. No window should move.")
        elif self.input_kind.currentData()=="lock-unlock":
            self.label.setText("Click/release once on each monitor, then Start. When prompted, lock the desktop normally "
                               "(Meta+L), unlock normally, then click/release on both monitors again. "
                               "Do not Resume. No warning or movement is expected. Output selection is unused.")
        elif self.input_kind.currentData()=="cross-monitor":
            other="HDMI / Cintiq" if self.output.currentData()=="DP-1" else "LG"
            self.label.setText("Before Start: put this panel and mouse on "+other+
                               ". When the warning appears on the selected target, move the mouse only within "+other+".")
        elif self.input_kind.currentData()=="topology-warning-both":
            selected=self.output.currentData()
            other="HDMI-A-1" if selected=="DP-1" else "DP-1"
            self.label.setText("First target: "+selected+". Put this panel on "+other+
                               ". Click/release on both desktops, Start, and unplug only "+selected+" when its warning appears.")

    def begin(self):
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        output=self.output.currentData()
        self.round_input_kind=self.input_kind.currentData()
        self.receipt=self.receipt_dir/(output.lower()+"-"+self.round_input_kind+"-"+stamp+".jsonl")
        self.confirm_move.setVisible(False)
        self.confirm_move.setEnabled(False)
        self.output.setEnabled(False)
        self.input_kind.setEnabled(False)
        self.start.setEnabled(False)
        self.stop.setEnabled(True)
        self.label.setText("Starting paused lock recovery check." if self.round_input_kind=="lock-unlock" else
                           "Starting in 5 seconds. Leave the mouse still.")
        self.process.setProgram(sys.executable)
        case="cancellation" if self.round_input_kind in ("mouse","keyboard") else self.round_input_kind
        if case=="rpm-smoke":
            self.process.setArguments([str(ROOT/"tools/desktop_rpm_smoke.py"),"--receipt",str(self.receipt)])
        elif case=="stability-paused":
            self.process.setArguments([str(ROOT/"tools/desktop_stability.py"),"--receipt",str(self.receipt),"--paused-only"])
        elif case=="stability":
            self.process.setArguments([str(ROOT/"tools/desktop_stability.py"),"--receipt",str(self.receipt)])
        elif case in ("gtk-both","gtk4-both"):
            self.process.setArguments([str(ROOT/"tools/desktop_gtk_batch.py"),"--receipt",str(self.receipt),"--gtk-version","4" if case=="gtk4-both" else "3"])
        elif case=="shrink-both":
            self.process.setArguments([str(ROOT/"tools/desktop_shrink_batch.py"),"--receipt",str(self.receipt)])
        elif case=="resize-refusal-both":
            self.process.setArguments([str(ROOT/"tools/desktop_resize_refusal.py"),"--receipt",str(self.receipt)])
        elif case=="topology-warning-both":
            self.process.setArguments([str(ROOT/"tools/desktop_topology_warning_batch.py"),"--receipt",str(self.receipt),"--output",output])
        elif case=="reconfigure-both":
            self.process.setArguments([str(ROOT/"tools/desktop_reconfigure_batch.py"),"--receipt",str(self.receipt)])
        elif case=="scaling-both":
            self.process.setArguments([str(ROOT/"tools/desktop_scaling_batch.py"),"--receipt",str(self.receipt)])
        elif case=="constraints-both":
            self.process.setArguments([str(ROOT/"tools/desktop_constraints_batch.py"),"--receipt",str(self.receipt)])
        elif case=="xwayland-both":
            self.process.setArguments([str(ROOT/"tools/desktop_xwayland_batch.py"),"--receipt",str(self.receipt)])
        elif case=="imported-change-both":
            self.process.setArguments([str(ROOT/"tools/desktop_library_batch.py"),"--receipt",str(self.receipt)])
        elif case=="topology-both":
            self.process.setArguments([str(ROOT/"tools/desktop_topology_round.py"),"--receipt",str(self.receipt)])
        elif case=="scheduled-both":
            self.process.setArguments([str(ROOT/"tools/desktop_scheduled_batch.py"),"--receipt",str(self.receipt)])
        elif case=="lock-unlock":
            self.process.setArguments([str(ROOT/"tools/desktop_lock_round.py"),"--receipt",str(self.receipt)])
        else:self.process.setArguments([str(ROOT/"tools/desktop_cancellation_round.py"),
                                   "--receipt",str(self.receipt),"--delay","5",
                                   "--output",output,"--case",case,
                                   "--input-kind",self.round_input_kind if case=="cancellation" else "mouse"])
        self.process.start()
        self.timer.start()

    def rows(self):
        if not self.receipt or not self.receipt.exists():return []
        rows=[]
        for line in self.receipt.read_text().splitlines():
            try:rows.append(json.loads(line))
            except ValueError:pass
        return rows

    def progress(self):
        rows=self.rows()
        if not rows:return
        last=rows[-1]
        kind,value=last["kind"],last["value"]
        if kind=="operator-input-recovery":
            self.label.setText(value)
            self.confirm_move.setText("Input refreshed — continue")
            self.confirm_move.setVisible(True)
            self.confirm_move.setEnabled(not self.receipt.with_suffix(".continue").exists())
        elif kind=="rpm-stage":
            self.label.setText(value["output"]+": "+value["case"]+". Stay idle until the warning instruction.")
        elif kind=="rpm-stage-verified":
            self.label.setText(value["output"]+": "+value["case"]+" verified. Next stage starting.")
        elif kind=="operator-restore-scales":
            self.label.setText(value)
        elif kind=="operator-switch-panel":
            self.label.setText(value)
            self.confirm_move.setText("Panel moved — continue")
            self.confirm_move.setVisible(True)
            self.confirm_move.setEnabled(not self.receipt.with_suffix(".continue").exists())
        elif kind=="library-stage":
            self.label.setText(value["output"]+": "+("library change during warning" if value["case"]=="imported-change-warning" else "move then changed-library Restore refusal")+". Stay idle.")
        elif kind=="topology-instructions":
            self.label.setText(value)
        elif kind=="stability-wait":
            self.label.setText("Normal timers running. Completed: LG "+str(value['completed']['DP-1'])+"/3, HDMI "+str(value['completed']['HDMI-A-1'])+"/3. Stay idle until the FINAL result.")
        elif kind in ("stability-cycle-verified","scheduled-cycle-committed"):
            self.label.setText(value['output']+": scheduled move "+str(value['cycle'])+"/3 verified. Stay idle; more timer checks remain.")
        elif kind in ("stability-paused-dwell","stability-paused-wait"):
            remaining=value.get('remainingSeconds',value.get('seconds'))
            self.label.setText("PAUSED verification: "+str(remaining)+" seconds remaining. Stay idle; no further warnings or movement should occur. Wait for FINAL result.")
        elif kind in ("resize-counterfactual","resize-refusal-preview","resize-refusal-result"):
            self.label.setText("Checking fixed-size shrink refusal. Stay idle; no warning or movement expected.")
        elif kind=="shrink-forward-verified":
            self.label.setText(value["output"]+": two-pixel client shrink verified. Stay idle for Restore warning.")
        elif kind=="shrink-restore-verified":
            self.label.setText(value["output"]+": original frames and client sizes restored.")
        elif kind=="batch-output":
            self.label.setText("Starting round on "+value+". Stay idle until BOTH rounds finish.")
        elif kind=="waiting-for-schedule":
            self.label.setText("Waiting about one minute for the normal timer on "+value["output"]+". Stay idle.")
        elif kind=="operator-move-window":
            self.label.setText(value)
            self.confirm_move.setText("I resized one window — check Restore" if self.round_input_kind=="stale-resize" else "I moved one window — check Restore")
            self.confirm_move.setVisible(True)
            self.confirm_move.setEnabled(not self.receipt.with_suffix(".continue").exists())
        elif kind=="operator-frame-change-confirmed":
            self.label.setText("Stay idle. Waiting for quiet, then checking stale Restore refusal.")
        elif self.round_input_kind in ("lock-unlock","lock-warning") and kind in ("lock-instructions","recovery-instructions"):
            self.label.setText(value)
        elif kind in ("lock-request","lock-request-acknowledged","lock-status","warning-lock-cancelled","warning-unlocked","lock-recovery-verified"):
            self.label.setText("Lock cancellation check. After about 5 seconds locked, unlock normally, "
                               "then click/release on both monitors. Do not Resume.")
        elif self.round_input_kind=="lock-unlock" and kind in ("status","native-locked","unlock-observed"):
            self.label.setText("Lock recovery: "+(", ".join(value.get("suspendedReasons",[])) or
                               "Safety ready; remains paused.")+"\nAfter unlocking, click/release on both monitors. Do not Resume.")
        elif kind=="waiting-for-operator":
            self.label.setText("Starting shortly. Keep the mouse still until the warning appears.")
        elif kind=="waiting-for-safety":
            self.label.setText("Waiting for safety: "+", ".join(value.get("suspendedReasons",[])))
        elif kind=="status":
            if value.get("skipAvailable"):
                if self.round_input_kind=="rpm-smoke":
                    action="MOVE THE MOUSE WITHIN LG NOW; KEEP IT OFF HDMI." if value.get("rpmCase")=="cross-monitor" else "STAY IDLE; DO NOT USE MOUSE OR KEYBOARD."
                elif self.round_input_kind=="mouse":action="MOVE THE MOUSE NOW."
                elif self.round_input_kind=="keyboard":action="PRESS AND RELEASE SHIFT NOW; KEEP THE MOUSE STILL."
                elif self.round_input_kind=="cross-monitor":
                    other="HDMI / Cintiq" if self.output.currentData()=="DP-1" else "LG"
                    action="MOVE THE MOUSE WITHIN "+other+" NOW; KEEP IT OFF THE TARGET MONITOR."
                else:action="STAY IDLE; DO NOT USE MOUSE OR KEYBOARD."
                self.label.setText(f"{value.get('testPhase','movement').capitalize()} warning ({value['warningRemainingSeconds']:.0f}s). "+action)
            else:self.label.setText("Planning / waiting for quiet. Keep the mouse still.")
        elif kind=="view-moved-window":
            self.label.setText("Movement committed. Stay idle; Restore will start in 4 seconds.")
        elif kind in ("target-close-request","target-close-ack"):
            self.label.setText("Selected disposable targets closed. Stay idle until the paused result; other-monitor windows should stay put.")
        elif kind in ("controller-crash","controller-lost","controller-loss-dwell","controller-loss-dwell-complete","orphan-cleanup"):
            self.label.setText("Controller-loss check: stay idle until paused reopen. The warning should disappear promptly; no windows should move.")
        elif kind in ("Quit","quit-lifecycle","quit-verified","reopening","reopened-paused"):
            self.label.setText("Checking Quit and paused reopen. Stay idle; disposable windows should remain in place.")

    def abort(self):
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            if self.round_input_kind in ("rpm-smoke","imported-change-both","resize-refusal-both","gtk-both","gtk4-both","shrink-both","stability","stability-paused"):self.process.terminate()
            else:self.process.kill()
        self.pause()

    def confirm_manual_move(self):
        if not self.receipt or not self.confirm_move.isEnabled():return
        try:self.receipt.with_suffix(".continue").write_text("operator confirmed manual frame change\n")
        except OSError:
            self.abort()
            return
        self.confirm_move.setEnabled(False)
        self.label.setText("Stay idle: HDMI Move/Restore, then mouse cancellation when prompted, then automatic Quit/reopen." if self.round_input_kind=="rpm-smoke" else "Stay idle. Starting HDMI GTK Move and Restore." if self.round_input_kind in ("gtk-both","gtk4-both","shrink-both") else "Stay idle. Starting HDMI fixed-size refusal check; no warning or movement expected." if self.round_input_kind=="resize-refusal-both" else "Second monitor check starting. Keep mouse/keyboard still; unplug the monitor named by the panel when its warning appears." if self.round_input_kind=="topology-warning-both" else "Stay idle. Starting HDMI reload check." if self.round_input_kind=="reconfigure-both" else "Stay idle. Starting HDMI constraint round." if self.round_input_kind in ("constraints-both","scaling-both") else "Stay idle. Checking Restore after your manual movement.")

    def pause(self):
        response=call_host("Pause")
        return bool(response and response.get("ok"))

    def finished(self,*unused):
        self.timer.stop()
        self.confirm_move.setVisible(False)
        self.confirm_move.setEnabled(False)
        paused=self.pause()
        rows=self.rows()
        result=next((r["value"] for r in reversed(rows) if r["kind"] in ("round-result","error","operator-warning-missed")),"No result recorded")
        self.label.setText(("Paused. " if paused else "Pause unconfirmed—use the dashboard Pause button. ")+str(result)+
                           ("\nReport whether the paused-only observation finished with no warnings or window movement."
                            if self.round_input_kind=="stability-paused" else
                            "\nReport LG and HDMI move/restore, mouse cancellation with unchanged windows, and automatic paused quit/reopen."
                            if self.round_input_kind=="rpm-smoke" else
                            "\nReport whether THREE moves occurred on EACH monitor, sizes stayed unchanged, and no warning or movement occurred during the final paused check. Windows keep their final positions."
                            if self.round_input_kind=="stability" else
                            "\nReport whether one GTK window moved and returned on each monitor, with unchanged sizes and other-monitor windows staying put."
                            if self.round_input_kind in ("gtk-both","gtk4-both") else
                            "\nReport whether one window moved and restored its position and size on each monitor, with all other windows staying put."
                            if self.round_input_kind=="shrink-both" else
                            "\nReport whether no warnings appeared and all fixtures kept their position and size on BOTH monitors."
                            if self.round_input_kind=="resize-refusal-both" else
                            "\nReport whether BOTH targets on each monitor moved and returned without resizing, with other-monitor targets unchanged."
                            if self.round_input_kind in ("constraints-both","scaling-both") else
                            "\nReport whether each warning-change prevented movement, and each Restore-change refused without a second warning after both targets moved."
                            if self.round_input_kind=="imported-change-both" else
                            "\nReport whether both displays disconnected and recovered normally, with Window Guard paused and no warnings."
                            if self.round_input_kind=="topology-both" else
                            "\nReport whether one window on EACH monitor moved and returned, with other-monitor windows unchanged during each round."
                            if self.round_input_kind in ("scheduled-both","xwayland-both") else
                            "\nReport whether both selected fixtures closed, warning dismissed, and other-monitor windows stayed open and unmoved."
                            if self.round_input_kind=="target-loss" else
                            "\nReport whether Restore refused without a second warning and both windows kept their post-move/manual positions and sizes."
                            if self.round_input_kind in ("stale-restore","stale-resize") else
                            "\nReturn to the CLI and report whether lock/unlock completed normally and windows stayed in place."
                            if self.round_input_kind in ("lock-unlock","lock-warning") else
                            "\nReturn to the CLI and report warning dismissal, dashboard reopening and whether windows stayed in place."
                            if self.round_input_kind in ("quit-warning","controller-loss") else
                            "\nReport whether BOTH target windows moved and returned, and other-monitor windows stayed in place."
                            if self.round_input_kind in ("group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore") else
                            "\nReturn to the CLI and report whether you saw the warning."))
        self.start.setEnabled(paused)
        self.output.setEnabled(True)
        self.input_kind.setEnabled(True)
        self.stop.setEnabled(False)

    def failed(self,error):
        if error==QProcess.ProcessError.FailedToStart:self.finished()

    def closeEvent(self,event):
        if self.process.state()!=QProcess.ProcessState.NotRunning:
            self.abort()
            event.ignore()
        else:event.accept()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-dir",type=Path,default=Path("/tmp/owg-desktop-physical-rounds"))
    parser.add_argument("--output",choices=("DP-1","HDMI-A-1"),default="DP-1")
    parser.add_argument("--test",choices=("rpm-smoke","stability-paused","stability","gtk-both","gtk4-both","shrink-both","resize-refusal-both","topology-warning-both","reconfigure-both","mouse","keyboard","cross-monitor","skip","pause","move-restore","quit-warning","controller-loss","target-loss","lock-warning","lock-unlock","group-restore","group-rotation-restore","imported-group-restore","imported-rotation-restore","stale-restore","stale-resize","scheduled-both","topology-both","imported-change-both","xwayland-both","constraints-both","scaling-both"),default="mouse")
    args=parser.parse_args()
    app=QApplication([])
    # The production policy excludes this identity from movement candidates.
    QGuiApplication.setDesktopFileName("oled-window-guard")
    control=Control(args.receipt_dir,args.output,args.test)
    control.show()
    return app.exec()


if __name__=="__main__":raise SystemExit(main())

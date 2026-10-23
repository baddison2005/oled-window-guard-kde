"""Qt dashboard and tray shell for the single external KWin controller.

This module owns presentation and validated settings only. It never creates a
movement/dimming controller; actions are sent through the injected intent
client and unknown capability state keeps guarded actions disabled.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import configparser
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
from typing import Any, Callable, Mapping, Protocol

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu, QMessageBox,
    QPushButton, QSpinBox, QSystemTrayIcon, QTabWidget, QVBoxLayout, QWidget,
    QScrollArea, QGroupBox, QLineEdit, QGridLayout, QSizePolicy, QRadioButton, QButtonGroup,
)

from . import preferences
from .layout_provider import LayoutProvider
from .updates import package_update_status, check_for_updates, download_update, read_upgrade_result
from .warning_audio import WarningAudio
from .movement_preview import MovementPreview
from .autostart import LoginStartup
from .application_identity import exclusion_aliases
from .slider_control import SliderControl, CompactDoubleSpinBox
from .dock_menu import CairoDockMenu


def application_icon() -> QIcon:
    icon_path = Path(__file__).resolve().parents[1] / "assets" / "icons" / "oled-window-guard.png"
    return QIcon(str(icon_path)) if icon_path.is_file() else QIcon.fromTheme("preferences-desktop-display")


class MovementStyleSelector(QWidget):
    """Native radio cards with the same value API as the other enum controls."""
    currentIndexChanged = Signal(int)
    choices = (("drift", "Shift position", "Move windows into free space within your maximum range."),
               ("swap", "Swap positions", "Rearrange windows together while keeping their sizes."),
               ("group", "Window Layouts groups", "Shift inside saved zones or rotate between them."))

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.group = QButtonGroup(self); layout = QVBoxLayout(self); layout.setContentsMargins(0,0,0,0)
        for index, (key, title, description) in enumerate(self.choices):
            card = QGroupBox(); card.setStyleSheet("QGroupBox { margin-top: 0; padding: 4px; border-radius: 8px; }"); body = QVBoxLayout(card); body.setContentsMargins(12,8,12,8)
            button = QRadioButton(title); self.group.addButton(button, index); body.addWidget(button)
            label = QLabel(description); label.setWordWrap(True); body.addWidget(label); layout.addWidget(card)
        self.setCurrentIndex(self.findData(value))
        self.group.idClicked.connect(self.currentIndexChanged.emit)

    def findData(self, value):
        return next((i for i, choice in enumerate(self.choices) if choice[0] == value), -1)

    def currentData(self):
        index = self.group.checkedId()
        return self.choices[index][0] if index >= 0 else None

    def setCurrentIndex(self, index):
        if index >= 0 and index != self.group.checkedId():
            self.group.button(index).setChecked(True)
            self.currentIndexChanged.emit(index)


class IntentClient(Protocol):
    """Narrow transport shared by dashboard, tray and a future CLI."""
    def submit(self, action: str, payload: Mapping[str, Any] | None = None) -> Any: ...


class ControllerIntentClient:
    """Callback adapter; concrete DBus/IPC transport is supplied by the host."""
    def __init__(self, submitter: Callable[[str, Mapping[str, Any]], Any]):
        self._submitter = submitter

    def submit(self, action: str, payload: Mapping[str, Any] | None = None) -> Any:
        if action not in {"PlanPreview", "Open", "Pause", "Resume", "ResumeMovement", "PauseMovement", "ResumeBrightness", "PauseBrightness", "MoveNow", "Skip", "Restore", "RestoreBrightness", "DimDisplayNow", "DimAllDisplaysNow", "Quit", "SettingsChanged"}:
            raise ValueError(f"unsupported controller intent: {action}")
        return self._submitter(action, dict(payload or {}))


def transaction_summary(status):
    result = str(status.get('lastResult', 'none'))
    count = status.get('committedWindows')
    if result == 'committed':
        return f"Committed: {count} window{'s' if count != 1 else ''} moved" if type(count) is int else 'Committed'
    if result.lower() == 'none':
        return 'None'
    text = result[:1].upper()+result[1:] + (f" · {count} window{'s' if count != 1 else ''} moved" if type(count) is int and count > 0 else '')
    uncertain = status.get('unverifiedWindows')
    if type(uncertain) is int and uncertain > 0:
        text += f" · {uncertain} window{'s' if uncertain != 1 else ''} changed; unverified"
    return text


def linux_application_ids() -> list[tuple[str, str]]:
    """Return visible desktop-file identities, without launching or guessing apps."""
    data_home = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))).expanduser()
    if not data_home.is_absolute(): data_home = Path.home() / ".local/share"
    roots = [data_home / "applications"]
    for entry in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        root = Path(entry)
        if root.is_absolute() and root / "applications" not in roots:
            roots.append(root / "applications")
    found: dict[str, str] = {}
    for root in roots:
        try:
            files = root.rglob("*.desktop")
            for path in files:
                parser = configparser.ConfigParser(interpolation=None, strict=False)
                try:
                    parser.read(path, encoding="utf-8")
                    entry = parser["Desktop Entry"]
                    if entry.get("NoDisplay", "false").lower() == "true" or entry.get("Hidden", "false").lower() == "true":
                        continue
                    app_id = str(path.relative_to(root).with_suffix("")).replace("/", "-")
                    name = entry.get("Name", app_id)
                    if app_id and name:
                        found.setdefault(app_id, name)
                except (OSError, KeyError, configparser.Error):
                    continue
        except OSError:
            continue
    return sorted(((app_id, name) for app_id, name in found.items()), key=lambda row: (row[1].casefold(), row[0]))


class Dashboard(QMainWindow):
    statusChanged = Signal(dict)
    previewDisplayChanged = Signal(str)

    def __init__(self, intent_client: IntentClient, *, settings_path: str | Path | None = None,
                 layout_provider: LayoutProvider | None = None, app_ids: list[tuple[str, str]] | None = None,
                 parent: QWidget | None = None, login_startup=None):
        super().__init__(parent)
        self.intent_client = intent_client
        self.login_startup = login_startup if login_startup is not None else LoginStartup()
        self._initializing = True
        self._read_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="owg-ui-read")
        self._reads = {}
        self._update_offer = None
        self._upgrade_busy = False
        self._upgrade_process = None
        self._upgrade_folder = None
        self._upgrade_cancel = threading.Event()
        notice_path = os.environ.pop("OWG_UPGRADE_RESULT", "")
        self._upgrade_notice = read_upgrade_result(notice_path) if notice_path else ""
        self._palette_key = QApplication.instance().palette().cacheKey()
        executor = self._read_executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))
        self._read_timer = QTimer(self); self._read_timer.setInterval(50)
        self._read_timer.timeout.connect(self._finish_reads); self._read_timer.start()
        QApplication.instance().setQuitOnLastWindowClosed(False)
        self.settings_path = Path(settings_path) if settings_path else preferences.default_settings_path()
        loaded = preferences.load_settings(self.settings_path)
        self.settings = loaded.values
        self.settings_error = loaded.error
        self.layout_provider = layout_provider or LayoutProvider(timeout=0.2)
        self.application_ids = list(app_ids) if app_ids is not None else linux_application_ids()
        self._catalog_aliases = exclusion_aliases([key for key,name in self.application_ids])
        self.application_aliases = {}
        self.status: dict[str, Any] = {"readiness": "unknown", "paused": True, "suspendedReasons": ["Controller status is unknown"], "capabilities": {}}
        self.setWindowTitle("OLED Window Guard")
        self.setWindowIcon(application_icon())
        self.resize(1080, 780)
        self.controls = {}
        self._warning_visible = False
        self._notification_pending = False
        self._preview_drawings = {}
        self.warning_audio = WarningAudio(self)
        self._displays = []
        self._pages = []
        self.tabs = QTabWidget(self)
        self._build_shell()
        self._build_overview()
        self._build_movement()
        self._build_safety()
        self._build_dimming()
        self._build_layouts()
        self._build_about()
        for i in range(self.tabs.count()):
            self.navigation.addItem(self.tabs.tabText(i))
        self.navigation.setCurrentRow(0)
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.windowIcon())
        self.tray.setToolTip("OLED Window Guard")
        self.tray_menu = QMenu()
        self.tray_open = self.tray_menu.addAction("Open OLED Window Guard", lambda: self._send("Open"))
        self.tray_movement = self.tray_menu.addAction("Start movement guarding", lambda: self._toggle_guard("movement"))
        self.tray_brightness_guard = self.tray_menu.addAction("Start brightness guarding", lambda: self._toggle_guard("brightness"))
        self.tray_pause_resume = self.tray_menu.addAction("Start selected guarding", self._toggle_pause)
        self.tray_move_actions = {}
        self.tray_move_end = self.tray_menu.addSeparator()
        self.tray_skip = self.tray_menu.addAction("Skip", lambda: self._send("Skip"))
        self.tray_restore = self.tray_menu.addAction("Restore previous position", lambda: self._send("Restore"))
        self.tray_brightness_actions = {}
        self.tray_brightness = self.tray_menu.addAction("Restore brightness on all displays", lambda: self._send("RestoreBrightness"))
        self.tray_dim_actions = {}
        self.tray_dim_all = self.tray_menu.addAction("Dim all guarded displays now", lambda: self._send("DimAllDisplaysNow"))
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("Quit", lambda: self._send("Quit"))
        self.tray.setContextMenu(self.tray_menu)
        self.tray.activated.connect(lambda reason: self._send("Open") if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
        self.dock_menu = CairoDockMenu(self)
        QApplication.instance().aboutToQuit.connect(self.dock_menu.stop)
        self.update_status(self.status)
        self._initializing = False

    def _build_shell(self) -> None:
        shell = QWidget(); outer = QHBoxLayout(shell)
        outer.setContentsMargins(16, 20, 20, 16); outer.setSpacing(24)
        sidebar = QWidget(); sidebar.setFixedWidth(220); left = QVBoxLayout(sidebar)
        left.setContentsMargins(8, 0, 8, 0); left.setSpacing(14)
        logo = QLabel(); logo.setPixmap(application_icon().pixmap(48,48)); left.addWidget(logo)
        brand = QLabel("OLED\nWindow Guard"); font = brand.font(); font.setPointSize(20); font.setBold(True); brand.setFont(font)
        left.addWidget(brand)
        self.subtitle = QLabel("Keep your windows moving.\nCare for your OLED"); self.subtitle.setWordWrap(True)
        left.addWidget(self.subtitle)
        self.navigation = QListWidget(); self.navigation.setAccessibleName("Settings pages")
        self.navigation.currentRowChanged.connect(self.tabs.setCurrentIndex)
        self.tabs.currentChanged.connect(self.navigation.setCurrentRow)
        self.tabs.currentChanged.connect(self._show_page_heading)
        self.tabs.tabBar().hide(); left.addWidget(self.navigation, 1)
        self.state_badge = QLabel("Guarding"); self.state_badge.setObjectName("stateBadge")
        left.addWidget(self.state_badge)
        self.guard_indicators = {}
        for kind, title in (("movement", "Movement"), ("brightness", "Brightness")):
            row = QHBoxLayout(); row.setContentsMargins(12, 0, 12, 0)
            light = QLabel(); light.setFixedSize(10, 10)
            label = QLabel(title); label.setTextFormat(Qt.TextFormat.PlainText)
            row.addWidget(light); row.addWidget(label, 1); left.addLayout(row)
            self.guard_indicators[kind] = (light, label)
        outer.addWidget(sidebar)
        content = QVBoxLayout(); content.setSpacing(16)
        header = QHBoxLayout(); titles = QVBoxLayout()
        self.page_title = QLabel(); font = self.page_title.font(); font.setPointSize(24); font.setBold(True); self.page_title.setFont(font)
        self.page_description = QLabel(); self.page_description.setWordWrap(True)
        titles.addWidget(self.page_title); titles.addWidget(self.page_description); header.addLayout(titles, 1)
        # Global intent remains available to the tray/CLI; the header exposes
        # independent controls for the two runtime guards.
        self.pause_resume_button = QPushButton("Start guarding", self); self.pause_resume_button.hide()
        self.pause_resume_button.setObjectName("primaryAction"); self.pause_resume_button.clicked.connect(self._toggle_pause)
        actions = QVBoxLayout()
        self.movement_guard_button = QPushButton("Start movement guarding")
        self.brightness_guard_button = QPushButton("Start brightness guarding")
        for kind, button in (("movement",self.movement_guard_button),("brightness",self.brightness_guard_button)):
            button.setObjectName("primaryAction")
            button.clicked.connect(lambda checked=False, kind=kind: self._toggle_guard(kind))
            actions.addWidget(button)
        header.addLayout(actions); header.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        content.addLayout(header); content.addWidget(self.tabs, 1)
        self.save_status = QLabel("Settings save automatically. Guarding starts paused."); self.save_status.setWordWrap(True)
        content.addWidget(self.save_status); outer.addLayout(content, 1)
        self.setCentralWidget(shell)
        self.setStyleSheet("""
            QTabWidget::pane { border: 0; }
            QListWidget { border: 1px solid palette(mid); border-radius: 8px; padding: 4px; }
            QListWidget::item { padding: 8px; }
            QGroupBox { background: palette(base); border: 1px solid palette(mid); border-radius: 12px; margin-top: 24px; padding: 16px; }
            QGroupBox::title { font-weight: bold; subcontrol-origin: margin; left: 14px; top: 2px; padding: 0 6px; }
            QGroupBox#overviewMetric::title { subcontrol-position: top center; left: 0px; }
            QPushButton#primaryAction { background: palette(highlight); color: palette(highlighted-text); border: 1px solid palette(highlight); border-radius: 6px; }
            QPushButton#primaryAction:disabled { background: palette(mid); color: palette(text); border-color: palette(mid); }
            QPushButton { padding: 7px 12px; }
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit { min-height: 26px; }
            QLabel#stateBadge { background: palette(alternate-base); border: 1px solid palette(mid); border-radius: 12px; padding: 8px 16px; }
        """)

    def _show_page_heading(self, index):
        if 0 <= index < len(self._pages):
            self.page_title.setText(self._pages[index][0])
            self.page_description.setText(self._pages[index][1])
            if index == 2 and hasattr(self, "launch_at_login"):
                self._refresh_login_startup()

    def _page(self, title: str, description: str) -> QVBoxLayout:
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        page = QWidget(); layout = QVBoxLayout(page); layout.setContentsMargins(4, 0, 16, 12); layout.setSpacing(16)
        self._pages.append((title, description))
        scroll.setWidget(page); self.tabs.addTab(scroll, title)
        return layout

    def _help(self, layout, text: str) -> QLabel:
        label = QLabel(text); label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText)
        label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addRow(label) if isinstance(layout, QFormLayout) else layout.addWidget(label)
        return label

    def _card(self, layout: QVBoxLayout, title: str, description: str = "") -> QFormLayout:
        group = QGroupBox(title); form = QFormLayout(group)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setVerticalSpacing(12)
        if description: self._help(form, description)
        layout.addWidget(group)
        return form

    def _build_overview(self) -> None:
        layout = self._page("Overview", "Automatically shift and rotate application windows to reduce how long content stays in one place.")
        metrics = QHBoxLayout(); self.overview_metrics = []
        for title in ("Guarding", "Selected displays", "Last move"):
            card = QGroupBox(title); card.setObjectName("overviewMetric"); card.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            body = QVBoxLayout(card); value = QLabel("—"); value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            font = value.font(); font.setPointSize(15); font.setBold(True); value.setFont(font)
            value.setWordWrap(True); body.addWidget(value); self.overview_metrics.append(value); metrics.addWidget(card, 1)
        layout.addLayout(metrics)
        self.next_move_label = QLabel("Next move check: paused"); self.next_move_label.setWordWrap(True)
        self.next_move_label.setToolTip("Each selected display has its own interval. When due, OWG waits for safe conditions and eligible windows before showing a movement warning.")
        layout.addWidget(self.next_move_label)
        form = self._card(layout, "Movement preview", "Review current positions and destinations. The warning checks the proposal again after grace; the writer revalidates it before moving.")
        self.preview_display = QComboBox(); self.preview_display.setAccessibleName("Display for preview, move and restore")
        self.preview_display.currentIndexChanged.connect(self._preview_display_changed)
        form.addRow("Display", self.preview_display)
        self.movement_preview = MovementPreview(); form.addRow(self.movement_preview)
        self.preview_label = self._help(form, "Preview calculates a proposal without moving windows.")
        self.preview_button = QPushButton("Preview safe moves"); self.move_now_button = QPushButton("Move after warning…")
        self.restore_button = QPushButton("Restore last move…")
        row = QGridLayout(); row.setSpacing(8)
        for i, (button, action) in enumerate(((self.preview_button,"PlanPreview"),(self.move_now_button,"MoveNow"),(self.restore_button,"Restore"))):
            row.addWidget(button, 0, i)
            button.clicked.connect(lambda checked=False, a=action: self._send(a))
        form.addRow(row)
        self._help(form, "Windows show application names and movement eligibility. Striped windows are excluded; dashed windows are unavailable. Mint outlines show move or Restore destinations. Hover for details. The preview uses positions and application names, never window contents.")
        self._help(form, "Move after warning starts a countdown when movement guarding is active and this display has eligible windows. With no eligible windows, the timer keeps checking silently. Restore last move warns before undoing the last successful move on this display, including any size adjustments. After the initial grace period, clicks, scrolling, dragging or typing on the affected display cancel its warning. Moving the pointer alone and interactions on other displays do not.")
        form = self._card(layout, "Your displays", "Select the screens to guard. Automatic movement follows each display’s interval and settings; manual actions use the display chosen above.")
        self.movement_display_list = QListWidget(); self.movement_display_list.setMaximumHeight(110)
        self.movement_display_list.setAccessibleName("Movement displays")
        self.movement_display_list.itemChanged.connect(self._movement_displays_changed); form.addRow(self.movement_display_list)
        self.display_summary = self._help(form, "Waiting for connected displays…")
        self._help(form, "Movement and dimming have independent display selections. New displays stay unselected until you choose them.")
        form = self._card(layout, "Guarding status")
        self.readiness_label = QLabel(); self.paused_label = QLabel(); self.reason_label = QLabel(); self.last_result_label = QLabel(); self.warning_label = QLabel()
        for label in (self.readiness_label, self.paused_label, self.reason_label, self.last_result_label, self.warning_label):
            label.setWordWrap(True); label.setTextFormat(Qt.TextFormat.PlainText); form.addRow(label)
        self.skip_button = QPushButton("Skip warning"); self.restore_brightness_button = QPushButton("Restore brightness"); self.quit_button = QPushButton("Quit")
        row = QHBoxLayout()
        for button, action in ((self.skip_button,"Skip"),(self.restore_brightness_button,"RestoreBrightness"),(self.quit_button,"Quit")):
            row.addWidget(button); button.clicked.connect(lambda checked=False, a=action: self._send(a))
        form.addRow(row)
        self._help(form, "Closing the dashboard keeps OWG in the system tray. Quit stops guarding; reopening starts paused. Movement and dimming cannot guarantee prevention of OLED burn-in. Keep your display’s built-in care and sleep features enabled.")
        layout.addStretch()

    def _preview_display_changed(self):
        if hasattr(self, "movement_preview"):
            if self.preview_display.currentData():
                self.previewDisplayChanged.emit(self.preview_display.currentData())
            self._update_preview()
            self._refresh_action_states()

    def _update_preview(self):
        result = self.status.get("planningResult", {})
        preview = result.get("preview") if result.get("status") in ("ready", "no-plan") else None
        target = self.preview_display.currentData()
        names = {key.lower().removesuffix('.desktop'): name for key,name in self.application_ids}
        for app_id, aliases in {**self._catalog_aliases, **self.application_aliases}.items():
            if app_id.lower().removesuffix('.desktop') in names:
                for alias in aliases:
                    names[alias.lower().removesuffix('.desktop')] = names[app_id.lower().removesuffix('.desktop')]
        if preview:
            self.movement_preview.set_preview(preview, preview.get("outputId"), names)
            if self.movement_preview.data:
                self._preview_drawings[preview["outputId"]] = self.movement_preview.data
        live = self.status.get("livePreview")
        drawing = preview if preview and preview.get("outputId") == target else live if live and live.get("outputId") == target else self._preview_drawings.get(target)
        restore = self.status.get('restorePreview')
        restoring = bool(restore and restore.get('outputId') == target and self.status.get('transactionActive') is True)
        if restoring:
            drawing = restore
        self.movement_preview.set_preview(drawing, target, names)
        stale = bool(drawing) and drawing is not live and (result.get("status") not in ("ready", "no-plan") or not preview or preview.get("outputId") != target)
        if restoring:
            count = len(restore['destinations'])
            text = f"Restore preview: {count} window{'s' if count != 1 else ''} will return to their previous positions after the warning."
        elif drawing is live and live:
            text = "Current window positions. Preview safe moves to review destinations."
        elif stale:
            text = "Last preview snapshot. Preview safe moves to refresh it; movement always uses a fresh safety check."
        elif preview and preview.get("outputId") != target:
            text = "Preview safe moves to calculate destinations for this display."
        elif result.get("status") == "ready":
            text = f"Preview: {result.get('moves', 0)} proposed moves. No windows were moved."
        elif result.get("status") == "no-plan":
            text = "Preview: no complete movement plan was found. No windows were moved."
        elif result.get("status") not in (None, "none"):
            text = f"Preview: {result['status']}" + (f" ({result['reason']})" if result.get("reason") else "")
        else:
            text = "Preview calculates a proposal without moving windows."
        if drawing and self.movement_preview.data and drawing['totalWindows'] > len(drawing['windows']):
            text += f" Showing {len(drawing['windows'])} of {drawing['totalWindows']} windows. All windows were considered by the planner."
        self.preview_label.setText(text)

    def _control(self, key: str, spec: Mapping[str, Any], value: Any, callback) -> QWidget:
        if key == "mode":
            control = MovementStyleSelector(value)
            control.currentIndexChanged.connect(lambda _: callback(control.currentData()))
        elif spec.get("type") == "boolean":
            control = QCheckBox(); control.setChecked(value)
            control.toggled.connect(callback)
        elif key in ("shiftRangePercent", "sizeTolerance", "windowDimPercent", "displayDimPercent", "windowDimFade", "displayDimFade"):
            tolerance = key == "sizeTolerance"
            fade = key.endswith("Fade")
            control = SliderControl(spec, value, multiplier=100 if tolerance else 1,
                                    suffix=" s" if fade else " %", step=.5 if fade else (.01 if tolerance else 1))
            control.valueChanged.connect(callback)
        elif spec.get("type") in ("integer", "number"):
            control = QSpinBox() if spec["type"] == "integer" else CompactDoubleSpinBox()
            control.setRange(spec.get("minimum", 0), spec.get("maximum", 9999))
            if isinstance(control, QDoubleSpinBox):
                control.setDecimals(2); control.setSingleStep(spec.get("multipleOf", 0.01 if key == "sizeTolerance" else 0.5))
            control.setKeyboardTracking(False)
            control.setValue(value)
            def changed(value):
                if "multipleOf" in spec:
                    value = round(value / spec["multipleOf"]) * spec["multipleOf"]
                    control.blockSignals(True); control.setValue(value); control.blockSignals(False)
                callback(value)
            control.valueChanged.connect(changed)
        else:
            control = QComboBox()
            modes = {"stay-bright":"Stay bright until I interact with a window", "restart-timers":"Restore brightness and restart dimming timers", "drift": "Shift within free space", "swap": "Swap positions", "group": "Move within layout groups"}
            for option in spec.get("enum", [value]): control.addItem(modes.get(option, option), option)
            control.setCurrentIndex(control.findData(value))
            control.currentIndexChanged.connect(lambda _: callback(control.currentData()))
        control.setAccessibleName(key)
        return control

    def _setting_control(self, key: str, label: str, form: QFormLayout) -> QWidget:
        control = self._control(key, preferences.schema_metadata()["properties"][key], self.settings[key], lambda value: self._set_setting(key, value))
        control.setAccessibleName(label); self.controls[key] = control
        if key == "mode":
            form.addRow(QLabel(label)); form.addRow(control)
        else:
            form.addRow(label, control)
        return control

    def _build_movement(self) -> None:
        layout = self._page("Movement", "Choose how far, how often, and how many.")
        form = self._card(layout, "Movement displays", "Choose screens in Overview. Each selected display can follow the defaults or its own settings below.")
        choose = QPushButton("Choose displays in Overview"); choose.clicked.connect(lambda: self.navigation.setCurrentRow(0)); form.addRow(choose)
        form = self._card(layout, "Default movement settings")
        self._setting_control("mode", "Movement style", form)
        for key, label in (("intervalMinutes", "Check every (minutes)"), ("maximumWindows", "Up to this many windows per cycle")):
            self._setting_control(key, label, form)
        self._help(form, "Idle focused windows can move. Finish interacting before the warning’s grace period ends.")
        self.movement_detail = self._help(form, "")
        self._movement_option_form = form
        for key, label in (("keepSimilarWindowsTogether", "Keep adjacent similar-sized windows together (Swap)"), ("shiftHorizontalOrder", "Allow horizontal order changes (Shift)"), ("shiftVerticalOrder", "Allow vertical order changes (Shift)")):
            self._setting_control(key, label, form)
        self.order_help = self._help(form, "Order changes allow coordinated shifts when space permits. These options may use brief overlap during placement; final positions remain separate.")
        form = self._card(layout, "Movement range", "A percentage of the display’s usable width and height. Available space and other windows can reduce the movement; Swap positions and group rotation use their own destinations.")
        self.range_card = form.parentWidget()
        self._setting_control("shiftRangePercent", "Maximum shift range", form)
        self._help(form, "For example, 10% on a 5120 × 2160 display permits up to 512 logical pixels horizontally and 216 vertically. Shift distances vary, and smaller safe moves can be used when space is limited.")
        form = self._card(layout, "Size matching", "Swap prefers similar-sized windows and can adjust resizable windows by up to 2 logical pixels to fit rounded slots. Group rotation also accounts for zone rounding and padding. Restore returns original positions and sizes.")
        self.size_card = form.parentWidget()
        self._setting_control("sizeTolerance", "Preferred size tolerance", form)
        form = self._card(layout, "Layout group", "Used by the layout-group movement style. Rotation fits resizable windows to rounded zone edges, allowing growth of up to 2 logical pixels or bounded reductions for padding. Restore returns their original sizes.")
        self.group_card = form.parentWidget()
        self.group_combo = QComboBox(); self.group_combo.setAccessibleName("Movement layout group")
        self.group_combo.currentIndexChanged.connect(self._group_selected)
        form.addRow("Group", self.group_combo); self._setting_control("rotateGroup", "Rotate between zones", form)
        self._build_overrides(layout, "movement")
        self._refresh_movement_options()
        layout.addStretch()

    def _refresh_movement_options(self):
        mode = self.settings["mode"]
        for key, visible in (("keepSimilarWindowsTogether", mode == "swap"),
                             ("shiftHorizontalOrder", mode == "drift"),
                             ("shiftVerticalOrder", mode == "drift")):
            self._movement_option_form.setRowVisible(self.controls[key], visible)
        self.order_help.setVisible(mode == "drift")
        self.range_card.setVisible(mode != "swap")
        self.size_card.setVisible(mode != "drift")
        self.group_card.setVisible(mode == "group")
        self.movement_detail.setText({
            "drift": "Windows shift into free space without resizing. Larger ranges allow more variety; obstacles and display edges can reduce the distance.",
            "swap": "Windows exchange positions or form coordinated arrangements. A resizable window may grow or shrink by up to 2 logical pixels to fit a rounded slot; Restore returns its original size. Similar-sized neighbours can stay together when enabled.",
            "group": "Choose a built-in or imported group below. Windows can shift within their current zone, or rotate between zones when enabled. Restore also returns any adjusted sizes."
        }[mode])

    def _build_safety(self) -> None:
        layout = self._page("Safety & alerts", "Predictable movement starts with sensible boundaries.")
        form = self._card(layout, "Before every move", "An on-screen countdown appears before every move and Restore. Sound can play when the countdown starts. Optional desktop summaries arrive after guarding pauses. Your KDE audio and notification settings apply.")
        for key, label in (("warningSeconds", "Warning countdown (seconds)"), ("soundEnabled", "Play a sound when a warning starts"), ("systemNotifications", "Send a summary notification when guarding pauses")):
            self._setting_control(key, label, form)
        self.test_sound = QPushButton("Test warning sound")
        self.test_sound.clicked.connect(lambda: self.warning_audio.play())
        form.addRow(self.test_sound)
        self.sound_status = self._help(form, "Uses the default audio output and its current volume.")
        self.warning_audio.message.connect(self.sound_status.setText)
        form = self._card(layout, "Protect your workflow", "Focused windows can move when idle. The initial warning grace period gives you time to finish interacting; completed arrangements must remain on-screen and free of overlap.")
        self._setting_control("quietSeconds", "Interaction grace period (seconds)", form)
        self._setting_control("allowTransientOverlap", "Allow brief overlap during swaps", form)
        self._help(form, "You can finish typing, scrolling or dragging during the initial grace period. Interaction on the affected display after that cutoff cancels the move. The grace period is limited to the warning countdown. Warnings and OWG movements preserve dimming. Pointer motion alone and interactions on other displays do not. Full-screen, maximised, minimised and unsupported windows stay in place. Changing displays or desktops, locking, and signing out cancel pending moves.")
        form = self._card(layout, "Startup")
        self.launch_at_login = QCheckBox("Launch at login"); self.launch_at_login.setAccessibleName("Launch at login")
        form.addRow(self.launch_at_login)
        self.login_status = self._help(form, "")
        self.launch_at_login.toggled.connect(self._set_login_startup)
        self._refresh_login_startup()
        self._setting_control("startMinimized", "Start OWG minimized", form)
        self._setting_control("resumeAfterUnlock", "Resume guarding after unlock or wake", form)
        self._help(form, "A fresh app session always starts with movement and brightness guarding paused. When enabled, unlocking or waking resumes the guards that were active before the interruption, once the desktop and input are ready. Display power saving does not forget those guards.")
        form = self._card(layout, "App exclusions", "Excluded applications stay in place and still count as obstacles. Choose apps that stay bright separately in Dimming.")
        self.excluded_apps = self._application_exclusions(form, "excludedApps", "Excluded applications", self._excluded_apps_changed)
        layout.addStretch()

    def _refresh_login_startup(self):
        state = self.login_startup.read()
        self.launch_at_login.blockSignals(True)
        self.launch_at_login.setChecked(state.enabled)
        self.launch_at_login.setEnabled(state.editable)
        self.launch_at_login.blockSignals(False)
        self.login_status.setText(state.message)

    def _set_login_startup(self, enabled):
        try:
            self.login_startup.set_enabled(enabled)
            self._refresh_login_startup()
        except (OSError, ValueError) as error:
            self._refresh_login_startup()
            self.login_status.setText("Could not change Launch at login: " + str(error))

    def _application_exclusions(self, form, key, name, callback):
        search = QLineEdit(); search.setPlaceholderText("Search applications…"); search.setAccessibleName(name + " search"); form.addRow(search)
        widget = QListWidget(); widget.setAccessibleName(name); widget.setMinimumHeight(160)
        apps = dict(self.application_ids)
        for app_id in self.settings[key]: apps.setdefault(app_id, app_id)
        for app_id, label in sorted(apps.items(), key=lambda item: item[1].casefold()):
            item = QListWidgetItem(f"{label} ({app_id})", widget); item.setData(Qt.ItemDataRole.UserRole, app_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if app_id in self.settings[key] else Qt.CheckState.Unchecked)
        widget.itemChanged.connect(callback); form.addRow(widget)
        search.textChanged.connect(lambda text: self._filter_apps(text, [widget]))
        return widget

    def _filter_apps(self, text: str, widgets=None) -> None:
        for widget in widgets or (self.excluded_apps, self.dimming_excluded_apps):
            for i in range(widget.count()): widget.item(i).setHidden(text.casefold() not in widget.item(i).text().casefold())

    def _build_dimming(self) -> None:
        layout = self._page("Dimming", "Keep attention on your active window.")
        form = self._card(layout, "Dimming displays", "Choose displays independently of movement. Start brightness guarding to apply dimming; pausing brightness guarding restores brightness.")
        self.dimming_display_list = QListWidget(); self.dimming_display_list.setMaximumHeight(140); self.dimming_display_list.setAccessibleName("Dimming displays")
        self.dimming_display_list.itemChanged.connect(self._dimming_displays_changed); form.addRow(self.dimming_display_list)
        for title, entries, description in (
            ("Background windows", (("dimWindows","Dim unfocused windows"),("windowDimPercent","Darkness"),("windowDimDelay","Activation delay (seconds)"),("windowDimFade","Fade-in (seconds)")), "Focused content stays bright. Clicking empty desktop space brightens that display. Choose apps that stay bright below."),
            ("Inactive displays", (("dimDisplays","Dim displays without focused content"),("displayDimPercent","Darkness"),("displayDimDelay","Activation delay (seconds)"),("displayDimFade","Fade-in (seconds)")), "A focused window spanning displays keeps both bright. Display darkness replaces window darkness; amounts do not add together.")):
            form = self._card(layout, title, description)
            for key, label in entries: self._setting_control(key, label, form)
        form = self._card(layout, "Restore brightness", "Restore Brightness on Overview clears both effects for at least five seconds, respecting longer activation delays. Dimming can then resume.")
        self.dimming_brightness_button = QPushButton("Restore brightness now · Meta+Shift+B")
        self.dimming_brightness_button.clicked.connect(lambda: self._send("RestoreBrightness")); form.addRow(self.dimming_brightness_button)
        self._setting_control("brightnessShortcutEnabled", "Enable Meta+Shift+B (customisable in KDE)", form)
        self._help(form, "0% adds no darkness; 90% is the darkest setting. Fade-in follows the delay, in half-second steps. Dimming continues through warnings, movement and Restore without restarting its timers. OWG starts paused after relaunch.")
        form = self._card(layout, "Apps that stay bright", "Exempt visible areas of these apps from both window and display dimming. These choices are independent of movement exclusions.")
        self.dimming_excluded_apps = self._application_exclusions(form, "dimmingExcludedApps", "Dimming excluded applications", self._dimming_excluded_apps_changed)
        form = self._card(layout, "Clicking empty desktop space", "Choose what happens on the display you click. This applies to both window and display dimming.")
        self._setting_control("desktopClickBehavior", "After a desktop click", form)
        self._help(form, "Stay bright keeps the clicked display and its windows clear until you interact with an application. Restart timers restores brightness immediately, then allows both effects to return after their activation delays and fades. A zero delay allows immediate dimming.")
        self._build_overrides(layout, "dimming")
        layout.addStretch()

    def _build_overrides(self, layout, kind: str) -> None:
        disclosure = QPushButton("Customise individual displays…"); disclosure.setCheckable(True)
        disclosure.setAccessibleName(kind.title() + " per-display settings")
        layout.addWidget(disclosure)
        form = self._card(layout, "Per-display settings", "Choose a display to customise. Only edited fields override defaults. Reset returns all fields for that display to the defaults above.")
        form.parentWidget().hide(); disclosure.toggled.connect(form.parentWidget().setVisible)
        combo = QComboBox(); setattr(self, kind + "_override_display", combo)
        combo.currentIndexChanged.connect(lambda: self._load_overrides(kind)); form.addRow("Display", combo)
        controls = {}; setattr(self, kind + "_override_controls", controls)
        labels = {"intervalMinutes":"Check every (minutes)","mode":"Movement style","shiftRangePercent":"Maximum shift range","maximumWindows":"Window limit","sizeTolerance":"Size tolerance","keepSimilarWindowsTogether":"Keep similar neighbours together","shiftHorizontalOrder":"Horizontal order changes","shiftVerticalOrder":"Vertical order changes","groupId":"Layout group","rotateGroup":"Rotate zones","windows":"Allow window dimming","display":"Allow display dimming","windowDimPercent":"Window darkness","displayDimPercent":"Display darkness","windowDimDelay":"Window delay (seconds)","displayDimDelay":"Display delay (seconds)","windowDimFade":"Window fade (seconds)","displayDimFade":"Display fade (seconds)","desktopClickBehavior":"After a desktop click"}
        for key, spec in preferences.schema_metadata()["$defs"][kind + "Override"]["properties"].items():
            global_key = {"windows":"dimWindows","display":"dimDisplays"}.get(key,key)
            value = True if key in ("windows","display") else self.settings[global_key]
            control = self._control(key, spec, value, lambda value, k=key: self._save_override(kind,k,value))
            controls[key] = control; form.addRow(labels[key],control)
        reset = QPushButton("Reset this display to defaults"); reset.clicked.connect(lambda: self._reset_override(kind))
        setattr(self, kind + "_override_reset", reset); form.addRow(reset)
        if kind == "dimming": self._help(form, "Per-display switches can disable an effect here. The global switches above must also be on.")
        # Retain the stable widget API used by existing acceptance tooling.
        if kind == "movement": self.override_interval=controls["intervalMinutes"]; self.override_mode=controls["mode"]
        else:
            self.dim_override_windows=controls["windows"]; self.dim_override_display_control=controls["display"]
            self.dim_override_window_percent=controls["windowDimPercent"]; self.dim_override_display_percent=controls["displayDimPercent"]

    def _build_layouts(self) -> None:
        layout = self._page("Layout groups", "Give your Window Layouts groups room to move.")
        form = self._card(layout, "Available layouts", "Choose the movement group on the Movement page. Windows must fit within a zone; leave room inside its boundaries for shifts.")
        self.layout_status = self._help(form, ""); self.layout_list = QListWidget(); self.layout_list.setAccessibleName("Imported layout groups"); self.layout_list.setMinimumHeight(200)
        form.addRow(self.layout_list)
        refresh = QPushButton("Refresh layouts"); refresh.clicked.connect(self.refresh_layouts); form.addRow(refresh)
        self._apply_layout_snapshot({"status":"loading", "diagnostics":[], "groups":[], "layouts":[]})
        self.refresh_layouts(); layout.addStretch()

    def _build_about(self) -> None:
        layout = self._page("About", "Keep your windows moving.\nCare for your OLED.")
        form = self._card(layout, "OLED Window Guard 1.0.5 · Dr. Brett Addison")
        self.about_text = self._help(form, "Guarded movement and software dimming for your desktop. Original KDE implementation: GNU GPL version 3 or later. Shared schema and interfaces: MIT. Third-party components retain their own licences.")
        self._help(form, "OWG is provided without warranty. It cannot guarantee prevention of image retention or burn-in. The supported package targets Fedora 44, KWin 6.7.5 and Qt 6.11.2. Enable Resume guarding after unlock or wake in Safety & alerts to continue previously active guarding after a session interruption.")
        self.package_update_label = self._help(form, "")
        self.check_updates_button = QPushButton("Check for updates")
        self.check_updates_button.clicked.connect(lambda: self.refresh_package_status(check_github=True))
        form.addRow(self.check_updates_button)
        self.upgrade_button = QPushButton("Download and upgrade…")
        self.upgrade_button.hide(); self.upgrade_button.clicked.connect(self._confirm_upgrade)
        form.addRow(self.upgrade_button)
        self._help(form, "Check for updates contacts the official GitHub repository. Downloads and installation require your confirmation. The RPM is verified against GitHub’s SHA-256 digest and checked against your installed KWin/Qt versions. Administrator authorization is required. OWG closes during installation and reopens paused with your settings retained. Installation does not enable autostart.")
        self.refresh_package_status(); layout.addStretch()

    def _queue_read(self, name: str, read, apply) -> None:
        if name not in self._reads:
            self._reads[name] = (self._read_executor.submit(read), apply)

    def _finish_reads(self) -> None:
        palette_key = QApplication.instance().palette().cacheKey()
        if palette_key != self._palette_key:
            self._palette_key = palette_key
            # Palette colours in QSS need polishing after a live KDE theme change.
            self.setStyleSheet(self.styleSheet())
        for name, (future, apply) in list(self._reads.items()):
            if not future.done(): continue
            del self._reads[name]
            try: apply(future.result())
            except Exception as exc:
                if name in ("package", "upgrade-download"):
                    self._upgrade_busy = False
                    self._apply_package_status({"message": f"Unable to complete update request: {exc}. Try again.", "offer": None})
                else:
                    self.layout_status.setText(f"Unable to refresh {name}: {exc}. Try again.")
        if self._upgrade_process is not None and self._upgrade_process.poll() is not None:
            self._upgrade_process = None; self._upgrade_busy = False
            notice = read_upgrade_result(self._upgrade_folder / "result.json")
            self._apply_package_status({"message": notice or "The upgrade helper stopped. Check for updates to try again.", "offer": None})

    def refresh_package_status(self, *, check_github=False) -> None:
        if "package" in self._reads or self._upgrade_busy:
            return
        self._update_offer = None; self.upgrade_button.hide()
        self.check_updates_button.setEnabled(False)
        self.package_update_label.setText("Checking GitHub for updates…" if check_github else "Checking installed package…")
        self._queue_read("package", check_for_updates if check_github else package_update_status, self._apply_package_status)

    def _apply_package_status(self, result):
        self._update_offer = result.get("offer")
        self.package_update_label.setText((self._upgrade_notice + "\n\n" if self._upgrade_notice else "") + result["message"])
        self.check_updates_button.setEnabled(not self._upgrade_busy)
        self.upgrade_button.setVisible(self._update_offer is not None)
        self.upgrade_button.setEnabled(not self._upgrade_busy)
        if self._update_offer:
            self.upgrade_button.setText(f"Download and upgrade to {self._update_offer['version']}…")

    def _confirm_upgrade(self):
        if self._upgrade_busy or not self._update_offer:
            return
        version = self._update_offer['version']
        answer = QMessageBox.question(self, "Upgrade OLED Window Guard",
            f"Download and install version {version}?\n\nOWG will close for installation and reopen paused. Your settings will be retained. Administrator authorization is required.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._upgrade_notice = ""; self._upgrade_busy = True; self._upgrade_cancel.clear()
        self.check_updates_button.setEnabled(False); self.upgrade_button.setEnabled(False)
        self.package_update_label.setText(f"Downloading and verifying version {version}…")
        offer = dict(self._update_offer)
        self._queue_read("upgrade-download", lambda: download_update(offer, cancel=self._upgrade_cancel), self._launch_upgrade)

    def _launch_upgrade(self, folder):
        self._upgrade_folder = Path(folder)
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        with (self._upgrade_folder / "upgrade.log").open("w") as log:
            self._upgrade_process = subprocess.Popen([sys.executable, "-m", "oled_window_guard.upgrade_worker",
                str(self._upgrade_folder), str(os.getpid())], env=env, cwd="/tmp", stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.package_update_label.setText("Preparing upgrade. OWG will close and request administrator authorization, then reopen paused.")

    def cancel_update_download(self):
        self._upgrade_cancel.set()

    @staticmethod
    def _display_name(display: Mapping[str, Any]) -> str:
        name = str(display.get("name") or display.get("id", "Display"))
        try:
            identity = json.loads(display["id"])
            if isinstance(identity, list) and len(identity) == 4 and all(isinstance(v,str) for v in identity):
                brand = " ".join(v for v in identity[1:3] if v).strip()
                if brand: return f"{brand} · {identity[0]}"
        except (ValueError, TypeError, KeyError): pass
        return name

    def set_displays(self, displays: list[Mapping[str, Any]]) -> None:
        displays = [dict(display, name=self._display_name(display)) for display in displays]
        self._displays = list(displays)
        self._preview_drawings = {key:value for key,value in self._preview_drawings.items() if any(d.get("id")==key for d in displays)}
        previous_movement = self.movement_override_display.currentData()
        previous_dimming = self.dimming_override_display.currentData()
        previous_preview = self.preview_display.currentData()
        self.preview_display.blockSignals(True)
        self.preview_display.clear()
        self.movement_override_display.blockSignals(True)
        self.movement_override_display.clear()
        self.movement_override_display.addItem("Use global settings", "")
        self.dimming_override_display.blockSignals(True); self.dimming_override_display.clear(); self.dimming_override_display.addItem("Use global settings", "")
        for widget, key in ((self.movement_display_list, "movementDisplays"), (self.dimming_display_list, "dimmingDisplays")):
            widget.blockSignals(True); widget.clear()
            selected = set(self.settings[key])
            for display in displays:
                display_id = str(display.get("id", ""))
                if not display_id: continue
                item = QListWidgetItem(str(display.get("name") or display_id), widget)
                item.setData(Qt.ItemDataRole.UserRole, display_id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Checked if display_id in selected else Qt.CheckState.Unchecked)
                if widget is self.movement_display_list:
                    self.preview_display.addItem(str(display.get("name") or display_id), display_id)
                    self.movement_override_display.addItem(str(display.get("name") or display_id), display_id)
                    self.dimming_override_display.addItem(str(display.get("name") or display_id), display_id)
            widget.blockSignals(False)
        for combo, previous in ((self.movement_override_display, previous_movement), (self.dimming_override_display, previous_dimming)):
            index = combo.findData(previous)
            if index >= 0: combo.setCurrentIndex(index)
        self.display_summary.setText("\n".join(str(d.get("name") or d["id"]) for d in displays) or "No displays available. Wait for the compositor connection, then review your selections.")
        self.movement_override_display.blockSignals(False)
        self.dimming_override_display.blockSignals(False)
        preview_index = self.preview_display.findData(previous_preview)
        if preview_index < 0:
            preview_index = next((i for i in range(self.preview_display.count())
                if self.preview_display.itemData(i) in self.settings["movementDisplays"]), -1)
        if preview_index >= 0:
            self.preview_display.setCurrentIndex(preview_index)
        self.preview_display.blockSignals(False)
        if self.preview_display.currentData():
            self.previewDisplayChanged.emit(self.preview_display.currentData())
        self._load_movement_override()
        self._load_dimming_override()
        self._update_preview()
        self._refresh_action_states()

    def _load_overrides(self, kind: str) -> None:
        display_id = getattr(self, kind + "_override_display").currentData()
        override = self.settings[kind + "Overrides"].get(display_id, {}) if display_id else {}
        for key, control in getattr(self, kind + "_override_controls").items():
            global_key = {"windows":"dimWindows","display":"dimDisplays"}.get(key,key)
            value = override.get(key, True if key in ("windows","display") else self.settings[global_key])
            control.blockSignals(True)
            if isinstance(control,QCheckBox): control.setChecked(value)
            elif isinstance(control,(QComboBox, MovementStyleSelector)): control.setCurrentIndex(control.findData(value))
            else: control.setValue(value)
            control.setEnabled(bool(display_id)); control.blockSignals(False)
            control.setToolTip("Custom value for this display" if key in override else "Using the default value")
        getattr(self, kind + "_override_reset").setEnabled(bool(override))

    def _load_movement_override(self, *_: Any) -> None: self._load_overrides("movement")
    def _load_dimming_override(self, *_: Any) -> None: self._load_overrides("dimming")

    def _save_override(self, kind: str, key: str, value: Any) -> None:
        display_id = getattr(self, kind + "_override_display").currentData()
        if not display_id: return
        latest = preferences.load_settings(self.settings_path)
        base = latest.values if not latest.error and self.settings_path.exists() else self.settings
        overrides = dict(base[kind + "Overrides"])
        overrides[display_id] = {**overrides.get(display_id, {}), key:value}
        self._set_setting(kind + "Overrides", overrides)
        self._load_overrides(kind)

    def _reset_override(self, kind: str) -> None:
        display_id = getattr(self, kind + "_override_display").currentData()
        latest = preferences.load_settings(self.settings_path)
        base = latest.values if not latest.error and self.settings_path.exists() else self.settings
        overrides = dict(base[kind + "Overrides"]); overrides.pop(display_id, None)
        self._set_setting(kind + "Overrides", overrides); self._load_overrides(kind)

    def _set_setting(self, key: str, value: Any) -> None:
        latest = preferences.load_settings(self.settings_path)
        base = latest.values if not latest.error and self.settings_path.exists() else self.settings
        self.settings = preferences.validate_settings({**base, key: value})
        for setting, control in self.controls.items():
            current = self.settings[setting]; control.blockSignals(True)
            if isinstance(control, QCheckBox): control.setChecked(current)
            elif isinstance(control, (QComboBox, MovementStyleSelector)): control.setCurrentIndex(control.findData(current))
            else: control.setValue(current)
            control.blockSignals(False)
        if hasattr(self, "dimming_override_controls"):
            self._load_overrides("movement"); self._load_overrides("dimming")
        if hasattr(self, "group_card"):
            self._refresh_movement_options()
        if not self._initializing:
            self._persist_settings()

    def _checked_ids(self, widget: QListWidget) -> list[str]:
        return [str(widget.item(i).data(Qt.ItemDataRole.UserRole)) for i in range(widget.count()) if widget.item(i).checkState() == Qt.CheckState.Checked]

    def _movement_displays_changed(self, _item: QListWidgetItem) -> None:
        self._set_setting("movementDisplays", self._checked_ids(self.movement_display_list) + [v for v in self.settings["movementDisplays"] if v not in {d["id"] for d in self._displays}])

    def _dimming_displays_changed(self, _item: QListWidgetItem) -> None:
        self._set_setting("dimmingDisplays", self._checked_ids(self.dimming_display_list) + [v for v in self.settings["dimmingDisplays"] if v not in {d["id"] for d in self._displays}])

    def _excluded_apps_changed(self, _item: QListWidgetItem) -> None:
        self._set_setting("excludedApps", self._checked_ids(self.excluded_apps))

    def _dimming_excluded_apps_changed(self, _item: QListWidgetItem) -> None:
        self._set_setting("dimmingExcludedApps", self._checked_ids(self.dimming_excluded_apps))

    def _persist_settings(self) -> None:
        try:
            preferences.save_settings(self.settings_path, self.settings)
            self.settings_error = None
            revision = hashlib.sha256(json.dumps(self.settings, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()
            response = self.intent_client.submit("SettingsChanged", {"revision": revision})
            if isinstance(response, Mapping) and response.get("ok") is True and isinstance(response.get("status"), Mapping):
                self.update_status(response["status"])
            if isinstance(response, Mapping) and response.get("ok") is False:
                raise RuntimeError(str(response.get("reason", "settings-not-accepted")))
        except (preferences.SettingsError, OSError, RuntimeError) as exc:
            self.settings_error = str(exc)
            self.save_status.setText(f"Settings error: {exc}")
            self.reason_label.setText(f"Settings error: {exc}")

    def _group_selected(self) -> None:
        group_id = self.group_combo.currentData()
        if group_id is not None and not self._initializing:
            self._set_setting("groupId", group_id)

    def refresh_layouts(self) -> None:
        self.layout_status.setText("Reading layout groups…")
        self._queue_read("layouts", self.layout_provider.read_snapshot, self._apply_layout_snapshot)

    def _apply_layout_snapshot(self, snapshot: Mapping[str, Any]) -> None:
        self.layout_status.setText(f"Layout source: {snapshot['status']} — {'; '.join(snapshot['diagnostics']) or 'read-only snapshot'}")
        self.group_combo.blockSignals(True)
        self.group_combo.clear()
        self.group_combo.addItem("Choose a group…", "")
        for group_id, label in (("halves", "Halves"), ("horizontal-halves", "Horizontal halves"),
                                ("vertical-halves", "Vertical halves"), ("quarters", "Quarters"),
                                ("thirds", "Thirds"), ("two-thirds", "Two thirds")):
            self.group_combo.addItem(label, "builtin."+group_id)
        if snapshot.get("executionReady") is True:
            for group in snapshot.get("groups", []):
                if group["layoutIds"]:
                    self.group_combo.addItem("Window Layouts: "+group["name"], "kde:"+group["id"])
        selected = self.group_combo.findData(self.settings["groupId"])
        if selected < 0:
            self.group_combo.addItem("Unavailable: "+self.settings["groupId"], self.settings["groupId"])
            selected = self.group_combo.count()-1
        self.group_combo.setCurrentIndex(selected)
        self.group_combo.blockSignals(False)
        group_control = self.movement_override_controls["groupId"]
        group_control.blockSignals(True); group_control.clear()
        for i in range(self.group_combo.count()): group_control.addItem(self.group_combo.itemText(i),self.group_combo.itemData(i))
        for override in self.settings["movementOverrides"].values():
            group_id = override.get("groupId")
            if group_id is not None and group_control.findData(group_id) < 0: group_control.addItem("Unavailable: " + group_id, group_id)
        group_control.blockSignals(False); self._load_overrides("movement")
        self.layout_list.clear()
        for group in snapshot.get("groups", []): self.layout_list.addItem(f"Group: {group['name']} ({len(group['layoutIds'])} layouts)")
        for layout in snapshot.get("layouts", []): self.layout_list.addItem(f"{layout['name']} — {layout['width']:.0%} × {layout['height']:.0%}")

    def update_status(self, status: Mapping[str, Any]) -> None:
        self.status = dict(status)
        self.state_badge.setText("Guarding" if status.get("paused") is False else "Paused" if status.get("readiness") == "ready" else "Waiting for readiness")
        self.readiness_label.setText(f"Readiness: {status.get('readiness', 'unknown')}")
        movement_paused=status.get("movementPaused",status.get("paused")) is not False
        brightness_paused=status.get("brightnessPaused",status.get("paused")) is not False
        movement_text="waiting for unlock or wake" if status.get("movementResumePending") else "paused" if movement_paused else "active"
        brightness_text="waiting for unlock or wake" if status.get("brightnessResumePending") else "paused" if brightness_paused else "active"
        for kind, paused in (("movement", movement_paused), ("brightness", brightness_paused)):
            light, label = self.guard_indicators[kind]
            if status.get(kind + "ResumePending"):
                state, colour = "Waiting", "#d99a23"
            elif not paused:
                state, colour = "Active", "#39a869"
            elif status.get("readiness") == "ready":
                state, colour = "Paused", "#8a8f98"
            else:
                state, colour = "Unavailable", "#d99a23"
            description = f"{kind.title()}: {state.lower()}"
            light.setStyleSheet(f"background-color: {colour}; border-radius: 5px;")
            light.setAccessibleName(description)
            light.setToolTip(description)
            label.setText(description)
            label.setToolTip(description + (" — waiting for unlock or wake" if status.get(kind + "ResumePending") else ""))
        self.paused_label.setText(f"Movement: {movement_text} · Brightness: {brightness_text}")
        reasons = status.get("suspendedReasons", [])
        if not isinstance(reasons, list): reasons = ["Suspension reason unavailable"]
        if status.get("shortcutConflict"):
            reasons = list(reasons) + [f"Restore Brightness shortcut: {status['shortcutConflict']}"]
        explanations = {
            "no-selected-output": "Choose at least one display in Movement, or enable a dimming effect and choose its displays.",
            "input-state-unknown-or-held": "Release held input. After startup or a display change, click and release on each desktop to establish input state.",
            "output-activity-unknown": "Click and release on each display’s desktop, then let input settle.",
            "output-set-mismatch": "Displays are changing. Wait for the layout to settle and review your selections.",
            "native-unavailable": "The compositor connection is unavailable. Reopen OWG; check About for supported versions.",
            "interaction-coverage-unavailable": "Reopen OWG to load its matching input observer.",
            "native-write-coverage-unavailable": "Input observation is unavailable or an unsupported input device was used. Reopen OWG to recheck readiness.",
            "quiet-period-incomplete": "Waiting for the quiet time set in Safety & alerts.",
        }
        messages = [explanations.get(str(reason), "Waiting for an unlocked, awake, active desktop." if str(reason).startswith("session-not-authoritatively-ready/") else str(reason)) for reason in reasons]
        self.reason_label.setText("Status: " + ("; ".join(messages) if messages else "Ready"))
        self.reason_label.setToolTip("; ".join(str(reason) for reason in reasons))
        self.last_result_label.setText(f"Last transaction: {transaction_summary(status)}")
        remaining = status.get("warningRemainingSeconds")
        if status.get("transactionActive") is True and isinstance(remaining, (int, float)) and not isinstance(remaining, bool) and remaining >= 0:
            self.warning_label.setText(f"Warning: {remaining:.0f} seconds remaining; Skip is available when enabled")
        else:
            self.warning_label.setText("")
        warning_visible = (status.get("transactionActive") is True and type(remaining) in (int,float) and remaining > 0)
        if warning_visible and not self._warning_visible:
            if self.settings["soundEnabled"]: self.warning_audio.play()
            if self.settings["systemNotifications"]:
                self._notification_pending = True
        # Plasma notifications create compositor windows. Never introduce one
        # during a warning, a write, or the interval in which Restore is usable.
        # Require authoritative paused/idle state, including no retained inverse.
        if not self.settings["systemNotifications"]:
            self._notification_pending = False
        if (self._notification_pending and status.get("paused") is True
                and status.get("transactionActive") is False
                and status.get("restoreAvailable") is False and not warning_visible):
            self._notification_pending = False
            if self.tray.isVisible():
                self.tray.showMessage("OLED Window Guard", "Guarding is paused. Review the dashboard for the last movement result.", QSystemTrayIcon.MessageIcon.Information, 3000)
        self._warning_visible = warning_visible
        self.save_status.setText("Settings error: " + self.settings_error if self.settings_error else "Settings save automatically. Guarding starts paused.")
        self._update_preview()
        self.overview_metrics[0].setText(f"Movement: {'waiting' if status.get('movementResumePending') else movement_text}\nBrightness: {'waiting' if status.get('brightnessResumePending') else brightness_text}")
        movement_count=sum(d.get("id") in self.settings["movementDisplays"] for d in self._displays)
        brightness_count=sum(d.get("id") in self.settings["dimmingDisplays"] for d in self._displays)
        self.overview_metrics[1].setText(f"Movement: {movement_count}\nBrightness: {brightness_count}")
        self.overview_metrics[2].setText(transaction_summary(status))
        next_check = status.get("nextMoveCheck")
        if status.get("movementResumePending"):
            self.next_move_label.setText("Next move check: waiting for unlock or wake")
        elif movement_paused:
            self.next_move_label.setText("Next move check: paused")
        elif status.get("transactionActive") is True:
            self.next_move_label.setText("Next move check: movement in progress")
        elif isinstance(next_check, dict) and type(next_check.get("remainingSeconds")) in (int, float):
            seconds = math.ceil(next_check["remainingSeconds"])
            output = next((d for d in self._displays if d.get("id") == next_check.get("outputId")), {})
            name = output.get("connector") or output.get("name") or "selected display"
            text = f"{seconds // 60}:{seconds % 60:02d}" if seconds else "waiting for safe conditions"
            self.next_move_label.setText(f"Next move check: {text} · {name}")
        else:
            self.next_move_label.setText("Next move check: waiting for readiness")
        self._refresh_action_states()
        self.statusChanged.emit(dict(status))

    def _refresh_action_states(self) -> None:
        status = self.status
        paused = status.get("paused") is not False
        movement_paused = status.get("movementPaused", paused) is not False
        ready = status.get("readiness") == "ready"
        caps = status.get("capabilities")
        caps = caps if isinstance(caps, dict) else {}
        self.pause_resume_button.setText("Start guarding" if paused else "Pause guarding")
        self.pause_resume_button.setEnabled(
            (ready and status.get("resumeReady") is True)
            if paused else status.get("canPause") is True
        )
        for kind,button in (("movement",self.movement_guard_button),("brightness",self.brightness_guard_button)):
            guard_paused=status.get(kind+"Paused",paused) is not False
            button.setText(("Start " if guard_paused else "Pause ")+kind+" guarding")
            button.setEnabled(ready and status.get(kind+"ResumeReady",status.get("resumeReady")) is True if guard_paused else status.get("canPause") is True)
            button.setToolTip("Start or pause "+kind+" guarding independently on its selected displays." if button.isEnabled() else "Choose a connected "+kind+" display and wait for compositor readiness.")
        planning_busy = status.get("planningResult", {}).get("status") in ("queued", "planning")
        selected = (self.preview_display.currentData() in self.settings["movementDisplays"]
                    and any(d.get("id") == self.preview_display.currentData() for d in self._displays))
        self.preview_display.setEnabled(status.get("transactionActive") is not True and not planning_busy)
        self.move_now_button.setEnabled(selected and ("movementActiveDisplays" not in status or self.preview_display.currentData() in status["movementActiveDisplays"]) and ready and not movement_paused and not planning_busy and status.get("movementReady") is True and status.get("transactionActive") is not True)
        self.skip_button.setEnabled(not movement_paused and status.get("transactionActive") is True and status.get("skipAvailable") is True)
        self.restore_button.setEnabled(selected and self.preview_display.currentData() == status.get("restoreOutputId") and ready and not movement_paused and not planning_busy and status.get("transactionActive") is not True and status.get("restoreAvailable") is True)
        self.restore_brightness_button.setEnabled(status.get("brightnessPaused") is not True and caps.get("restoreBrightness") is True)
        self.dimming_brightness_button.setEnabled(self.restore_brightness_button.isEnabled())
        self.preview_button.setEnabled(status.get("planningReady") is True and status.get("transactionActive") is not True and
            self.preview_display.currentData() in self.settings["movementDisplays"] and
            status.get("planningResult", {}).get("status") not in ("queued", "planning"))
        self.quit_button.setEnabled(True)
        self.tray_movement.setText(self.movement_guard_button.text()); self.tray_movement.setEnabled(self.movement_guard_button.isEnabled())
        self.tray_brightness_guard.setText(self.brightness_guard_button.text()); self.tray_brightness_guard.setEnabled(self.brightness_guard_button.isEnabled())
        self.tray_pause_resume.setText("Pause all guarding" if not paused else "Start selected guarding")
        self.tray_pause_resume.setEnabled(self.pause_resume_button.isEnabled())
        self.tray_open.setEnabled(True)
        self.tray_skip.setEnabled(self.skip_button.isEnabled())
        self.tray_restore.setEnabled(self.restore_button.isEnabled())
        restoring=status.get("restoreBrightnessOutputs", []) if status.get("brightnessPaused") is False else []
        self.tray_brightness.setEnabled(self.restore_brightness_button.isEnabled() and bool(restoring))
        dim_outputs=status.get("dimDisplayOutputs", []) if status.get("brightnessPaused") is False else []
        self.tray_dim_all.setEnabled(bool(dim_outputs))
        self.tray_dim_all.setToolTip("Dim displays with active brightness guarding and monitor dimming enabled.")
        movement_enabled=ready and not movement_paused and not planning_busy and status.get("movementReady") is True and status.get("transactionActive") is not True
        self._monitor_menu_actions(self.tray_move_actions,self.tray_move_end,"Move now", "MoveNow",
            self.settings["movementDisplays"],lambda output:movement_enabled and output in status.get("movementActiveDisplays",self.settings["movementDisplays"]))
        self._monitor_menu_actions(self.tray_brightness_actions,self.tray_brightness,"Restore brightness", "RestoreBrightness",
            self.settings["dimmingDisplays"],lambda output:self.restore_brightness_button.isEnabled() and output in restoring)
        self._monitor_menu_actions(self.tray_dim_actions,self.tray_dim_all,"Dim now", "DimDisplayNow",
            self.settings["dimmingDisplays"],lambda output:output in dim_outputs)
        for button, enabled_text, disabled_text in (
            (self.pause_resume_button,"Start or pause guarding on selected displays.","Select a movement or dimming display and wait for compositor readiness. Release held input; click and release on each desktop after startup if needed."),
            (self.move_now_button,"Start a warning on the display selected in Overview.","Start guarding, select a movement display, and wait for any current plan or warning to finish."),
            (self.skip_button,"Cancel the current warning without pausing guarding.","Skip is available during a movement or restore warning."),
            (self.restore_button,"Warn, then undo the last move if its windows still match.","Select the display of the last successful move. Manual moves or resizing can make Restore unavailable."),
            (self.restore_brightness_button,"Clear dimming temporarily on all selected displays.","Requires an armed, available dimming renderer."),
            (self.preview_button,"Calculate a plan without moving windows.","Choose a selected movement display and wait for the planner to become available.")):
            button.setToolTip(enabled_text if button.isEnabled() else disabled_text)


    def _toggle_guard(self, kind: str) -> None:
        paused=self.status.get(kind+"Paused",self.status.get("paused")) is not False
        self._send(("Resume" if paused else "Pause")+("Movement" if kind=="movement" else "Brightness"))

    def _toggle_pause(self) -> None:
        action = "Pause" if self.status.get("paused") is False else "Resume"
        self._send(action)

    def _monitor_menu_actions(self, actions, before, label, intent, selected, enabled) -> None:
        displays=[d for d in self._displays if d.get("id") in selected]
        ids={d["id"] for d in displays}
        for output in list(actions):
            if output not in ids:
                old=actions.pop(output);self.tray_menu.removeAction(old);old.deleteLater()
        for display in displays:
            output=display["id"]
            if output not in actions:
                item=QAction(self.tray_menu)
                item.triggered.connect(lambda checked=False, target=output, command=intent:self._send(command,target))
                self.tray_menu.insertAction(before,item);actions[output]=item
            name=str(display.get("name") or output)
            connector=display.get("connector")
            if connector and connector not in name:name+=f" · {connector}"
            actions[output].setText(f"{label} — {name}")
            actions[output].setEnabled(bool(enabled(output)))

    def _send(self, action: str, output_id: str | None = None) -> None:
        # Recheck at dispatch so stale/unknown UI state cannot turn into an intent.
        self._refresh_action_states()
        allowed = {"Open": True, "Quit": self.quit_button.isEnabled(), "Pause": self.pause_resume_button.isEnabled(), "Resume": self.pause_resume_button.isEnabled() and self.status.get("readiness") == "ready" and self.status.get("resumeReady") is True,
                   "PlanPreview": self.preview_button.isEnabled(), "MoveNow": self.move_now_button.isEnabled(), "Skip": self.skip_button.isEnabled(), "Restore": self.restore_button.isEnabled(), "RestoreBrightness": self.restore_brightness_button.isEnabled()}
        allowed.update(DimDisplayNow=False,DimAllDisplaysNow=self.tray_dim_all.isEnabled())
        if output_id is not None:
            actions={"MoveNow":self.tray_move_actions,"DimDisplayNow":self.tray_dim_actions,
                     "RestoreBrightness":self.tray_brightness_actions}.get(action,{})
            item=actions.get(output_id)
            allowed[action]=item is not None and item.isEnabled()
        for kind,button in (("Movement",self.movement_guard_button),("Brightness",self.brightness_guard_button)):
            allowed["Resume"+kind]=button.isEnabled() and self.status.get(kind.lower()+"Paused",self.status.get("paused")) is not False
            allowed["Pause"+kind]=button.isEnabled() and self.status.get(kind.lower()+"Paused",self.status.get("paused")) is False
        if not allowed.get(action, False): return
        if action == "Open": self.showNormal(); self.raise_(); self.activateWindow()
        try:
            payload={"outputId":output_id} if output_id is not None else {"outputId": self.preview_display.currentData()} if action in ("PlanPreview", "MoveNow", "Restore") else {}
            response = self.intent_client.submit(action,payload)
            if isinstance(response, Mapping) and response.get("ok") is True and isinstance(response.get("status"), Mapping):
                self.update_status(response["status"])
            if isinstance(response, Mapping) and response.get("ok") is False:
                self.update_status({**(response.get("status") if isinstance(response.get("status"), Mapping) else self.status), "suspendedReasons": [f"Controller intent unavailable: {response.get('reason', 'unknown')}"]})
        except Exception as exc:
            self.update_status({**self.status, "suspendedReasons": [f"Controller intent failed: {exc}"]})

    def closeEvent(self, event: Any) -> None:
        # Closing preferences only hides this window; the host app/controller lifetime is unchanged.
        event.accept()
        self.hide()


def create_application(argv: list[str] | None = None) -> QApplication:
    """Use one Qt application per process; callers own the sole controller."""
    app = QApplication.instance() or QApplication(argv or [])
    app.setApplicationName("OLED Window Guard")
    app.setDesktopFileName("oled-window-guard")
    app.setWindowIcon(application_icon())
    app.setQuitOnLastWindowClosed(False)
    return app

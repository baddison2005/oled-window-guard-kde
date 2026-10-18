"""Opt-in XDG login entry; independent of compositor movement preferences."""
from dataclasses import dataclass
from pathlib import Path
import configparser
import os
import tempfile
from .preferences import default_settings_path

ENTRY = '''[Desktop Entry]
Type=Application
Name=OLED Window Guard
Comment=Open OLED Window Guard paused at login
Exec=/usr/bin/oled-window-guard open
TryExec=/usr/bin/oled-window-guard
Icon=oled-window-guard
Terminal=false
OnlyShowIn=KDE;
Hidden=false
X-GNOME-Autostart-enabled=true
X-OLEDWindowGuard-Managed=true
'''


@dataclass(frozen=True)
class LoginState:
    enabled: bool
    editable: bool
    message: str


class LoginStartup:
    def __init__(self, config_home=None):
        home = Path(config_home) if config_home is not None else default_settings_path().parent.parent
        self.path = home / 'autostart/oled-window-guard.desktop'

    def read(self):
        if self.path.is_symlink():
            return LoginState(False, False, 'An existing startup link is managed elsewhere. Review it in KDE System Settings → Autostart.')
        if not self.path.exists():
            return LoginState(False, True, 'Open OWG paused when you sign in to KDE Plasma. Start guarding when you are ready.')
        try:
            if self.path.stat().st_size > 16384:
                raise ValueError('oversized entry')
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(self.path.read_text())
            entry = config['Desktop Entry']
            managed = (entry.get('X-OLEDWindowGuard-Managed') == 'true'
                       and entry.get('Exec') == '/usr/bin/oled-window-guard open'
                       and entry.get('Type') == 'Application')
            enabled = entry.get('Hidden', 'false').lower() != 'true' and entry.get('X-GNOME-Autostart-enabled', 'true').lower() != 'false'
            if not managed:
                return LoginState(enabled, False, 'An existing startup entry is managed elsewhere. Review it in KDE System Settings → Autostart.')
            return LoginState(enabled, True, 'Open OWG paused when you sign in to KDE Plasma. Start guarding when you are ready.')
        except (OSError, UnicodeError, configparser.Error, KeyError, ValueError):
            return LoginState(False, False, 'The existing startup entry cannot be read. Review it in KDE System Settings → Autostart.')

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Login startup must be enabled or disabled')
        state = self.read()
        if not state.editable:
            raise OSError(state.message)
        if not enabled:
            self.path.unlink(missing_ok=True)
            return self.read()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix='.oled-window-guard-', suffix='.desktop', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as output:
                output.write(ENTRY); output.flush(); os.fsync(output.fileno())
            os.replace(name, self.path)
        finally:
            Path(name).unlink(missing_ok=True)
        return self.read()

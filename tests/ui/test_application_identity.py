import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from oled_window_guard.application_identity import exclusion_aliases

class ApplicationIdentityTests(unittest.TestCase):
    def test_selected_flatpak_desktop_identity_resolves_startup_wm_class(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);apps=root/'applications';apps.mkdir()
            (apps/'com.valvesoftware.Steam.desktop').write_text('[Desktop Entry]\nName=Steam\nStartupWMClass=Steam\n')
            (apps/'unrelated.desktop').write_text('[Desktop Entry]\nStartupWMClass=Other\n')
            with patch.dict(os.environ,{'XDG_DATA_HOME':d,'XDG_DATA_DIRS':d}):
                self.assertEqual(exclusion_aliases(['com.valvesoftware.Steam']),{'com.valvesoftware.Steam':['Steam']})
                self.assertEqual(exclusion_aliases([]),{})

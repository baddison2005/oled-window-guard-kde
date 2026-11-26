"""Keep desktop upgrades free while retaining a declared native build target."""
from pathlib import Path
import re
import unittest
from oled_window_guard.dependencies import TESTED_VERSIONS


class RuntimeRequirementsTests(unittest.TestCase):
    def test_target_is_not_a_runtime_package_pin(self):
        source=(Path(__file__).resolve().parents[2]/'packaging/rpm/oled-window-guard.spec').read_text()
        requirements=re.findall(r'^Requires:\s*(.*?)\s*$',source,re.M)
        self.assertIn('plasma-integration',requirements)
        for name in ('kwin','qt6-qtbase','qt6-qtdeclarative'):
            self.assertIn(name,requirements)
            self.assertFalse(any(line.startswith(name+' ') for line in requirements))
        versions = dict(re.findall(r'^%global (owg_(?:kwin|qt)_version) (\S+)$', source, re.M))
        self.assertEqual(versions['owg_kwin_version'], TESTED_VERSIONS['kwin'])
        self.assertEqual(versions['owg_qt_version'], TESTED_VERSIONS['python3-pyside6'])
        self.assertIn('Provides:       oled-window-guard-native-kwin = %{owg_kwin_version}', source)
        self.assertIn('Provides:       oled-window-guard-native-qt = %{owg_qt_version}', source)
        self.assertIn('%global __requires_exclude_from',source)
        self.assertIn('%global __provides_exclude_from',source)


if __name__=='__main__':unittest.main()

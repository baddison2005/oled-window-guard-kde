import json
import tempfile
import unittest
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from oled_window_guard.preferences import (  # noqa: E402
    SettingsError, defaults, load_settings, migrate_example_unversioned_to_v1, save_settings, schema_metadata,
    validate_settings, default_settings_path,
)


class PreferenceTests(unittest.TestCase):
    def test_canonical_defaults_match_b01_and_start_unselected(self):
        d = defaults()
        self.assertEqual(d["paused"], True)
        self.assertFalse(d["startMinimized"])
        self.assertFalse(d["resumeAfterUnlock"])
        self.assertEqual(d["movementDisplays"], [])
        self.assertEqual(d["dimmingDisplays"], [])
        self.assertEqual(d["intervalMinutes"], 10)
        self.assertEqual(d["warningSeconds"], 10)
        self.assertEqual(d["mode"], "drift")
        self.assertEqual(d["shiftRangePercent"], 10)
        self.assertEqual(d["maximumWindows"], 4)
        self.assertEqual(d["sizeTolerance"], .10)
        self.assertEqual(d["avoidFocusedWindow"], True)
        self.assertEqual(d["quietSeconds"], 5)
        self.assertEqual(d["allowTransientOverlap"], False)
        self.assertEqual(d["dimWindows"], False)
        self.assertEqual(d["dimDisplays"], False)
        self.assertEqual(d["windowDimPercent"], 30)
        self.assertEqual(d["displayDimPercent"], 40)
        self.assertEqual(d["windowDimDelay"], 0)
        self.assertEqual(d["displayDimDelay"], 0)
        self.assertEqual(d["windowDimFade"], 0)
        self.assertEqual(d["displayDimFade"], 0)
        self.assertEqual(d["brightnessShortcutEnabled"], True)
        schema_defaults = {k: v["default"] for k, v in schema_metadata()["properties"].items() if "default" in v}
        self.assertEqual(d, schema_defaults)

    def test_missing_and_explicit_empty_selections_are_distinguishable(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            fresh = load_settings(path)
            self.assertEqual(fresh.values["dimmingDisplays"], [])
            self.assertNotIn("dimmingDisplays", fresh.present_fields)
            path.write_text('{"schemaVersion":1,"dimmingDisplays":[]}', encoding="utf-8")
            explicit = load_settings(path)
            self.assertEqual(explicit.values["dimmingDisplays"], [])
            self.assertIn("dimmingDisplays", explicit.present_fields)

    def test_corrupt_read_preserves_file_and_explicit_save_keeps_backup(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            corrupt = b'{broken json\n'
            path.write_bytes(corrupt)
            loaded = load_settings(path)
            self.assertIsNotNone(loaded.error)
            self.assertTrue(loaded.values["paused"])
            self.assertEqual(path.read_bytes(), corrupt)
            backup = save_settings(path, defaults())
            self.assertIsNotNone(backup)
            self.assertEqual(backup.read_bytes(), corrupt)
            self.assertEqual(load_settings(path).values["movementDisplays"], [])

    def test_invalid_utf8_falls_back_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            corrupt = b'{"schemaVersion":1,\xff}'
            path.write_bytes(corrupt)
            loaded = load_settings(path)
            self.assertIsNotNone(loaded.error)
            self.assertTrue(loaded.values["paused"])
            self.assertEqual(path.read_bytes(), corrupt)
            backup = save_settings(path, defaults())
            self.assertEqual(backup.read_bytes(), corrupt)

    def test_unversioned_file_requires_explicit_migration_and_save_backups_it(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            original = b'{"selectedDisplays":["DP-1"]}'
            path.write_bytes(original)
            loaded = load_settings(path)
            self.assertIn("explicit migration", loaded.error)
            self.assertEqual(path.read_bytes(), original)
            backup = save_settings(path, defaults())
            self.assertEqual(backup.read_bytes(), original)

    def test_default_xdg_path_is_resolved_without_writing(self):
        with tempfile.TemporaryDirectory() as td:
            config_home = Path(td) / "config-root"
            with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(config_home)}):
                result = default_settings_path()
            self.assertEqual(result, config_home / "oled-window-guard" / "settings.json")
            self.assertFalse(config_home.exists())
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": "relative-config"}):
            self.assertEqual(default_settings_path(), Path.home() / ".config" / "oled-window-guard" / "settings.json")

    def test_override_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            value = defaults()
            value["movementOverrides"] = {"DP-1": {"mode": "swap", "intervalMinutes": 27}}
            value["dimmingOverrides"] = {"DP-1": {"display": False, "displayDimFade": 1.5}}
            save_settings(path, value)
            loaded = load_settings(path)
            self.assertEqual(loaded.values["movementOverrides"], value["movementOverrides"])
            self.assertEqual(loaded.values["dimmingOverrides"], value["dimmingOverrides"])
            with self.assertRaises(SettingsError):
                validate_settings({**value, "dimmingOverrides": {"DP-1": {"displayDimFade": 1.25}}})
            with self.assertRaises(SettingsError):
                validate_settings({**value, "dimmingOverrides": {"DP-1": {"dimDisplays": True}}})
            with self.assertRaises(SettingsError):
                validate_settings({**value, "movementOverrides": {"DP-1": {"maximumWindows": 13}}})
            with self.assertRaises(SettingsError):
                validate_settings({**value, "movementOverrides": {"DP-1": {"layoutSourceId": "library"}}})

    def test_future_version_fails_closed_and_migration_is_explicit(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text('{"schemaVersion":99}', encoding="utf-8")
            self.assertIn("newer than supported", load_settings(path).error)
            before = path.read_bytes()
            with self.assertRaisesRegex(SettingsError, "newer than supported"):
                save_settings(path, defaults())
            self.assertEqual(path.read_bytes(), before)
        migrated = migrate_example_unversioned_to_v1({"selectedDisplays": ["DP-1"]})
        self.assertEqual(migrated["movementDisplays"], ["DP-1"])
        self.assertEqual(migrated["dimmingDisplays"], ["DP-1"])
        explicit_empty = migrate_example_unversioned_to_v1({"selectedDisplays": ["DP-1"], "dimmingDisplays": []})
        self.assertEqual(explicit_empty["dimmingDisplays"], [])
        with self.assertRaisesRegex(SettingsError, "conflict"):
            migrate_example_unversioned_to_v1({"selectedDisplays": ["DP-1"], "movementDisplays": []})
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.json"
            self.assertEqual(load_settings(missing).values["schemaVersion"], 1)
            self.assertFalse(missing.exists())

    def test_unknown_keys_and_invalid_global_values_rejected(self):
        effect_defaults = schema_metadata()["$defs"]["dimmingOverride"]["properties"]
        self.assertTrue(effect_defaults["windows"]["default"])
        self.assertTrue(effect_defaults["display"]["default"])
        with self.assertRaises(SettingsError):
            validate_settings({"notASetting": True})
        with self.assertRaises(SettingsError):
            validate_settings({"intervalMinutes": 0})
        with self.assertRaises(SettingsError):
            validate_settings({"movementDisplays": ["DP-1", "DP-1"]})


if __name__ == "__main__":
    unittest.main()

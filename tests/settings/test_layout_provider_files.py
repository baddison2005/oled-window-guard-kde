"""Exercise actual KConfig reads on disposable files, with controlled save timing.

The save fixture owns its file. All reads delegate to the real kreadconfig6;
no host kwinrc is read or written.
"""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from oled_window_guard.layout_provider import GROUP, KEYS, LayoutProvider


def values(version):
    group = "group-" + version
    return {
        "CustomLayouts": json.dumps([{"id": "layout-" + version, "name": "Third " + version,
            "x": 0, "y": 0, "width": 1 / 3, "height": 1, "groupId": group}], separators=(",", ":")),
        "CustomGroups": json.dumps([{"id": group, "name": "Group " + version}], separators=(",", ":")),
        "GroupOrder": json.dumps([group], separators=(",", ":")),
        "LayoutPadding": "2" if version == "old" else "7",
    }


def config_bytes(raw):
    return ("# fixture; preserve comments and unrelated configuration\n[Unrelated]\nKeep=literal value\n\n"
            "[" + GROUP + "]\n" + "".join(key + "=" + raw[key] + "\n" for key in KEYS)).encode()


@unittest.skipUnless(shutil.which("kreadconfig6"), "kreadconfig6 is unavailable")
class FileLayoutProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "kwinrc"
        self.old = values("old")
        self.new = values("new")
        self.file.write_bytes(config_bytes(self.old))
        self.provider = LayoutProvider(config_file=self.file, debounce_seconds=0)

    def test_real_nonempty_groups_and_revision_are_read_without_writes(self):
        original = self.file.read_bytes()
        result = self.provider.read_snapshot()
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["executionReady"])
        self.assertEqual(result["groups"][0]["layoutIds"], ["layout-old"])
        self.assertEqual(result["layouts"][0]["width"], 1 / 3)
        self.assertEqual(result["padding"], 2)
        self.assertTrue(self.provider.is_current(result["revision"]))
        self.assertEqual(self.file.read_bytes(), original)
        self.assertEqual(sorted(p.name for p in Path(self.temp.name).iterdir()), ["kwinrc"])

    def test_save_between_each_real_key_read_is_unstable_then_recovers(self):
        for boundary in range(1, 8):
            with self.subTest(after_key_read=boundary):
                self.file.write_bytes(config_bytes(self.old))
                provider = LayoutProvider(config_file=self.file, debounce_seconds=0)
                accepted = provider.read_snapshot()
                calls = 0
                def during_save(argv, timeout):
                    nonlocal calls
                    output = LayoutProvider._run(argv, timeout)
                    calls += 1
                    if calls == boundary:
                        self.file.write_bytes(config_bytes(self.new))
                    return output
                provider.runner = during_save
                unstable = provider.read_snapshot()
                self.assertEqual(calls, 8)
                self.assertEqual(unstable["status"], "unstable")
                self.assertFalse(unstable["executionReady"])
                self.assertEqual(unstable["groups"], accepted["groups"])
                self.assertEqual(self.file.read_bytes(), config_bytes(self.new))
                provider.runner = LayoutProvider._run
                self.assertFalse(provider.is_current(accepted["revision"]))
                recovered = provider.read_snapshot()
                self.assertTrue(recovered["executionReady"])
                self.assertEqual(recovered["groups"][0]["id"], "group-new")
                self.assertTrue(provider.is_current(recovered["revision"]))
                self.assertEqual(self.file.read_bytes(), config_bytes(self.new))

    def test_stable_broken_partial_saves_are_display_only_and_recover(self):
        cases = []
        malformed = dict(self.new, CustomLayouts="{incomplete")
        cases.append(malformed)
        dangling = dict(self.old, CustomGroups=self.new["CustomGroups"])
        cases.append(dangling)
        cases.append(dict(self.old, LayoutPadding="NaN"))
        for partial in cases:
            with self.subTest(partial=partial):
                self.file.write_bytes(config_bytes(self.old))
                provider = LayoutProvider(config_file=self.file, debounce_seconds=0)
                accepted = provider.read_snapshot()
                self.file.write_bytes(config_bytes(partial))
                broken = provider.read_snapshot()
                self.assertEqual(broken["status"], "invalid")
                self.assertFalse(broken["executionReady"])
                self.assertEqual(broken["layouts"], accepted["layouts"])
                self.assertFalse(provider.is_current(accepted["revision"]))
                self.assertEqual(self.file.read_bytes(), config_bytes(partial))
                self.file.write_bytes(config_bytes(self.new))
                self.assertTrue(provider.read_snapshot()["executionReady"])
                self.assertEqual(self.file.read_bytes(), config_bytes(self.new))

    def test_deletion_and_recreation_do_not_revive_a_previous_revision(self):
        accepted = self.provider.read_snapshot()
        self.file.unlink()  # fixture-owned disposable file only
        missing = self.provider.read_snapshot()
        self.assertEqual(missing["status"], "missing")
        self.assertFalse(missing["executionReady"])
        self.assertFalse(self.provider.is_current(accepted["revision"]))
        self.assertFalse(self.file.exists())
        self.file.write_bytes(config_bytes(self.new))
        self.assertTrue(self.provider.read_snapshot()["executionReady"])
        self.assertFalse(self.provider.is_current(accepted["revision"]))

    def test_atomic_file_replacement_invalidates_the_old_revision(self):
        accepted = self.provider.read_snapshot()
        replacement = self.file.with_suffix(".save")
        replacement.write_bytes(config_bytes(self.new))
        replacement.replace(self.file)
        self.assertFalse(self.provider.is_current(accepted["revision"]))
        recovered = self.provider.read_snapshot()
        self.assertTrue(self.provider.is_current(recovered["revision"]))
        self.assertEqual(self.file.read_bytes(), config_bytes(self.new))

    def test_valid_stable_intermediate_save_is_not_proof_of_save_completion(self):
        # Two matching reads can only establish observed stability. Upstream
        # supplies no transaction/commit marker: a schema-valid intermediate
        # state may legitimately be accepted before another key is saved.
        accepted = self.provider.read_snapshot()
        intermediate = dict(self.old, LayoutPadding="6")
        self.file.write_bytes(config_bytes(intermediate))
        result = self.provider.read_snapshot()
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["executionReady"])
        self.assertNotEqual(result["revision"], accepted["revision"])
        self.assertEqual(result["padding"], 6)
        self.assertEqual(result["layouts"][0]["id"], "layout-old")
        self.file.write_bytes(config_bytes(self.new))
        self.assertFalse(self.provider.is_current(result["revision"]))
        self.assertEqual(self.file.read_bytes(), config_bytes(self.new))


if __name__ == "__main__":
    unittest.main()

import json
import shutil
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from oled_window_guard.layout_provider import GROUP, KEYS, LayoutProvider  # noqa: E402


def encoded(layouts=None, groups=None, order=None, padding="0"):
    return {
        "CustomLayouts": json.dumps(layouts if layouts is not None else [], separators=(",", ":")),
        "CustomGroups": json.dumps(groups if groups is not None else [], separators=(",", ":")),
        "GroupOrder": json.dumps(order if order is not None else [], separators=(",", ":")),
        "LayoutPadding": padding,
    }


class FakeReader:
    def __init__(self, values, mutate_after=None):
        self.values = values
        self.mutate_after = mutate_after
        self.calls = []

    def __call__(self, argv, timeout):
        self.calls.append((list(argv), timeout))
        key = argv[argv.index("--key") + 1]
        if self.mutate_after is not None and len(self.calls) > self.mutate_after:
            self.values = self.mutate_after_values
        value = self.values.get(key)
        if value is None:
            return argv[argv.index("--default") + 1]
        return value


class LayoutProviderTests(unittest.TestCase):
    def make_provider(self, values, **kwargs):
        runner = FakeReader(values)
        provider = LayoutProvider(runner=runner, debounce_seconds=0, **kwargs)
        return provider, runner

    def test_valid_custom_source_has_plain_stable_records_and_hash(self):
        values = encoded(
            [{"id": "layout-left", "name": "Left", "x": 0, "y": 0,
              "width": .5, "height": 1, "groupId": "pair", "shortcutSlot": 1}],
            [{"id": "pair", "name": "Pair"}], ["halves", "pair"], "12.4",
        )
        provider, runner = self.make_provider(values)
        result = provider.read_snapshot()
        self.assertEqual(result["status"], "available")
        self.assertTrue(result["executionReady"])
        self.assertEqual(result["layouts"][0], {
            "id": "layout-left", "name": "Left", "x": 0, "y": 0,
            "width": .5, "height": 1, "groupId": "pair", "shortcutSlot": 1,
        })
        self.assertEqual(result["groups"], [{"id": "pair", "name": "Pair", "layoutIds": ["layout-left"]}])
        self.assertEqual(result["groupOrder"], ["pair"])
        self.assertEqual(result["padding"], 12)
        self.assertEqual(len(result["revision"]), 64)
        self.assertTrue(provider.is_current(result["revision"]))
        self.assertEqual(len(runner.calls), 16)
        self.assertTrue(all(call[0][call[0].index("--group") + 1] == GROUP for call in runner.calls))

    def test_absent_source_is_not_an_execution_source(self):
        provider, _ = self.make_provider({})
        result = provider.read_snapshot()
        self.assertEqual(result["status"], "missing")
        self.assertFalse(result["executionReady"])
        self.assertIsNone(result["revision"])

    def test_missing_persistent_layout_id_fails_closed_instead_of_using_slot_or_order(self):
        provider, _ = self.make_provider(encoded(
            [{"name": "Legacy", "x": 0, "y": 0, "width": .5, "height": 1,
              "groupId": "pair", "shortcutSlot": 1}],
            [{"id": "pair", "name": "Pair"}],
        ))
        result = provider.read_snapshot()
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["executionReady"])
        self.assertIn("stable ID", result["diagnostics"][0])

    def test_duplicate_ids_dangling_groups_and_invalid_geometry_reject_whole_snapshot(self):
        cases = [
            encoded([{"id": "x", "name": "a", "x": 0, "y": 0, "width": .5, "height": 1},
                     {"id": "x", "name": "b", "x": 0, "y": 0, "width": .5, "height": 1}]),
            encoded([{"id": "x", "name": "a", "x": 0, "y": 0, "width": .5, "height": 1,
                      "groupId": "gone"}]),
            encoded([{"id": "x", "name": "a", "x": 0, "y": 0, "width": .5, "height": 1.000002}]),
        ]
        for values in cases:
            with self.subTest(values=values):
                result = self.make_provider(values)[0].read_snapshot()
                self.assertEqual(result["status"], "invalid")
                self.assertFalse(result["executionReady"])

    def test_normalized_epsilon_boundary_is_accepted(self):
        layout = {"id": "edge", "name": "Edge", "x": 0, "y": 0,
                  "width": 1.000001, "height": 1}
        result = self.make_provider(encoded([layout]))[0].read_snapshot()
        self.assertEqual(result["status"], "available")

    def test_malformed_json_and_more_than_twenty_layouts_are_rejected(self):
        values = encoded()
        values["CustomLayouts"] = "{broken"
        self.assertEqual(self.make_provider(values)[0].read_snapshot()["status"], "invalid")
        many = [{"id": str(i), "name": str(i), "x": 0, "y": 0, "width": 1, "height": 1}
                for i in range(21)]
        self.assertIn("limit", self.make_provider(encoded(many))[0].read_snapshot()["diagnostics"][0])

    def test_padding_uses_js_rounding_then_clamps(self):
        for raw, expected in (("0", 0), ("200", 200), ("250", 200), ("-5", 0), ("2.5", 3)):
            result = self.make_provider(encoded(padding=raw))[0].read_snapshot()
            self.assertEqual(result["padding"], expected)

    def test_two_key_reads_detect_partial_save_and_last_good_is_ui_only(self):
        good = encoded([], [{"id": "g", "name": "Group"}])
        runner = FakeReader(good)
        provider = LayoutProvider(runner=runner, debounce_seconds=0)
        accepted = provider.read_snapshot()
        changed = encoded([], [{"id": "g", "name": "Renamed"}])
        runner.mutate_after_values = changed
        runner.mutate_after = 12
        unstable = provider.read_snapshot()
        self.assertEqual(accepted["status"], "available")
        self.assertEqual(unstable["status"], "unstable")
        self.assertFalse(unstable["executionReady"])
        self.assertEqual(unstable["groups"], accepted["groups"])
        self.assertFalse(provider.is_current(accepted["revision"]))

    def test_stable_revision_change_during_warning_fails_freshness_check(self):
        runner = FakeReader(encoded([], [{"id": "g", "name": "Before"}]))
        provider = LayoutProvider(runner=runner, debounce_seconds=0)
        revision = provider.read_snapshot()["revision"]
        runner.values = encoded([], [{"id": "g", "name": "After"}])
        self.assertFalse(provider.is_current(revision))
        self.assertTrue(provider.read_snapshot()["executionReady"])

    def test_kwinrc_bytes_are_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            kwinrc = Path(td) / "kwinrc"
            original = b"[Script-windowlayouts]\nCustomLayouts=[]\n# keep exact\n"
            kwinrc.write_bytes(original)
            provider, _ = self.make_provider(encoded(), config_file=kwinrc)
            provider.read_snapshot()
            self.assertEqual(kwinrc.read_bytes(), original)

    @unittest.skipUnless(shutil.which("kreadconfig6"), "kreadconfig6 is unavailable")
    def test_real_kreadconfig_reads_temporary_kwinrc_without_modifying_it(self):
        with tempfile.TemporaryDirectory() as td:
            kwinrc = Path(td) / "kwinrc"
            original = (b"[Script-windowlayouts]\nCustomLayouts=[]\nCustomGroups=[]\n"
                        b"GroupOrder=[]\nLayoutPadding=9\n# preserve exact bytes\n")
            kwinrc.write_bytes(original)
            result = LayoutProvider(config_file=kwinrc, debounce_seconds=0).read_snapshot()
            self.assertEqual(result["status"], "available")
            self.assertEqual(result["padding"], 9)
            self.assertEqual(kwinrc.read_bytes(), original)

    def test_kreadconfig_argv_uses_literal_arguments_and_timeout(self):
        provider, runner = self.make_provider(encoded())
        provider.read_snapshot()
        argv, timeout = runner.calls[0]
        self.assertEqual(argv[:2], ["kreadconfig6", "--file"])
        self.assertEqual(argv[argv.index("--key") + 1], KEYS[0])
        self.assertEqual(timeout, 1.5)


if __name__ == "__main__":
    unittest.main()

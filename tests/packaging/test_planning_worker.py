"""Worker bundle dependency and production/probe separation checks."""
from pathlib import Path
import tempfile
import unittest

from tools.build_planning_worker import build


class PlanningWorkerBuildTests(unittest.TestCase):
    def test_production_bundle_is_repeatable_and_excludes_probe_stall(self):
        production = build()
        self.assertEqual(production, build())
        self.assertNotIn("probeDelayMs", production)
        self.assertIn("probeDelayMs", build(probe=True))
        self.assertNotIn(".pragma library", production)
        self.assertNotIn('.import "', production)

    def fixture(self, sources):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        for name, source in sources.items():
            (root / name).write_text(source)
        return root

    def test_shared_dependency_emitted_once_before_both_consumers(self):
        root = self.fixture({
            "planning_worker_entry.js": '.import "a.js" as A\n.import "b.js" as B\nfunction processPlanningRequest(message) { return A.read()+B.read(); }',
            "a.js": '.import "shared.js" as Shared\nfunction read() { return Shared.read(); }',
            "b.js": '.import "shared.js" as Shared\nfunction read() { return Shared.read(); }',
            "shared.js": 'function read() { return 1; }',
        })
        bundle = build(root)
        marker = 'OWGModules["shared.js"] ='
        self.assertEqual(bundle.count(marker), 1)
        self.assertLess(bundle.index(marker), bundle.index('OWGModules["a.js"] ='))
        self.assertLess(bundle.index(marker), bundle.index('OWGModules["b.js"] ='))

    def test_dependency_cycle_rejected(self):
        root = self.fixture({
            "planning_worker_entry.js": '.import "a.js" as A\n',
            "a.js": '.import "planning_worker_entry.js" as Entry\n',
        })
        with self.assertRaisesRegex(ValueError, "cyclic"):
            build(root)

    def test_dependency_cannot_escape_core_directory(self):
        root = self.fixture({"planning_worker_entry.js": '.import "../outside.js" as Outside\n'})
        with self.assertRaisesRegex(ValueError, "invalid"):
            build(root)

    def test_missing_dependency_rejected(self):
        root = self.fixture({"planning_worker_entry.js": '.import "missing.js" as Missing\n'})
        with self.assertRaises(FileNotFoundError):
            build(root)


if __name__ == "__main__":
    unittest.main()

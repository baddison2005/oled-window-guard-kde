import subprocess
import unittest

from oled_window_guard.updates import package_update_status


class UpdateStatusTests(unittest.TestCase):
    def test_installed_and_uninstalled_status(self):
        result=package_update_status(runner=lambda argv:"0.1.0-1.fc44")
        self.assertTrue(result["installed"])
        self.assertIn("0.1.0-1.fc44",result["message"])
        self.assertIn("install with DNF",result["message"])
        self.assertIn("github.com/baddison2005/oled-window-guard-kde/releases", result["message"])
        def missing(argv): raise subprocess.CalledProcessError(1,argv)
        result=package_update_status(runner=missing)
        self.assertFalse(result["installed"])
        self.assertIn("Development checkout",result["message"])


if __name__ == "__main__":
    unittest.main()

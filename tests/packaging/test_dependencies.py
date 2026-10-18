import unittest

from oled_window_guard.dependencies import check_dependencies


class DependenciesTests(unittest.TestCase):
    def test_tested_matrix_and_missing_dependency(self):
        expected={"kwin":"6.7.5","plasma-workspace":"6.7.5","python3-pyside6":"6.11.2"}
        runner=lambda argv: expected[argv[-1]]
        which=lambda command: "/usr/bin/" + command
        checked=check_dependencies(runner=runner,which=which,qt_version="6.11.2")
        self.assertTrue(checked["supportedMatrix"])
        changed=check_dependencies(runner=runner,which=lambda c: None if c=="kreadconfig6" else which(c),
                                   qt_version="6.11.2")
        self.assertFalse(changed["supportedMatrix"])
        self.assertIn("missing command: kreadconfig6",changed["issues"])

    def test_unavailable_or_newer_kwin_is_unverified(self):
        def runner(argv):
            if argv[-1]=="kwin": return "6.8.0"
            return {"plasma-workspace":"6.7.5","python3-pyside6":"6.11.2"}[argv[-1]]
        result=check_dependencies(runner=runner,which=lambda c:"/bin/"+c,qt_version="6.11.2")
        self.assertFalse(result["supportedMatrix"])
        self.assertTrue(any("kwin 6.8.0" in issue for issue in result["issues"]))


if __name__ == "__main__":
    unittest.main()

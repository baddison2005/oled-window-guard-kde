"""A skipped or failed warning must not count as physical input cancellation."""
import unittest
from tools.desktop_rpm_smoke import verify_cancelled


class RpmSmokeTests(unittest.TestCase):
    def test_native_input_cancellation_without_inverse_is_accepted(self):
        verify_cancelled([dict(kind='movement-result', value=dict(warningSeen=True,
            result='cancelled: native-input', restoreAvailable=False))])

    def test_skips_missing_warnings_inverses_and_harness_errors_are_refused(self):
        valid = dict(warningSeen=True, result='cancelled: native-input', restoreAvailable=False)
        for change in (dict(result='skipped'), dict(warningSeen=False), dict(restoreAvailable=True)):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                verify_cancelled([dict(kind='movement-result', value=dict(valid, **change))])
        for kind in ('error', 'operator-warning-missed'):
            with self.subTest(kind=kind), self.assertRaises(RuntimeError):
                verify_cancelled([dict(kind='movement-result', value=valid), dict(kind=kind, value='failed')])


if __name__ == '__main__':
    unittest.main()

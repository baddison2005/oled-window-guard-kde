"""Release discovery, package trust boundaries and interrupted upgrade behavior."""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from oled_window_guard import updates as U
from oled_window_guard.upgrade_worker import coordinate


class UpdateStatusTests(unittest.TestCase):
    def setUp(self):
        self.data = b'fake RPM fixture'
        self.offer = dict(version='1.0.2', release='1.fc44', name='oled-window-guard-1.0.2-1.fc44.x86_64.rpm',
            url=U.REPOSITORY + '/releases/download/v1.0.2/oled-window-guard-1.0.2-1.fc44.x86_64.rpm',
            sha256=hashlib.sha256(self.data).hexdigest(), size=len(self.data))
        self.current = '1.0.0-1.fc44'

    def runner(self, argv):
        if argv[:3] == ['rpm', '-qp', '--qf']:
            return '\n'.join([U.PACKAGE, '1.0.2', '1.fc44', 'x86_64', '0'])
        if argv[:3] == ['rpm', '-qp', '--requires']:
            return 'kwin = 6.7.5\nqt6-qtbase = 6.11.2\nqt6-qtdeclarative = 6.11.2'
        if argv[:2] == ['rpm', '-V']:
            return ''
        if argv[-1] == U.PACKAGE:
            return self.current
        return '6.7.5' if argv[-1] == 'kwin' else '6.11.2'

    def release(self, **changes):
        result = dict(tag_name='v1.0.2', draft=False, prerelease=False, assets=[dict(name=self.offer['name'],
            browser_download_url=self.offer['url'], digest='sha256:' + self.offer['sha256'], size=self.offer['size'])])
        result.update(changes)
        return result

    def check(self, release=None, **kwargs):
        return U.check_for_updates(runner=self.runner, fetch=lambda: release or self.release(), machine='x86_64',
            os_release={'ID': 'fedora', 'VERSION_ID': '44'}, **kwargs)

    def stage(self, path):
        path.chmod(0o700)
        (path / self.offer['name']).write_bytes(self.data)
        (path / 'offer.json').write_text(json.dumps(self.offer))

    def test_local_query_does_not_contact_github_and_missing_rpm_is_explicit(self):
        with patch.object(U, 'fetch_latest', side_effect=AssertionError('no network')):
            self.assertEqual(U.package_update_status(runner=self.runner)['state'], 'unchecked')
        def missing(_): raise subprocess.CalledProcessError(1, 'rpm')
        result = U.package_update_status(runner=missing)
        self.assertFalse(result['installed'])
        self.assertIn('Development checkout', result['message'])

    def test_new_equal_older_and_rpm_revision_comparison(self):
        result = self.check()
        self.assertEqual(result['state'], 'available')
        self.assertEqual(result['offer'], self.offer)
        for current in ('1.0.2-1.fc44', '1.0.2-2.fc44', '1.1.0-1.fc44'):
            self.current = current
            result = self.check()
            self.assertEqual(result['state'], 'current')
            self.assertIsNone(result['offer'])
        self.current = '0.1.0-27.fc44'
        self.assertEqual(self.check()['state'], 'available')

    def test_failed_remote_check_never_claims_up_to_date(self):
        def fail(): raise OSError('network unavailable')
        result = U.check_for_updates(runner=self.runner, fetch=fail, machine='x86_64',
                                    os_release={'ID': 'fedora', 'VERSION_ID': '44'})
        self.assertEqual(result['state'], 'error')
        self.assertIsNone(result['offer'])
        self.assertIn('Could not check GitHub', result['message'])

    def test_rejects_prerelease_draft_foreign_assets_duplicates_and_missing_digest(self):
        for release in (self.release(prerelease=True), self.release(draft=True), self.release(tag_name='v1.0.2-rc1'),
                self.release(assets=[]), self.release(assets=self.release()['assets'] * 2),
                self.release(assets=[dict(self.release()['assets'][0], digest=None)]),
                self.release(assets=[dict(self.release()['assets'][0], browser_download_url='https://example.org/package.rpm')])):
            with self.subTest(release=release):
                result = self.check(release)
                self.assertEqual(result['state'], 'error')
                self.assertIsNone(result['offer'])

    def test_rejects_other_platform_and_non_https_redirects(self):
        self.assertEqual(U.check_for_updates(runner=self.runner, fetch=lambda: self.release(), machine='aarch64',
            os_release={'ID': 'fedora', 'VERSION_ID': '44'})['state'], 'error')
        for url in ('http://github.com/a', 'https://github.com.evil.invalid/a', 'https://user@github.com/a',
                    'https://github.com:8443/a', 'file:///tmp/a'):
            with self.assertRaises(ValueError): U.check_https_url(url)

    def test_verified_download_and_cleanup_of_corrupt_or_cancelled_download(self):
        folder = U.download_update(self.offer, opener=lambda _: io.BytesIO(self.data), runner=self.runner)
        try:
            self.assertEqual(U.validate_rpm(folder, runner=self.runner)[0], self.offer)
        finally:
            import shutil
            shutil.rmtree(folder)
        event = threading.Event(); event.set()
        for data, cancel in ((self.data[:-1], None), (b'x' * (len(self.data) + 1), None), (self.data, event)):
            with tempfile.TemporaryDirectory() as parent:
                folder = Path(parent) / 'download'
                folder.mkdir(mode=0o700)
                with patch.object(U.tempfile, 'mkdtemp', return_value=str(folder)), self.assertRaises(ValueError):
                    U.download_update(self.offer, opener=lambda _: io.BytesIO(data), runner=self.runner, cancel=cancel)
                self.assertFalse(folder.exists())

    def test_tampered_hash_identity_runtime_or_downgrade_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); self.stage(folder)
            (folder / self.offer['name']).write_bytes(b'x' * len(self.data))
            with self.assertRaisesRegex(ValueError, 'checksum'): U.validate_rpm(folder, runner=self.runner)
            self.stage(folder)
            def wrong_identity(argv):
                return self.runner(argv).replace(U.PACKAGE, 'other-app') if '--qf' in argv and '-qp' in argv else self.runner(argv)
            with self.assertRaisesRegex(ValueError, 'identity'): U.validate_rpm(folder, runner=wrong_identity)
            def wrong_runtime(argv):
                return '6.8.0' if argv[-1] == 'kwin' else self.runner(argv)
            with self.assertRaisesRegex(ValueError, 'kwin'): U.validate_rpm(folder, runner=wrong_runtime)
            self.current = '1.0.3-1.fc44'
            with self.assertRaisesRegex(ValueError, 'not newer'): U.validate_rpm(folder, runner=self.runner)

    def test_worker_preserves_settings_and_restarts_after_success_cancel_and_failure(self):
        for code in (0, 126, 1):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as temp:
                folder = Path(temp); self.stage(folder); self.current = '1.0.0-1.fc44'
                config = folder / 'user-settings.json'; config.write_bytes(b'{"custom":"keep"}\n')
                events = []
                class Bridge:
                    def quit(self, pid): events.append(('quit', pid))
                    def wait_closed(self): events.append(('closed',))
                def install(_):
                    self.assertEqual(events, [('quit', 42), ('closed',)])
                    events.append(('install',))
                    if code == 0: self.current = '1.0.2-1.fc44'
                    return code
                result = coordinate(folder, 42, runner=self.runner, bridge=Bridge(), installer=install,
                    restart=lambda _: events.append(('reopen',)), settings_paths=[config, folder / 'absent-login'])
                self.assertEqual(result['result'], {0:'success', 126:'cancelled', 1:'failed'}[code])
                self.assertEqual(events[-1], ('reopen',))
                self.assertEqual(config.read_bytes(), b'{"custom":"keep"}\n')
                self.assertEqual((folder / 'settings-0.before').read_bytes(), config.read_bytes())
                self.assertEqual(U.read_upgrade_result(folder / 'result.json'), result['message'])

    def test_installer_uses_authorized_dnf_without_shell_or_repository_updates(self):
        from oled_window_guard.upgrade_worker import install_rpm
        from types import SimpleNamespace
        with patch('oled_window_guard.upgrade_worker.subprocess.run', return_value=SimpleNamespace(returncode=126)) as run:
            self.assertEqual(install_rpm(Path('/tmp/package.rpm')), 126)
        self.assertEqual(run.call_args.args[0], ['/usr/bin/pkexec', '/usr/bin/dnf', '--disablerepo=*',
            '--setopt=localpkg_gpgcheck=False', '-y', 'install', '/tmp/package.rpm'])
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_worker_preflight_failure_never_quits_or_installs(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp); self.stage(folder)
            (folder / self.offer['name']).write_bytes(b'bad')
            with patch('oled_window_guard.upgrade_worker.SessionBridge') as bridge:
                result = coordinate(folder, 42, runner=self.runner,
                    installer=lambda _: self.fail('must not install'), restart=lambda _: self.fail('must not restart'))
            bridge.assert_not_called()
            self.assertEqual(result['result'], 'failed')


if __name__ == '__main__':
    unittest.main()

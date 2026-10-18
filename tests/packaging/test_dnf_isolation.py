"""DNF acceptance must never use host transaction paths or desktop buses."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tests.integration import run_dnf_acceptance as runner


class DnfIsolationTests(unittest.TestCase):
    def test_command_targets_private_database_cache_repositories_and_disables_plugins(self):
        command=runner.dnf()
        self.assertIn('--installroot='+str(runner.ROOT/'installroot'),command)
        for option,name in (('cachedir','cache'),('logdir','logs'),('reposdir','repos')):
            self.assertIn('--setopt='+option+'='+str(runner.ROOT/name),command)
        self.assertIn('--repo=fedora,updates',command)
        self.assertIn('--no-plugins',command)
        self.assertNotIn('--no-gpgchecks',command)
        self.assertNotIn('--nodeps',command)

    def test_environment_does_not_export_normal_desktop_buses(self):
        with tempfile.TemporaryDirectory(prefix='owg-dnf-test-') as folder,patch.object(runner,'ROOT',Path(folder)), \
             patch.dict(os.environ,{'DBUS_SESSION_BUS_ADDRESS':'host-session','DBUS_SYSTEM_BUS_ADDRESS':'host-system','WAYLAND_DISPLAY':'host-display','DISPLAY':':0'}):
            env=runner.environment()
            for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','WAYLAND_DISPLAY','DISPLAY'):
                self.assertNotIn(key,env)
            self.assertEqual(env['XDG_RUNTIME_DIR'],str(Path(folder)/'runtime'))

    def test_redirected_installroot_is_refused_before_transactions(self):
        with tempfile.TemporaryDirectory(prefix='owg-dnf-test-') as folder:
            root=Path(folder);(root/'elsewhere').mkdir();(root/'installroot').symlink_to(root/'elsewhere',target_is_directory=True)
            with patch.object(runner,'ROOT',root),self.assertRaisesRegex(RuntimeError,'symlinks'):
                runner.environment()


if __name__=='__main__':unittest.main()

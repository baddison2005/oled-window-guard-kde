from __future__ import annotations

import json
import hashlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools import install_user


ROOT = Path(__file__).resolve().parents[2]


class UserInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        base = Path(self.temp.name)
        self.data = base / "data"
        self.bin = base / "bin"
        self.config = base / "config"
        self.settings = self.config / "oled-window-guard/settings.json"
        self.settings.parent.mkdir(parents=True)
        self.settings.write_text('{"paused": false, "keep": "user"}\n')
        self.no_active = patch.object(install_user, "companion_active", return_value=False)
        self.no_active.start()
        self.no_deps = patch.object(install_user, "check_dependencies")
        self.no_deps.start()

    def tearDown(self):
        self.no_deps.stop()
        self.no_active.stop()
        self.temp.cleanup()

    def install(self, autostart=False):
        install_user.install(ROOT, self.data, self.bin, autostart)

    def test_fresh_install_is_paused_and_autostart_is_opt_in(self):
        self.install()
        self.assertFalse((self.data / install_user.AUTOSTART_REL).exists())
        self.assertEqual(json.loads((ROOT / "schema/settings.schema.json").read_text())
                         ["properties"]["paused"]["default"], True)
        command = (self.bin / install_user.APP_ID).read_text()
        self.assertIn(str(self.data / "oled-window-guard/app"), command)
        self.assertTrue((self.data / "oled-window-guard/app/assets/icons/oled-window-guard.png").is_file())
        self.assertTrue((self.data / "oled-window-guard/app/schema/settings.schema.json").is_file())
        app=self.data / "oled-window-guard/app"
        self.assertEqual((app/'LICENSE').read_bytes(),(ROOT/'LICENSE').read_bytes())
        self.assertEqual((app/'schema/LICENSE').read_bytes(),(ROOT/'packaging/licenses/MIT.txt').read_bytes())
        package=self.data / "kwin/scripts/oled-window-guard-adapter"
        self.assertEqual((package/'COPYING').read_bytes(),(ROOT/'LICENSE').read_bytes())
        self.assertEqual(json.loads((package/'metadata.json').read_text())['KPlugin']['License'],'GPL-3.0-or-later')

    def test_kpackage_contains_resolved_core_and_production_worker(self):
        self.install()
        package=self.data / "kwin/scripts/oled-window-guard-adapter/contents/ui"
        self.assertTrue((package / "PlanningController.qml").is_file())
        self.assertEqual((package / "core/planning_dispatch.js").read_bytes(),
                         (ROOT / "core/planning_dispatch.js").read_bytes())
        self.assertNotIn("probeDelayMs",(package / "planning_worker.js").read_text())
        self.assertFalse(json.loads((package.parent.parent / "metadata.json").read_text())
                         ["KPlugin"]["EnabledByDefault"])

    def native_fixture(self):
        module=Path(self.temp.name)/"native"
        directory=module/"org/oledwindowguard/Native"
        directory.mkdir(parents=True)
        binary=b"\x7fELF\x02synthetic packaging fixture; never loaded"
        (directory/"libowg_native_observer.so").write_bytes(binary)
        shutil.copy2(ROOT/"native/observer/qmldir",directory/"qmldir")
        info={"schema":1,"protocol":1,"testInjection":False,"kwinVersion":"6.7.5","qtVersion":"6.11.2",
              "sourceSHA256":hashlib.sha256((ROOT/"native/observer/observer.cpp").read_bytes()).hexdigest(),
              "pluginSHA256":hashlib.sha256(binary).hexdigest()}
        (module/"build-info.json").write_text(json.dumps(info))
        return module

    def test_native_bundle_installs_and_uninstalls_owned_binary(self):
        module=self.native_fixture()
        install_user.install(ROOT,self.data,self.bin,native_module=module)
        binary=next((self.data/"kwin/scripts/oled-window-guard-adapter/contents/ui/native").glob("*/org/oledwindowguard/Native/libowg_native_observer.so"))
        self.assertEqual(binary.read_bytes(),(module/"org/oledwindowguard/Native/libowg_native_observer.so").read_bytes())
        manifest=json.loads((self.data/install_user.MANIFEST_REL).read_text())
        self.assertIn(str(binary),manifest["files"])
        install_user.uninstall(self.data,self.bin)
        self.assertFalse(binary.exists())
        self.assertTrue(self.settings.exists())

    def test_altered_native_build_is_refused_before_install_writes(self):
        module=self.native_fixture()
        (module/"org/oledwindowguard/Native/libowg_native_observer.so").write_bytes(b"altered")
        with self.assertRaisesRegex(ValueError,"altered native"):
            install_user.install(ROOT,self.data,self.bin,native_module=module)
        self.assertFalse((self.bin/install_user.APP_ID).exists())
        self.assertFalse((self.data/install_user.MANIFEST_REL).exists())

    def test_native_upgrade_uses_distinct_library_path_and_module_uri(self):
        module=self.native_fixture()
        install_user.install(ROOT,self.data,self.bin,native_module=module)
        package=self.data/"kwin/scripts/oled-window-guard-adapter/contents/ui"
        first=next((package/"native").glob("*/org/oledwindowguard/Native/libowg_native_observer.so"))
        first_descriptor=first.with_name("qmldir").read_text()
        binary=module/"org/oledwindowguard/Native/libowg_native_observer.so"
        binary.write_bytes(binary.read_bytes()+b"different implementation")
        info=json.loads((module/"build-info.json").read_text())
        info["pluginSHA256"]=hashlib.sha256(binary.read_bytes()).hexdigest()
        (module/"build-info.json").write_text(json.dumps(info))
        install_user.install(ROOT,self.data,self.bin,native_module=module)
        second=next((package/"native").glob("*/org/oledwindowguard/Native/libowg_native_observer.so"))
        self.assertNotEqual(first,second)
        self.assertFalse(first.exists())
        self.assertNotEqual(first_descriptor,second.with_name("qmldir").read_text())
        self.assertIn("./"+str(second.parent.relative_to(package)),(package/"NativeObservation.qml").read_text())

    def test_runtime_update_with_same_native_binary_uses_distinct_uri(self):
        from tools import build_kwin_package as builder
        module=self.native_fixture()
        first=builder.package_files(native_module=module)
        worker=builder.build(ROOT/'core')
        with patch.object(builder,'build',return_value=worker+'\n// worker upgrade fixture\n'):
            second=builder.package_files(native_module=module)
        def descriptor(files):
            main=json.loads(files[Path('contents/runtime-build.json')])['main']
            directory=Path('contents')/Path(main).parent
            return next(content for path,content in files.items()
                        if path.is_relative_to(directory) and path.name=='qmldir')
        self.assertNotEqual(descriptor(first),descriptor(second))
        self.assertEqual([v for p,v in first.items() if p.suffix=='.so'],
                         [v for p,v in second.items() if p.suffix=='.so'])
        loader=first[Path('contents/ui/NativeObservation.qml')].decode()
        self.assertIn('throw packagedError',loader)
        self.assertNotIn('import org.oledwindowguard.Native 1.0',loader)

    def test_probe_build_cannot_be_packaged_as_production(self):
        module=self.native_fixture()
        info=json.loads((module/"build-info.json").read_text());info["testInjection"]=True
        (module/"build-info.json").write_text(json.dumps(info))
        with self.assertRaisesRegex(ValueError,"invalid or altered"):
            install_user.install(ROOT,self.data,self.bin,native_module=module)
        self.assertFalse((self.bin/install_user.APP_ID).exists())

    def test_opt_out_native_upgrade_removes_owned_binary(self):
        install_user.install(ROOT,self.data,self.bin,native_module=self.native_fixture())
        self.install()
        self.assertFalse((self.data/"kwin/scripts/oled-window-guard-adapter/contents/ui/native/build-info.json").exists())

    def test_repeated_install_is_idempotent_and_opt_in_autostart_runs_open(self):
        self.install(autostart=True)
        manifest = (self.data / install_user.MANIFEST_REL).read_bytes()
        self.install(autostart=True)
        self.assertEqual(manifest, (self.data / install_user.MANIFEST_REL).read_bytes())
        startup = (self.data / install_user.AUTOSTART_REL).read_text()
        self.assertIn("Exec=" + str(self.bin / install_user.APP_ID) + " open", startup)

    def test_opt_out_upgrade_removes_only_owned_autostart(self):
        self.install(autostart=True)
        self.install(autostart=False)
        self.assertFalse((self.data / install_user.AUTOSTART_REL).exists())

    def test_uninstall_preserves_settings_and_modified_owned_file(self):
        self.install(autostart=True)
        desktop = self.data / "applications/oled-window-guard.desktop"
        desktop.write_text("user-edited\n")
        removed = install_user.uninstall(self.data, self.bin)
        self.assertNotIn(str(desktop), removed)
        self.assertEqual(desktop.read_text(), "user-edited\n")
        self.assertEqual(self.settings.read_text(), '{"paused": false, "keep": "user"}\n')
        self.assertFalse((self.data / install_user.AUTOSTART_REL).exists())

    def test_manifest_cannot_claim_settings_or_out_of_scope_files(self):
        manifest_path = self.data / install_user.MANIFEST_REL
        manifest_path.parent.mkdir(parents=True)
        manifest_path.write_text(json.dumps({"format": 1, "files": {
            str(self.settings): {"sha256": install_user.digest(self.settings.read_bytes()), "mode": 0o600}
        }}))
        with self.assertRaisesRegex(install_user.InstallError, "out-of-scope"):
            install_user.uninstall(self.data, self.bin)
        self.assertTrue(self.settings.exists())

    def test_upgrade_refuses_active_companion(self):
        install_user.install(ROOT, self.data, self.bin)
        with patch.object(install_user, "companion_active", return_value=True):
            with self.assertRaisesRegex(install_user.InstallError, "active"):
                install_user.install(ROOT, self.data, self.bin)

    def test_partial_failure_rolls_back_written_files(self):
        original_atomic = install_user._atomic_write
        writes = 0

        def fail_after_some(path, content, mode):
            nonlocal writes
            writes += 1
            if writes == 4:
                raise OSError("injected fixture failure")
            original_atomic(path, content, mode)

        with patch.object(install_user, "_atomic_write", side_effect=fail_after_some):
            with self.assertRaisesRegex(install_user.InstallError, "rolled back"):
                self.install()
        self.assertFalse((self.data / install_user.MANIFEST_REL).exists())
        self.assertFalse((self.bin / install_user.APP_ID).exists())

    def test_unowned_destination_is_never_replaced(self):
        self.bin.mkdir(parents=True)
        command = self.bin / install_user.APP_ID
        command.write_text("user command\n")
        with self.assertRaisesRegex(install_user.InstallError, "not owned"):
            self.install()
        self.assertEqual(command.read_text(), "user command\n")

    def test_symlinked_payload_parent_cannot_redirect_outside_xdg_root(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        applications = self.data / "applications"
        applications.parent.mkdir(parents=True)
        applications.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(install_user.InstallError, "symlink or alias"):
            self.install()
        self.assertFalse((outside / "oled-window-guard.desktop").exists())

    def test_dependency_failure_is_reported_before_mutation(self):
        with patch.object(install_user, "check_dependencies", side_effect=install_user.InstallError("missing dependency")):
            with self.assertRaisesRegex(install_user.InstallError, "missing dependency"):
                install_user.install(ROOT, self.data, self.bin)
        self.assertFalse(self.data.exists())

    def test_live_service_probe_and_failure_are_fail_closed(self):
        self.no_active.stop()
        with patch.object(install_user.shutil, "which", return_value="/usr/bin/busctl"):
            with patch.object(install_user.subprocess, "run", return_value=SimpleNamespace(
                    returncode=0, stdout="org.oledwindowguard.Controller1 123 user\n")) as run:
                self.assertTrue(install_user.companion_active())
                self.assertEqual(run.call_args.kwargs["timeout"], 3)
            with patch.object(install_user.subprocess, "run", side_effect=subprocess.TimeoutExpired("busctl", 3)):
                with self.assertRaisesRegex(install_user.InstallError, "cannot determine"):
                    install_user.companion_active()
        with patch.object(install_user.shutil, "which", return_value=None):
            with self.assertRaisesRegex(install_user.InstallError, "unavailable"):
                install_user.companion_active()


if __name__ == "__main__":
    unittest.main()

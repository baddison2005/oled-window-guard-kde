"""Reject RPM payloads missing the reload runtime or carrying stale native hashes."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tests.integration.run_native_rpm_install import verify_payload


class RpmPayloadTests(unittest.TestCase):
    def payload(self,usr):
        package=usr/'share/kwin/scripts/oled-window-guard-adapter'
        ui=package/'contents/ui';runtime=package/'contents/build_test/ui'
        for tree in (ui,runtime):
            tree.mkdir(parents=True)
            for name in ('main.qml','RuntimeController.qml','GuardedWriter.qml','OrderedGuardedWriter.qml','WarningSurface.qml','planning_worker.js'):
                (tree/name).write_text('property bool enabled: false')
            native=tree/'native';binary=native/'hash/org/oledwindowguard/Native/libowg_native_observer.so'
            binary.parent.mkdir(parents=True);binary.write_bytes(b'\x7fELF-test-payload')
            (native/'build-info.json').write_text(json.dumps({'testInjection':False,'pluginSHA256':hashlib.sha256(binary.read_bytes()).hexdigest()}))
        (package/'metadata.json').write_text(json.dumps({'KPlugin':{'EnabledByDefault':False}}))
        (package/'contents/runtime-build.json').write_text(json.dumps({'main':'build_test/ui/main.qml'}))
        return package

    def test_immutable_runtime_and_shipped_hashes_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            usr=Path(folder);package=self.payload(usr);verify_payload(usr,immutable_required=True)
            runtime=package/'contents/runtime-build.json';runtime.unlink()
            with self.assertRaisesRegex(RuntimeError,'Immutable'):verify_payload(usr,immutable_required=True)

    def test_runtime_native_hash_and_runtime_component_cannot_be_missing(self):
        with tempfile.TemporaryDirectory() as folder:
            usr=Path(folder);package=self.payload(usr)
            tree=package/'contents/build_test/ui';(tree/'WarningSurface.qml').unlink()
            with self.assertRaisesRegex(RuntimeError,'component missing'):verify_payload(usr,immutable_required=True)
            (tree/'WarningSurface.qml').write_text('test')
            binary=next((tree/'native').rglob('*.so'));binary.write_bytes(b'altered')
            with self.assertRaisesRegex(RuntimeError,'hash/provenance'):verify_payload(usr,immutable_required=True)


if __name__=='__main__':unittest.main()

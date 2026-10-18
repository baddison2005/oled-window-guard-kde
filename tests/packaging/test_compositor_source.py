"""GPL bundles must contain controller source, notices and reproducible builds."""
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from tools.build_compositor_source import build,ROOT
from tools.build_planning_worker import build as worker


class CompositorSourceTests(unittest.TestCase):
    def test_complete_bundle_rebuilds_worker_and_carries_controller_and_licenses(self):
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);archive=temp/'source.tar.gz';build(archive)
            with tarfile.open(archive) as bundle:
                names=bundle.getnames()
                self.assertTrue(any(n.endswith('/oled_window_guard/session.py') for n in names))
                self.assertTrue(any(n.endswith('/assets/icons/generate-icon.swift') for n in names))
                self.assertFalse(any('Evaluation' in n or '.proposed.' in n for n in names))
                for relative in ('LICENSE', 'NOTICE', 'SOURCE.md', 'packaging/licenses/MIT.txt',
                                 'packaging/rpm/oled-window-guard.spec'):
                    self.assertIn('oled-window-guard-source/' + relative, names)
                self.assertTrue(any(n.endswith('/schema/settings.schema.json') for n in names))
                self.assertTrue(any(n.endswith('/interfaces/CONTROLLER_PROTOCOL.md') for n in names))
                self.assertTrue(any(n.endswith('/packaging/licenses/native-header-notices.json') for n in names))
                bundle.extractall(temp/'extracted',filter='data')
            source=temp/'extracted/oled-window-guard-source'
            result=temp/'worker.js'
            subprocess.run([sys.executable,str(source/'tools/build_planning_worker.py'),str(result)],cwd=source,check=True)
            self.assertEqual(result.read_text(),worker())
            manifest=json.loads((source/'SOURCE-MANIFEST.json').read_text())
            import hashlib
            for name, checksum in manifest['sha256'].items():
                self.assertEqual(hashlib.sha256((source/name).read_bytes()).hexdigest(),checksum)
            package=temp/'kpackage'
            subprocess.run([sys.executable,str(source/'tools/build_kwin_package.py'),str(package)],cwd=source,check=True)
            self.assertEqual(json.loads((package/'metadata.json').read_text())['KPlugin']['License'],'GPL-3.0-or-later')
            self.assertEqual((package/'COPYING').read_bytes(),(source/'LICENSE').read_bytes())
            self.assertEqual((source/'native/observer/observer.cpp').read_bytes(),(ROOT/'native/observer/observer.cpp').read_bytes())

    def test_archive_is_reproducible_and_does_not_depend_on_source_file_mtimes(self):
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);first=temp/'first.tar.gz';second=temp/'second.tar.gz'
            a=build(first);b=build(second)
            self.assertEqual(first.read_bytes(),second.read_bytes())
            self.assertEqual(a['archiveSHA256'],b['archiveSHA256'])
            with tarfile.open(first) as bundle:
                self.assertTrue(all(m.mtime==0 and m.uid==0 and m.gid==0 for m in bundle.getmembers()))


if __name__=='__main__':unittest.main()

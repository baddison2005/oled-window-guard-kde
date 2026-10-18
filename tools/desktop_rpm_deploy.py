#!/usr/bin/env python3
"""Snapshot and retire the owned user-local install before RPM desktop testing."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile

from tools import install_user

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'docs/evidence/live-rpm-2026-10-12'
RPM = ROOT / 'docs/evidence/gpl-adoption-2026-10-12/oled-window-guard-0.1.0-3.fc44.x86_64.rpm'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage', choices=('backup', 'retire', 'verify'))
    args = parser.parse_args()
    data, bindir = install_user.xdg_paths()
    manifest_path = data / install_user.MANIFEST_REL
    snapshot = EVIDENCE / 'before.json'
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    expected = json.loads((ROOT / 'docs/evidence/gpl-adoption-2026-10-12/result.json').read_text())['artifactSHA256'][RPM.name]
    if install_user.digest(RPM.read_bytes()) != expected:
        raise RuntimeError('RPM digest differs from tested artifact')
    if args.stage == 'backup':
        if snapshot.exists():
            raise RuntimeError('Backup already exists; refusing to replace it')
        manifest = install_user._read_manifest(manifest_path)
        if not manifest['files']:
            raise RuntimeError('No owned user-local installation found')
        install_user._validate_manifest_targets(manifest, data, bindir)
        install_user._check_prior_ownership(manifest)
        config = Path.home() / '.config'
        configuration = sorted((config / 'oled-window-guard').rglob('*')) + [config / 'kwinrc']
        files = {Path(name) for name in manifest['files']} | {manifest_path}
        files.update(p for p in configuration if p.is_file())
        hashes = {str(p): install_user.digest(p.read_bytes()) for p in files if p.is_file()}
        archive = EVIDENCE / 'user-install-and-settings.tar.gz'
        with tarfile.open(archive, 'x:gz') as bundle:
            for p in sorted(files):
                if p.is_file():
                    if p.is_symlink() or not p.is_relative_to(Path.home()):
                        raise RuntimeError('Unexpected backup path')
                    bundle.add(p, arcname=str(p.relative_to(Path.home())), recursive=False)
        snapshot.write_text(json.dumps(dict(rpmSHA256=expected, files=hashes,
            configuration={str(p): hashes[str(p)] for p in configuration if p.is_file()},
            backupSHA256=install_user.digest(archive.read_bytes())), indent=2) + '\n')
        print(json.dumps(dict(backedUpFiles=len(hashes), backup=str(archive), ownedFilesUnmodified=True)))
    else:
        before = json.loads(snapshot.read_text())
        version = subprocess.check_output(['rpm', '-q', '--qf', '%{EVR}', 'oled-window-guard'], text=True)
        if version != '0.1.0-3.fc44':
            raise RuntimeError('Expected tested RPM to be installed first')
        if args.stage == 'retire':
            if install_user.digest((EVIDENCE / 'user-install-and-settings.tar.gz').read_bytes()) != before['backupSHA256']:
                raise RuntimeError('Backup was altered')
            install_user._check_prior_ownership(install_user._read_manifest(manifest_path))
            removed = install_user.uninstall(data, bindir)
            print(json.dumps(dict(retiredUserLocalFiles=len(removed), settingsUntouched=True)))
        else:
            for path, checksum in before['configuration'].items():
                if install_user.digest(Path(path).read_bytes()) != checksum:
                    raise RuntimeError('Configuration changed during deployment: ' + path)
            if (bindir / 'oled-window-guard').exists() or (data / 'applications/oled-window-guard.desktop').exists():
                raise RuntimeError('User-local launcher still shadows RPM')
            result = subprocess.run(['rpm', '-V', 'oled-window-guard'], capture_output=True, text=True)
            if result.returncode or result.stdout:
                raise RuntimeError('Installed RPM verification failed: ' + result.stdout + result.stderr)
            value = dict(result='PASS', installedVersion=version, configurationBytesPreserved=True,
                userLocalShadowRemoved=True, rpmPayloadVerified=True, rpmSHA256=expected)
            (EVIDENCE / 'deployment.json').write_text(json.dumps(value, indent=2) + '\n')
            print(json.dumps(value))


if __name__ == '__main__':
    main()

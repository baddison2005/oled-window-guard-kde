#!/usr/bin/env python3
"""Test package lifecycle in a private RPM DB with /usr relocated under /tmp.

No scripts, triggers or RPM plugins run. Never uses the system package database.
Optional --rollback-to checks a real older payload before upgrading again.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


def run(*args):
    result=subprocess.run(list(args),capture_output=True,text=True,timeout=30)
    if result.returncode:raise RuntimeError('RPM test command failed: '+result.stdout+result.stderr)
    return result.stdout


def inspect(rpm):
    rpm=Path(rpm).resolve()
    paths=run('rpm','-qpl',str(rpm)).splitlines()
    if not paths or not all(p.startswith('/usr/') and '..' not in Path(p).parts for p in paths):
        raise RuntimeError('RPM paths must remain beneath relocated /usr')
    if any('probe' in p or 'native_check' in p or '/autostart/' in p for p in paths):
        raise RuntimeError('Test helpers or autostart payload cannot ship')
    if run('rpm','-qp','--qf','%{NAME}',str(rpm))!='oled-window-guard':raise RuntimeError('Wrong RPM name')
    return dict(path=rpm,evr=run('rpm','-qp','--qf','%{EVR}',str(rpm)),
                sha256=hashlib.sha256(rpm.read_bytes()).hexdigest(),files=paths)


def verify_payload(usr,*,immutable_required):
    package=usr/'share/kwin/scripts/oled-window-guard-adapter'
    native=package/'contents/ui/native'
    infos=list(package.rglob('build-info.json'))
    if not infos:raise RuntimeError('Native metadata missing')
    for path in infos:
        info=json.loads(path.read_text());binaries=list(path.parent.rglob('libowg_native_observer.so'))
        if len(binaries)!=1 or info['testInjection'] is not False or hashlib.sha256(binaries[0].read_bytes()).hexdigest()!=info['pluginSHA256']:
            raise RuntimeError('Native payload hash/provenance mismatch')
    runtime=package/'contents/runtime-build.json'
    if immutable_required:
        if not runtime.exists() or len(infos)!=2:raise RuntimeError('Immutable runtime omitted from current RPM')
        main=json.loads(runtime.read_text())['main'];relative=Path(main)
        if relative.is_absolute() or '..' in relative.parts or not (package/'contents'/relative).is_file():
            raise RuntimeError('Invalid immutable runtime entry')
        tree=(package/'contents'/relative).parent
        for name in ('RuntimeController.qml','GuardedWriter.qml','OrderedGuardedWriter.qml','WarningSurface.qml','planning_worker.js'):
            if not (tree/name).is_file():raise RuntimeError('Immutable runtime component missing: '+name)
    if json.loads((package/'metadata.json').read_text())['KPlugin']['EnabledByDefault']:
        raise RuntimeError('Package must start disabled')
    if 'property bool enabled: false' not in (package/'contents/ui/GuardedWriter.qml').read_text():
        raise RuntimeError('Writer default is enabled')
    # Keep exact payload hashes to verify repair, upgrade and rollback content.
    return {str(p.relative_to(usr)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in usr.rglob('*') if p.is_file()}


def lifecycle(rpm,rollback=None):
    current=inspect(rpm);older=inspect(rollback) if rollback else None
    if older and older['evr']==current['evr']:raise RuntimeError('Rollback requires a distinct older version/release')
    with tempfile.TemporaryDirectory(prefix='owg-native-rpm-install-') as temporary:
        base=Path(temporary);db=base/'db';usr=base/'usr';db.mkdir();usr.mkdir()
        common=['rpm','--dbpath',str(db),'--noplugins']
        run(*common,'--initdb')
        install=['--nodeps','--noscripts','--notriggers','--badreloc','--relocate','/usr='+str(usr)]
        settings=base/'home/.config/oled-window-guard/settings.json';settings.parent.mkdir(parents=True)
        settings.write_text('{"keep":"user"}')
        library=base/'home/.config/kwinrc';library.write_text('[WindowLayouts]\nLayoutPadding=8\n')
        def preserved():
            assert settings.read_text()=='{"keep":"user"}'
            assert library.read_text()=='[WindowLayouts]\nLayoutPadding=8\n'
        def verify(package,immutable=True):
            assert run(*common,'-q','--qf','%{EVR}','oled-window-guard')==package['evr']
            run(*common,'-V','--nodeps','--nouser','--nogroup','oled-window-guard')
            preserved();return verify_payload(usr,immutable_required=immutable)
        def apply(package,*,downgrade=False,replace=False,fresh=False):
            flags=['-i' if fresh else '-U']
            if downgrade:flags+=['--oldpackage']
            if replace:flags+=['--replacepkgs']
            run(*common,*flags,*install,str(package['path']))
        if older:
            apply(older,fresh=True);old_hashes=verify(older,False)
            apply(current);new_hashes=verify(current)
            assert old_hashes!=new_hashes,'Upgrade must change actual payload'
        else:
            apply(current,fresh=True);new_hashes=verify(current)
        # Repair must restore modified owned bytes, not just an untouched reinstall.
        owned=usr/'libexec/oled-window-guard/oled_window_guard/session.py'
        owned.write_bytes(owned.read_bytes()+b'\n# isolated repair test\n')
        apply(current,replace=True);assert verify(current)==new_hashes
        if older:
            apply(older,downgrade=True);assert verify(older,False)==old_hashes
            apply(current);assert verify(current)==new_hashes
        # Preserve unrelated data even beneath the install prefix on erase.
        unrelated=usr/'share/owg-unrelated-sentinel.txt';unrelated.write_text('keep')
        run(*common,'-e','--nodeps','--noscripts','--notriggers','oled-window-guard')
        assert all(not (usr/p).exists() for p in new_hashes)
        assert unrelated.read_text()=='keep';preserved()
    return dict(result='PASS',privateRpmDatabase=True,relocatedUsr=True,install=True,
        sameVersionUpgrade=True,modifiedOwnedFileRepair=True,payloadVerification=True,
        versionUpgrade=bool(older),versionRollback=bool(older),upgradeAfterRollback=bool(older),
        uninstall=True,settingsPreserved=True,layoutLibraryPreserved=True,unrelatedFilesPreserved=True,
        immutableRuntimeVerified=True,systemInstalled=False,rpmSHA256=current['sha256'],
        currentEVR=current['evr'],rollbackEVR=older['evr'] if older else None,
        rollbackRpmSHA256=older['sha256'] if older else None,
        limits='Private RPM DB only: dependencies, ownership, scripts and triggers are not exercised; system DNF and published-release acceptance remain separate.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('rpm',type=Path);parser.add_argument('--rollback-to',type=Path)
    args=parser.parse_args();print(json.dumps(lifecycle(args.rpm,args.rollback_to)))


if __name__=='__main__':main()

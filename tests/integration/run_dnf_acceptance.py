#!/usr/bin/env python3
"""Isolated Fedora 44 DNF checks; never targets the host RPM database."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path('/tmp/owg-dnf-acceptance-20261012')
REPO=Path(__file__).resolve().parents[2]
OLDER=REPO/'docs/evidence/native-deployment-writer-2026-10-05/oled-window-guard-0.1.0-1.fc44.x86_64.rpm'
RPM=REPO/'docs/evidence/rpm-lifecycle-2026-10-12/oled-window-guard-0.1.0-2.fc44.x86_64.rpm'


def environment():
    ROOT.mkdir(exist_ok=True)
    for name in ('runtime','storage','runroot','tmp','logs','cache','installroot','repos','container-storage','container-run','artifacts'):
        (ROOT/name).mkdir(mode=0o700,exist_ok=True)
    if ROOT.resolve()!=ROOT or (ROOT/'installroot').resolve()!=ROOT/'installroot':
        raise RuntimeError('Private root must not be redirected by symlinks')
    env=dict(os.environ,XDG_RUNTIME_DIR=str(ROOT/'runtime'))
    for key in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY'):
        env.pop(key,None)
    return env


def podman():
    return ['podman','--root',str(ROOT/'storage'),'--runroot',str(ROOT/'runroot'),'--tmpdir',str(ROOT/'tmp')]


def dnf():
    return ['dnf','--installroot='+str(ROOT/'installroot'),'--releasever=44','--use-host-config',
            '--setopt=reposdir='+str(ROOT/'repos'),'--setopt=cachedir='+str(ROOT/'cache'),
            '--setopt=logdir='+str(ROOT/'logs'),'--setopt=persistdir='+str(ROOT/'installroot/var/lib/dnf'),
            '--setopt=install_weak_deps=False','--no-plugins','--repo=fedora,updates']


def configure():
    for name in ('fedora.repo','fedora-updates.repo'):
        shutil.copy2(Path('/etc/yum.repos.d')/name,ROOT/'repos'/name)
    # Reuse metadata by copying it; DNF never writes the host cache.
    for cache in (Path('/var/cache/libdnf5'),Path.home()/'.cache/libdnf5'):
        for p in cache.glob('*'):
            if p.is_dir() and p.name.startswith(('fedora-','updates-')) and not (ROOT/'cache'/p.name).exists():
                shutil.copytree(p,ROOT/'cache'/p.name,dirs_exist_ok=True)


def run(command,env,name,timeout=120):
    with (ROOT/(name+'.log')).open('w') as stream:
        result=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    event=dict(stage=name,exitCode=result.returncode,log=str(ROOT/(name+'.log')),at=time.time())
    snapshot=ROOT/(name+'-'+str(time.time_ns())+'.log');shutil.copy2(ROOT/(name+'.log'),snapshot)
    event['snapshotLog']=snapshot.name
    with (ROOT/'history.jsonl').open('a') as history:history.write(json.dumps(event)+'\n')
    print(json.dumps(event))
    print('\n'.join((ROOT/(name+'.log')).read_text(errors='replace').splitlines()[-25:]))
    return result.returncode


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('namespace','cached-solve','refresh-solve','missing-dependency','unsupported-qt','install-older','upgrade','downgrade','reinstall','check','remove','query','container-install','container-upgrade','container-downgrade','container-reinstall','container-check','container-remove','container-query','container-status','container-seed','container-verify','container-tamper','container-stop','container-image','container-tools'))
    args=parser.parse_args();env=environment()
    if args.stage=='namespace':return run(podman()+['unshare','id'],env,'namespace',30)
    if args.stage.startswith('container-'):
        engine=['podman','--root',str(ROOT/'container-storage'),'--runroot',str(ROOT/'container-run'),'--tmpdir',str(ROOT/'tmp'),'--storage-driver=vfs','--cgroup-manager=cgroupfs']
        name='owg-dnf-acceptance-20261012'
        if args.stage=='container-install':
            for source in (RPM,OLDER):shutil.copy2(source,ROOT/'artifacts'/source.name)
            config=ROOT/'artifacts/dnf-test.conf'
            config.write_text('[main]\ninstall_weak_deps=False\nkeepcache=True\nplugins=False\ngpgcheck=True\nlocalpkg_gpgcheck=False\n')
            command=engine+['run','--detach','--name',name,'--security-opt','label=disable','--network=host',
                '--volume',str(ROOT/'cache')+':/var/cache/libdnf5',
                '--volume',str(ROOT/'artifacts')+':/owg:ro',
                'registry.fedoraproject.org/fedora:44','sh','-c','dnf --config=/owg/dnf-test.conf --repo=fedora,updates --assumeyes install /owg/'+OLDER.name+'; result=$?; echo OWG_BOOTSTRAP_EXIT=$result; if [ $result -eq 0 ]; then exec sleep infinity; fi; exit $result']
        elif args.stage=='container-status':
            return run(engine+['logs',name],env,args.stage,30)
        elif args.stage=='container-tools':
            return run(engine+['exec',name,'sh','-c','cat /etc/os-release; id; dnf --version'],env,args.stage,30)
        elif args.stage=='container-image':
            return run(engine+['image','inspect','registry.fedoraproject.org/fedora:44'],env,args.stage,30)
        elif args.stage=='container-stop':
            return run(engine+['stop',name],env,args.stage,30)
        elif args.stage in ('container-seed','container-verify','container-tamper'):
            if args.stage=='container-seed':
                code="from pathlib import Path; p=Path('/root/.config/oled-window-guard'); p.mkdir(parents=True,exist_ok=True); (p/'settings.json').write_text('{\"preserve\":\"settings\"}'); Path('/root/.config/kwinrc').write_text('[WindowLayouts]\\nLayoutPadding=8\\n'); Path('/usr/share/owg-unrelated.txt').write_text('preserve unrelated data')"
            elif args.stage=='container-tamper':
                code="from pathlib import Path; p=Path('/usr/libexec/oled-window-guard/oled_window_guard/session.py'); p.write_bytes(p.read_bytes()+b'\\n# isolated repair test\\n')"
            else:
                code="import json,hashlib,subprocess; from pathlib import Path; assert Path('/root/.config/oled-window-guard/settings.json').read_text()=='{\"preserve\":\"settings\"}'; assert Path('/root/.config/kwinrc').read_text()=='[WindowLayouts]\\nLayoutPadding=8\\n'; assert Path('/usr/share/owg-unrelated.txt').read_text()=='preserve unrelated data'; p=Path('/usr/libexec/oled-window-guard/oled_window_guard/session.py'); installed=p.exists(); owned=[Path('/usr/bin/oled-window-guard'),Path('/usr/libexec/oled-window-guard'),Path('/usr/share/applications/oled-window-guard.desktop'),Path('/usr/share/kwin/scripts/oled-window-guard-adapter')]; assert installed or not any(q.exists() for q in owned); r=subprocess.run(['rpm','-V','oled-window-guard'],capture_output=True,text=True); assert r.returncode==0 if installed else r.returncode==1; print(json.dumps({'installed':installed,'settingsAndLibraryPreserved':True,'unrelatedFilePreserved':True,'sessionSHA256':hashlib.sha256(p.read_bytes()).hexdigest() if installed else None,'rpmVerification':r.returncode}))"
            return run(engine+['exec',name,'python3','-c',code],env,args.stage,30)
        elif args.stage=='container-query':
            command=engine+['exec',name,'rpm','-q','oled-window-guard','kwin','qt6-qtbase','qt6-qtdeclarative','python3-pyside6']
        else:
            action={'container-upgrade':['upgrade','/owg/'+RPM.name],'container-downgrade':['downgrade','/owg/'+OLDER.name],'container-reinstall':['reinstall','/owg/'+RPM.name],'container-check':['check'],'container-remove':['--setopt=clean_requirements_on_remove=False','remove','oled-window-guard']}[args.stage]
            command=engine+['exec',name,'dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','--assumeyes',*action]
        return run(command,env,args.stage,900)
    if args.stage in ('cached-solve','refresh-solve','missing-dependency'):
        configure()
    if args.stage=='missing-dependency':
        return run(dnf()+['--cacheonly','--assumeno','--exclude=kwin','install',str(RPM)],env,args.stage,120)
    if args.stage=='unsupported-qt':
        rejected=ROOT/'unsupported-root';rejected.mkdir(exist_ok=True)
        command=[arg.replace(str(ROOT/'installroot'),str(rejected)) for arg in dnf()]
        return run(command+['--cacheonly','--assumeno','--exclude=qt6-qtbase-6.11.2*','install',str(RPM)],env,args.stage,120)
    if args.stage in ('install-older','upgrade','downgrade','reinstall','check','remove','query'):
        actions={'install-older':['install',str(OLDER)],'upgrade':['upgrade',str(RPM)],'downgrade':['downgrade',str(OLDER)],'reinstall':['reinstall',str(RPM)],'check':['check'],'remove':['remove','oled-window-guard']}
        if args.stage=='query':
            command=podman()+['unshare','rpm','--root',str(ROOT/'installroot'),'-q','oled-window-guard','kwin','qt6-qtbase','qt6-qtdeclarative','python3-pyside6']
        else:
            command=podman()+['unshare',*dnf(),'--assumeyes',*actions[args.stage]]
        return run(command,env,args.stage,900)
    flags=['--cacheonly'] if args.stage=='cached-solve' else ['--refresh']
    return run(dnf()+flags+['--assumeno','install',str(RPM)],env,args.stage,120)


if __name__=='__main__':raise SystemExit(main())

#!/usr/bin/env python3
"""Disposable signing-key rehearsal in the existing private Fedora container.

Never accesses the user's GPG keyring or publishes artifacts.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

ROOT=Path('/tmp/owg-dnf-acceptance-20261012')
REPO=Path(__file__).resolve().parents[2]
NAME='owg-dnf-acceptance-20261012'
OUTPUT=ROOT/'signing-output'


def engine():return ['podman','--root',str(ROOT/'container-storage'),'--runroot',str(ROOT/'container-run'),'--tmpdir',str(ROOT/'tmp'),'--storage-driver=vfs','--cgroup-manager=cgroupfs']


def run(command,name,timeout=120,expected=0):
    env=dict(os.environ,XDG_RUNTIME_DIR=str(ROOT/'runtime'))
    for k in ('DBUS_SESSION_BUS_ADDRESS','DBUS_SYSTEM_BUS_ADDRESS','DISPLAY','WAYLAND_DISPLAY','GNUPGHOME'):
        env.pop(k,None)
    OUTPUT.mkdir(exist_ok=True)
    with (OUTPUT/(name+'.log')).open('w') as stream:
        result=subprocess.run(command,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=timeout)
    text=(OUTPUT/(name+'.log')).read_text(errors='replace')
    event=dict(stage=name,exitCode=result.returncode,expectedExitCode=expected,at=time.time())
    with (OUTPUT/'history.jsonl').open('a') as stream:stream.write(json.dumps(event)+'\n')
    print(json.dumps(event));print('\n'.join(text.splitlines()[-15:]))
    if result.returncode!=expected:raise RuntimeError(name+' unexpected result; see '+str(OUTPUT))
    return text


def execute(arguments,name,timeout=120,expected=0):return run(engine()+['exec',NAME,*arguments],name,timeout,expected)


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('stage',choices=('prepare','sign','untrusted','trusted','tampered','export','cleanup-key','stop'))
    args=parser.parse_args()
    if args.stage=='prepare':
        run(engine()+['start',NAME],'start')
        execute(['dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','-y','install','rpm-sign','gnupg2'],'signing-tools',900)
        execute(['python3','-c',"from pathlib import Path; import shutil; p=Path('/tmp/owg-signing'); p.mkdir(mode=0o700,exist_ok=True); (p/'gnupg').mkdir(mode=0o700,exist_ok=True); shutil.copy2('/owg/oled-window-guard-0.1.0-2.fc44.x86_64.rpm',p/'owg-development-signing-test.rpm')"],'private-key-directory')
        execute(['gpg','--batch','--homedir','/tmp/owg-signing/gnupg','--pinentry-mode','loopback','--passphrase','','--quick-generate-key','OWG disposable signing test <owg-test@example.invalid>','ed25519','sign','1d'],'generate-disposable-key')
        execute(['gpg','--homedir','/tmp/owg-signing/gnupg','--with-colons','--list-keys'],'fingerprint')
    elif args.stage=='sign':
        listing=execute(['gpg','--homedir','/tmp/owg-signing/gnupg','--with-colons','--list-keys'],'fingerprint')
        fingerprint=next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        execute(['rpmsign','--define','_openpgp_sign gpg','--define','_openpgp_sign_id '+fingerprint,
                 '--define','_gpg_path /tmp/owg-signing/gnupg','--addsign','/tmp/owg-signing/owg-development-signing-test.rpm'],'sign-rpm')
        execute(['sh','-c','gpg --batch --homedir /tmp/owg-signing/gnupg --armor --export > /tmp/owg-signing/disposable-public-key.asc'],'export-public-key')
    elif args.stage=='untrusted':
        execute(['dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','--setopt=localpkg_gpgcheck=True','-y','install','/tmp/owg-signing/owg-development-signing-test.rpm'],'untrusted-install',expected=1)
    elif args.stage=='trusted':
        execute(['rpm','--import','/tmp/owg-signing/disposable-public-key.asc'],'import-disposable-key-private-container')
        execute(['rpmkeys','--checksig','/tmp/owg-signing/owg-development-signing-test.rpm'],'trusted-signature')
        execute(['dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','--setopt=localpkg_gpgcheck=True','-y','install','/tmp/owg-signing/owg-development-signing-test.rpm'],'trusted-install')
        execute(['rpm','-V','oled-window-guard'],'verify-installed-payload')
        execute(['dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','--setopt=clean_requirements_on_remove=False','-y','remove','oled-window-guard'],'remove-test-package')
    elif args.stage=='tampered':
        execute(['python3','-c',"from pathlib import Path; p=Path('/tmp/owg-signing/owg-development-signing-test.rpm'); data=bytearray(p.read_bytes()); data[-100]^=1; (p.parent/'owg-tampered-signing-test.rpm').write_bytes(data)"],'corrupt-test-copy')
        execute(['rpmkeys','--checksig','/tmp/owg-signing/owg-tampered-signing-test.rpm'],'tampered-digest-rejected',expected=1)
        execute(['dnf','--config=/owg/dnf-test.conf','--repo=fedora,updates','--setopt=localpkg_gpgcheck=True','-y','install','/tmp/owg-signing/owg-tampered-signing-test.rpm'],'tampered-install-rejected',expected=1)
    elif args.stage=='cleanup-key':
        listing=execute(['gpg','--homedir','/tmp/owg-signing/gnupg','--with-colons','--list-keys'],'cleanup-fingerprint')
        fingerprint=next(line.split(':')[9] for line in listing.splitlines() if line.startswith('fpr:'))
        execute(['gpg','--batch','--yes','--homedir','/tmp/owg-signing/gnupg','--delete-secret-and-public-key',fingerprint],'delete-disposable-secret-key')
        execute(['gpg','--homedir','/tmp/owg-signing/gnupg','--with-colons','--list-secret-keys'],'confirm-no-secret-keys')
    elif args.stage=='export':
        for file in ('owg-development-signing-test.rpm','disposable-public-key.asc'):
            run(engine()+['cp',NAME+':/tmp/owg-signing/'+file,str(OUTPUT/file)],'copy-'+file)
    else:run(engine()+['stop',NAME],'stop',30)


if __name__=='__main__':main()

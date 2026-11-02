#!/usr/bin/env python3
"""Offline DNF solve against stub desktop packages in a private RPM database.

Uses the real OWG RPM. No DNF transaction executes and the host DB is untouched.
The stubs test dependency constraints, not the behavior of a future compositor.
"""
import argparse,hashlib,json,os,re,subprocess,tempfile
from pathlib import Path


def run(argv,**kwargs):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=60,**kwargs)
    if result.returncode:raise RuntimeError(result.stdout+result.stderr)
    return result.stdout


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('rpm',type=Path)
    candidate=parser.parse_args().rpm.resolve()
    assert run(['rpm','-qp','--qf','%{NAME}',str(candidate)])=='oled-window-guard'
    requirements=run(['rpm','-qp','--requires',str(candidate)]).splitlines()
    assert not any(re.match(r'(?:kwin|qt6-qtbase|qt6-qtdeclarative)\s*[=<>]|libkwin\.so|libQt6',line) for line in requirements),requirements
    root=Path(tempfile.mkdtemp(prefix='owg-desktop-upgrade-'))
    env=dict(os.environ,LC_ALL='C',LANG='C')
    groups=[]
    for variant,kwin,qt in [('old','6.8.2','6.11.2'),('future','6.9.0','6.12.0')]:
        top=root/variant
        for name in ('BUILD','BUILDROOT','RPMS','SPECS','SOURCES','SRPMS','tmp'):(top/name).mkdir(parents=True)
        spec=f'''Name: kwin
Version: {kwin}
Release: 1.fc44
Summary: Private solver fixture, no real desktop code
License: MIT
AutoReqProv: no
Provides: libkwin.so.{6 if variant=='old' else 7}()(64bit)
%description
Private dependency fixture.
%files
'''
        for name in ('qt6-qtbase','qt6-qtdeclarative'):
            spec+=f'''%package -n {name}
Version: {qt}
Summary: Private Qt solver fixture
AutoReqProv: no
%description -n {name}
Private dependency fixture.
%files -n {name}
'''
        if variant=='old':
            spec+='''%package -n owg-test-dependencies
Version: 1
Summary: Private dependency providers
AutoReqProv: no
Provides: python3 = 3.14
Provides: python3-pyside6
Provides: python3-rpm
Provides: /usr/bin/dnf
Provides: /usr/bin/pkexec
Provides: /usr/bin/sh
Provides: kf6-kcoreaddons
%description -n owg-test-dependencies
Private dependency fixture.
%files -n owg-test-dependencies
'''
        path=top/'SPECS/fixtures.spec';path.write_text(spec)
        output=run(['rpmbuild','-bb','--define',f'_topdir {top}','--define',f'_tmppath {top}/tmp',str(path)],env=env)
        (top/'build.log').write_text(output);groups.append(sorted((top/'RPMS').rglob('*.rpm')))
    installroot=root/'installroot';db=installroot/'usr/lib/sysimage/rpm';db.mkdir(parents=True)
    common=['rpm','--dbpath',str(db),'--noplugins']
    run(common+['--initdb'])
    run(common+['-i','--nodeps','--noscripts','--notriggers','--badreloc','--relocate',f'/usr={installroot}/usr',*[str(p) for p in groups[0]],str(candidate)])
    before=run(common+['-qa','--qf','%{NAME} %{VERSION}-%{RELEASE}\n'])
    repos=root/'repos';repos.mkdir()
    command=['dnf',f'--installroot={installroot}','--releasever=44','--use-host-config',
        f'--setopt=reposdir={repos}',f'--setopt=logdir={root}',f'--setopt=cachedir={root}/cache',
        f'--setopt=persistdir={installroot}/var/lib/dnf','--no-plugins','--assumeno','upgrade',*[str(p) for p in groups[1]]]
    result=subprocess.run(command,capture_output=True,text=True,timeout=60,env=env)
    text=result.stdout+result.stderr;(root/'solve.log').write_text(text)
    assert '6.9.0' in text and '6.12.0' in text and 'Upgrading' in text,text
    assert 'Removing' not in text and 'Downgrading' not in text and 'conflicting requests' not in text,text
    assert run(common+['-qa','--qf','%{NAME} %{VERSION}-%{RELEASE}\n'])==before
    report=dict(result='PASS',normalDatabaseUntouched=True,dnfTransactionExecuted=False,
        candidate=str(candidate),candidateSHA256=hashlib.sha256(candidate.read_bytes()).hexdigest(),futureVersions=dict(kwin='6.9.0',qt='6.12.0'),
        installedPackages=before.splitlines(),dnfExitCode=result.returncode,
        limits='Stub packages establish solver freedom only; future runtime compatibility must still fail closed.')
    (root/'result.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report));print('Artifacts:',root)


if __name__=='__main__':main()

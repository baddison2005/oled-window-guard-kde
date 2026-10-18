#!/usr/bin/env python3
"""Build the version-coupled native QML module; no installation or enablement.

An extracted development prefix may be used instead of installed headers.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "native/observer"


def link_libraries(prefix, names):
    libraries=[]
    for name in names:
        library=Path(prefix)/'lib64'/f'lib{name}.so'
        if not library.is_file() and library.is_symlink():
            # Download-only devel RPMs omit already installed runtime packages.
            # Use the exact SONAME named by their linker symlink on this host.
            library=Path('/usr/lib64')/library.readlink().name
        if not library.is_file():
            raise ValueError(f'matching runtime library unavailable: {library}')
        libraries.append(str(library))
    return libraries


def build(prefix, output):
    prefix=Path(prefix).resolve();output=Path(output).resolve()
    if output==Path('/tmp') or not output.is_relative_to(Path('/tmp')):
        raise ValueError('build output must be a private directory under /tmp')
    headers=prefix/'include'
    version=re.search(r'#define KWIN_PLUGIN_VERSION_STRING "([^"]+)"',
                      (headers/'kwin/config-kwin.h').read_text()).group(1)
    qt_version=re.search(r'#define QT_VERSION_STR "([^"]+)"',
                         (headers/'qt6/QtCore/qconfig-64.h').read_text()).group(1)
    running=subprocess.run(['kwin_wayland','--version'],check=True,capture_output=True,text=True,timeout=10).stdout.strip()
    if running!='kwin '+version:
        raise ValueError('matching installed KWin development headers are required')
    moc=prefix/'lib64/qt6/libexec/moc'
    includes=[headers,headers/'kwin',headers/'qt6']
    includes += [p for parent in (headers/'qt6',headers/'KF6') for p in sorted(parent.iterdir()) if p.is_dir()]
    flags=[f'-I{p}' for p in includes]
    target=output/'org/oledwindowguard/Native';target.mkdir(parents=True,exist_ok=True)
    plugin=target/'libowg_native_observer.so'
    with tempfile.TemporaryDirectory(prefix='owg-observer-build-') as directory:
        generated=Path(directory)
        subprocess.run([str(moc),*flags,str(SOURCE/'observer.cpp'),'-o',str(generated/'observer.moc')],check=True)
        subprocess.run(['g++','-std=c++23','-fPIC','-shared',str(SOURCE/'observer.cpp'),'-o',str(plugin),
                        f'-I{generated}',*flags,*link_libraries(prefix,['kwin','Qt6Core','Qt6Gui','Qt6Qml','Qt6DBus','KF6CoreAddons'])],check=True)
    shutil.copy2(SOURCE/'qmldir',target/'qmldir')
    info={'schema':1,'kwinVersion':version,'qtVersion':qt_version,'protocol':1,'testInjection':False,
          'sourceSHA256':hashlib.sha256((SOURCE/'observer.cpp').read_bytes()).hexdigest(),
          'pluginSHA256':hashlib.sha256(plugin.read_bytes()).hexdigest()}
    (output/'build-info.json').write_text(json.dumps(info,indent=2)+'\n')
    return plugin


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sysroot',type=Path,default=Path('/usr'))
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print(build(args.sysroot,args.output))

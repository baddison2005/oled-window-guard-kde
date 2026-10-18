#!/usr/bin/env python3
"""Build only the isolated cancellation test effect under /tmp."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tools.build_native_observer import link_libraries

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sysroot',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    output=args.output.resolve()
    if output==Path('/tmp') or not output.is_relative_to(Path('/tmp')): parser.error('private /tmp output required')
    prefix=args.sysroot.resolve();headers=prefix/'include'
    includes=[headers,headers/'kwin',headers/'qt6']
    includes += [p for parent in (headers/'qt6',headers/'KF6') for p in sorted(parent.iterdir()) if p.is_dir()]
    flags=[f'-I{p}' for p in includes]
    source=Path(__file__).resolve().parent/'check.cpp'
    target=output/'kwin/effects/plugins/libowg_native_check.so';target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='owg-native-check-build-') as directory:
        generated=Path(directory)
        subprocess.run([str(prefix/'lib64/qt6/libexec/moc'),*flags,str(source),'-o',str(generated/'check.moc')],check=True)
        subprocess.run(['g++','-std=c++23','-fPIC','-shared',str(source),'-o',str(target),f'-I{generated}',*flags,
            *link_libraries(prefix,['kwin','Qt6Core','Qt6Gui','Qt6DBus','Qt6Qml','KF6CoreAddons'])],check=True)
    (output/'build-info.json').write_text(json.dumps({'schema':1,'testOnly':True,'sourceSHA256':hashlib.sha256(source.read_bytes()).hexdigest(),
        'pluginSHA256':hashlib.sha256(target.read_bytes()).hexdigest()},indent=2)+'\n')
    print(target)

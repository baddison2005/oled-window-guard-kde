#!/usr/bin/env python3
"""Create a deterministic GPL application corresponding-source bundle.

The legacy tool name is retained; controller and compositor are both included.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import tarfile
import io

ROOT=Path(__file__).resolve().parents[1]
DIRECTORIES=('core','native/observer','oled_window_guard','packages/kwin-script',
             'packages/desktop','schema','interfaces','assets','tools','tests','probes',
             'packaging/rpm','packaging/licenses')
FILES=('LICENSE','NOTICE','SOURCE.md','README.md','CHANGELOG.md','docs/RELEASE_REVIEW_v1.0.0.md','docs/RELEASE_REVIEW_v1.0.1.md','docs/RELEASE_REVIEW_v1.0.2.md','docs/RELEASE_REVIEW_v1.0.3.md','docs/RELEASE_REVIEW_v1.0.4.md')


def source_files(root=ROOT):
    root=Path(root);files={}
    for directory in DIRECTORIES:
        for p in sorted((root/directory).rglob('*')):
            if p.is_symlink():raise ValueError('Source symlinks are not permitted')
            if not p.is_file() or '__pycache__' in p.parts or p.suffix in ('.pyc','.so','.rpm'):continue
            files[p.relative_to(root).as_posix()]=p.read_bytes()
    for relative in FILES:
        p=root/relative
        if p.is_symlink():raise ValueError('Source symlinks are not permitted')
        files[relative]=p.read_bytes()
    if not any(name.startswith('oled_window_guard/') for name in files):
        raise ValueError('Application corresponding source is missing')
    return files


def build(output,root=ROOT):
    files=source_files(root)
    manifest={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
    files['SOURCE-MANIFEST.json']=(json.dumps(dict(status='GPL_SOURCE_BUNDLE',license='GPL-3.0-or-later AND MIT',sha256=manifest),indent=2)+'\n').encode()
    files['SOURCE-REVIEW.md']=b"This bundle includes the GPL controller and compositor. See SOURCE.md for build and distribution instructions. No binary release is published by this tool.\n"
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',filename='',mtime=0) as zipped:
        with tarfile.open(fileobj=zipped,mode='w',format=tarfile.PAX_FORMAT) as archive:
            for relative,data in sorted(files.items()):
                info=tarfile.TarInfo('oled-window-guard-source/'+relative)
                info.size=len(data);info.mode=0o644;info.mtime=0;info.uid=info.gid=0
                archive.addfile(info,io.BytesIO(data))
    return dict(status='GPL_SOURCE_BUNDLE',files=len(manifest),
                archiveSHA256=hashlib.sha256(output.read_bytes()).hexdigest(),
                nativeSourceSHA256=manifest['native/observer/observer.cpp'])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('output',type=Path)
    print(json.dumps(build(parser.parse_args().output)))

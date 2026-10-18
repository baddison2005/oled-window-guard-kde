#!/usr/bin/env python3
"""Build the default-off KPackage with its exact core and production worker."""
from pathlib import Path
import argparse
import hashlib
import json
import re

try:
    from .build_planning_worker import build
except ImportError:
    from build_planning_worker import build

ROOT = Path(__file__).resolve().parents[1]


def native_files(module):
    """Validate a version-coupled build before it becomes installer-owned data."""
    module=Path(module).resolve()
    info=json.loads((module/"build-info.json").read_text())
    base=Path("org/oledwindowguard/Native")
    binary=(module/base/"libowg_native_observer.so").read_bytes()
    if (info.get("schema")!=1 or info.get("protocol")!=1 or info.get("testInjection") is not False or not binary.startswith(b"\x7fELF\x02") or
            info.get("sourceSHA256")!=hashlib.sha256((ROOT/"native/observer/observer.cpp").read_bytes()).hexdigest() or
            info.get("pluginSHA256")!=hashlib.sha256(binary).hexdigest() or
            any(not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+",str(info.get(key,""))) for key in ("kwinVersion","qtVersion"))):
        raise ValueError("invalid or altered native observer build")
    if (module/base/"qmldir").read_text()!= (ROOT/"native/observer/qmldir").read_text():
        raise ValueError("unexpected native module descriptor")
    # Qt retains native plugins/types for the compositor process's lifetime.
    # Keep each binary at a distinct path and URI so an owned script reload can
    # load an upgraded observer without reusing the previous implementation.
    build_id=info["pluginSHA256"]
    relative=Path("native")/build_id/base
    descriptor=f"module org.oledwindowguard.Native.Build_{build_id}\nplugin owg_native_observer\n"
    loader=(ROOT/"packages/kwin-script/contents/ui/NativeObservation.qml").read_text()
    marker="./native/org/oledwindowguard/Native"
    if loader.count(marker)!=1:
        raise ValueError("unexpected native observer loader")
    loader=loader.replace(marker,"./"+relative.as_posix())
    fallback="localObserver=Qt.createQmlObject('import org.oledwindowguard.Native 1.0; NativeObserver {}',consumer)"
    if loader.count(fallback)!=1:
        raise ValueError("unexpected native observer fallback")
    # A bundled deployment must never acquire authority from an older global
    # registration after its exact packaged import fails.
    loader=loader.replace(fallback,"throw packagedError")
    return {Path("contents/ui")/relative/"libowg_native_observer.so":binary,
            Path("contents/ui")/relative/"qmldir":descriptor.encode(),
            Path("contents/ui/native/build-info.json"):(module/"build-info.json").read_bytes(),
            Path("contents/ui/NativeObservation.qml"):loader.encode()}


def package_files(root=ROOT, native_module=None):
    files = {}
    package = root / "packages/kwin-script"
    for source in sorted(package.rglob("*")):
        if source.is_file() and "__pycache__" not in source.parts:
            files[source.relative_to(package)] = source.read_bytes()
    for source in sorted((root / "core").glob("*.js")):
        files[Path("contents/ui/core") / source.name] = source.read_bytes()
    files[Path("contents/ui/planning_worker.js")] = build(root / "core").encode()
    if native_module is not None:
        files.update(native_files(native_module))
    # KWin can retain its shared QML engine/component cache after script unload.
    # The companion uses a complete immutable runtime tree so every upgraded
    # QML component and its relative imports receive new URLs.
    digest=hashlib.sha256()
    for path,content in sorted(files.items()):
        digest.update(path.as_posix().encode()+b"\0"+content+b"\0")
    build_id=digest.hexdigest()
    for path,content in list(files.items()):
        if path.is_relative_to(Path("contents/ui")):
            # A URI cannot be registered again from a different directory in
            # KWin's retained QML engine, even for an identical native binary.
            # The immutable runtime moves the module; give that copy its own URI.
            if native_module is not None and path.name=="qmldir" and "native" in path.parts:
                content=content.replace(b"\nplugin ",("_Runtime_"+build_id+"\nplugin ").encode(),1)
            files[Path("contents")/("build_"+build_id)/"ui"/path.relative_to("contents/ui")]=content
    files[Path("contents/runtime-build.json")]=json.dumps({"schema":1,"main":"build_"+build_id+"/ui/main.qml"}).encode()
    return files


def materialize(destination, root=ROOT, native_module=None):
    for relative, content in package_files(root,native_module).items():
        path = Path(destination) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--native-module",type=Path)
    args = parser.parse_args()
    materialize(args.output,native_module=args.native_module)

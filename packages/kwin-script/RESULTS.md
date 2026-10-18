# KWin package validation

Current product and package validation is recorded in the
[v1.0.0 release review](../../docs/RELEASE_REVIEW_v1.0.0.md).

The raw source package must be built before use so that its core modules,
WorkerScript and native observer are included. The metadata is default-off;
the single-instance external controller owns loading and unloading the matching
immutable runtime. Fresh startup remains paused.

```sh
python3 tools/build_kwin_package.py /tmp/owg-package --native-module /tmp/owg-native
```

Run this command from the repository root after building the matching native
module as described in [SOURCE.md](../../SOURCE.md). Private test probes and
input injectors are never part of the installed application package.

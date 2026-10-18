# Source and licensing

Copyright (C) 2026 Dr. Brett Addison. Original KDE implementation is licensed
under GPL-3.0-or-later; shared schema and interface definitions use MIT.
See LICENSE, NOTICE and packaging/licenses/THIRD_PARTY_NOTICES.md.

Create the complete application source bundle from this checkout:

```sh
python3 tools/build_compositor_source.py /tmp/oled-window-guard-source.tar.gz
```

The legacy tool name is retained, but the bundle now includes the controller,
compositor, source/build/installation tools, tests, interface definitions,
artwork and notices. It contains a SHA-256 manifest and no native binaries.
Extract it and run these commands from its `oled-window-guard-source` directory:

```sh
python3 tools/build_native_observer.py --sysroot /usr --output /tmp/owg-native
python3 tools/build_kwin_package.py /tmp/owg-kpackage --native-module /tmp/owg-native
python3 -m unittest discover -s tests/packaging
```

The exercised matrix is Fedora 44, KWin 6.7.5 and Qt 6.11.2. Install matching
compiler/development dependencies: gcc-c++, kwin-devel, qt6-qtbase-devel,
qt6-qtdeclarative-devel, kf6-kcoreaddons-devel, libepoxy-devel, pixman-devel,
libinput-devel, libxcb-devel, xcb-util-wm-devel, xcb-util-cursor-devel and
xcb-util-keysyms-devel. Runtime requires Python 3.11 or newer and PySide6.
Exact KWin/Qt version and source/binary hashes are checked by the builders.
Qt moc output and the planning worker are generated from included source.

The optional original icon's preferred source is assets/icons/generate-icon.swift.
It uses AppKit on macOS to render the artwork; it is not needed to build or run
the KDE controller/compositor. The RPM installs the rendered product icon.

For the RPM source archive, run packaging/rpm/make-source.py from a temporary
directory, then follow packaging/rpm/README.md. The source archive includes the
spec, launcher, licensing and all original application/build sources. A user-local
installation can be made with tools/install_user.py; it starts paused and requires
an explicit autostart opt-in. Do not run that installer during an active session
upgrade; use its documented ownership checks and quit the app first.

When distributing binaries, deliver the exact corresponding source and these
instructions under the applicable GPL requirements. A manifest alone is not a
substitute for source. System dependencies are not bundled: their Fedora source
packages and upstream licensing/source information must remain available as
applicable. Preserve any modified build instructions and source needed for your
distributed artifact. The public source repository is https://github.com/baddison2005/oled-window-guard-kde.
Release v1.0.0 includes the complete source archive and source RPM alongside the binary RPM.

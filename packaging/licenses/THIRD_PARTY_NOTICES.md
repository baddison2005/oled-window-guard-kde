# Dependency and provenance notices

Original OLED Window Guard project code and generated artwork:
Copyright © 2026 Dr. Brett Addison. The owner confirmed control of original
macOS, KDE and Window Layouts code/artwork, with no other original-project
contributors or external assets. This does not assert ownership of dependencies.

## KWin

The native observer dynamically links `libkwin.so.6` and uses KWin headers.
The inspected Fedora `kwin-devel-6.7.5-1.fc44.x86_64` package has a valid Fedora
signature. Its directly used API headers carry GPL-2.0-or-later notices, including:

- Matthias Ettrich; Lubos Lunak; Lucas Murray; Martin Gräßlin; Roman Gilg;
  Vlad Zahorodnii; Natalie Clarius.

Exact per-header copyright lines, emails, file hashes and package hash are in
`packaging/licenses/native-header-notices.json` (also archived in the review
evidence).
The headers and KWin library are provided by Fedora dependencies, rather than
vendored into this source tree. Keep applicable upstream notices and GPL source
availability obligations; the original project-owner confirmation does not
replace those notices. KWin's overall package contains multiple licenses and
must not be described as wholly owned by the project or wholly proprietary.

## Qt / Qt for Python / KDE Frameworks

The dashboard imports PySide6 QtCore, QtGui, QtWidgets and QtDBus. The native
module links Qt6 Core, Gui, Qml and DBus, and KDE CoreAddons. QML imports QtQuick,
QtQml.WorkerScript, KWin and PlasmaCore. These are separate installed system
packages; this project's RPM does not bundle those shared libraries.

Qt/PySide6 community packages inspected on the declared matrix provide LGPLv3
options (with GPL/commercial alternatives). The original controller and compositor are GPL-3.0-or-later. Fulfill applicable
dependency notice, license text,
replacement/relinking, reverse-engineering-for-debugging and source/installation
requirements as applicable. Dynamic loading and separate dependency packages
help implement compliance, but are not proof that every obligation is met.

KWin-linked compositor distribution uses a GPLv3-compatible path under the
project's GPL-3.0-or-later source grant. The upstream GPL-2.0-or-later KWin
headers permit the GPLv3 path. Full GPLv2, GPLv3 and LGPLv3 texts are included. Complete module-specific/system-package license
expressions and versions are recorded in the review's dependency receipt;
transitive dependencies keep their own notices and are not relicensed here.

Upstream guidance: https://doc.qt.io/qtforpython-6/ and
https://www.qt.io/development/open-source-lgpl-obligations .

## Source/asset references

Window Layouts 1.3.1, in the directory named `window-layouts-kde-v1.1.0`, is a
behavior/configuration-schema reference, not a runtime dependency. The macOS
application is a behavior and artwork reference. Neither repository is changed.
The local PNG exactly matches macOS icon exports from native vector generation
in `assets/icons/generate-icon.swift` (the KDE copy of the original generator).
Owner rights are confirmed; the release RPM includes this original artwork.

Text similarity review is supporting evidence only. It cannot prove that no
small copied fragment, adapted expression or third-party obligation exists.
The project license does not relicense system dependencies. Binary distributors
must satisfy applicable corresponding-source and dependency obligations.

The original warning chime (`assets/sounds/warning.wav`) is synthesized by
`assets/sounds/generate-warning.py` without external samples, under the project
GPL-3.0-or-later license.

# Compositor-local native observer

The production QML module is compiled for KWin 6.7.5 / Qt 6.11.2. It observes
input timing, held state, focus, output identity and session transitions inside
KWin. It does not inject input or assign application geometry/focus/opacity.
A restricted renderer operation reorders authenticated internal OWG dimming
planes around dock surfaces; it never reorders application clients.
The read-only pointer recipient identifies shell interactions using KWin's input
focus, so a transparent auto-hide dock rectangle cannot stand in for a click
delivered to an application underneath it.

Movement uses target-display interaction tokens. Passive pointer motion does
not cancel movement; clicks, scrolling, dragging and typing after the warning's
grace cutoff do. Held input and unknown state block writes. Unknown keyboard
focus is treated conservatively. Session/topology changes invalidate movement
authority synchronously. Previews use boundary tokens without write authority.

Native state is ephemeral. On observed lock/sleep/inactivity, guarding may retain
non-writing resume intent when the user enabled it. Fresh contact, session facts
and released input are required before work resumes. New app/module startup
remains paused; stale or replaced observers do not confer authority.

Build from the project root with matching development headers/runtime libraries:

```sh
python3 tools/build_native_observer.py --sysroot /usr --output /tmp/owg-native
python3 tools/build_kwin_package.py /tmp/owg-package --native-module /tmp/owg-native
```

The native build records source and binary hashes and version metadata. Packaging
validates them and gives updated runtimes distinct immutable module paths to
avoid reusing an already loaded shared library after an upgrade.

Isolated test helpers live under `probes/`; they are not installed by the RPM.
See [SOURCE.md](../../SOURCE.md) for build dependencies and the
[v1.0.0 review](../../docs/RELEASE_REVIEW_v1.0.0.md) for validation scope.

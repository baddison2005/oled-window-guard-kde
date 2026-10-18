# Phase 1A / 1B runtime probe results

Validated in the Fedora 44 test environment with KWin 6.7.5, Qt 6.11.2 and Node v22.23.1. All compositor, D-Bus, XDG config/data/cache/runtime paths and KPackage installation were isolated in a temporary directory. The default run used `kwin_wayland --virtual`; neither run connected to or changed the user's active compositor.

Commands from the project root:

```sh
node tests/runtime/test_runtime_core.js
python3 tests/runtime/run_nested.py
python3 tests/runtime/run_nested.py --allow-owned-test-move
```

All three passed. Node reported `runtime core: 9 assertions passed`. Both nested runs asserted successful declarative load and `Script.run`, `isScriptLoaded` before and after unload, no exchanges while unloaded, exactly one exchange after reload, one QML timer, async success, timeout, late-reply rejection, unanswered-call release, bounded D-Bus failure, and unavailable declarative shortcut registration. The private mock delays request 2 for 2500 ms, longer than the tick-counted 750 ms timeout. The late reply was ignored; request 3 never replies and its retained QML call is released after the five-second observation window; request 4 exercises the explicit D-Bus error path. Retention is also capped at three calls.

The probe enumerated two managed windows using `Workspace.stackingOrder`: its 2x30 timer-clock window and one 320x268 disposable QWidget, both with app ID `oled-window-guard-runtime-test`. It reported UUID identity, PID, output `Virtual-0`, output/work area geometry, frame geometry and move/resize capability. The opt-in run additionally required exactly one large owned candidate. It moved that window by (12, 8) without changing size, received `frameGeometryChanged`, restored the original position, then enlarged it by 16x12, received another geometry acknowledgement, and restored the original size. It verified unchanged active-window identity and desktop. The observed move round trip was `(467.75, 323.54, 320, 268)` → `(479.75, 331.54, 320, 268)` → `(467.75, 323.54, 320, 268)`; the size-only round trip was `(467.75, 323.54, 320, 268)` → `(467.75, 323.54, 336, 280)` → `(467.75, 323.54, 320, 268)`.

KWin crashed during script destruction while the probe's `Component.onDestruction` accessed the window object. The callback was removed; Qt/QML parent ownership cleans up its child Timer, DBusCall and Connections. Re-running both passive and opt-in nested paths completed unload/reload with exit status 0. In the opt-in path the owned geometry round trip had completed and restored before unload. This was a probe-only lifecycle fix, not a product workaround.

Limitations: XWayland enumeration and geometry were not verified; the earlier XWayland trial could not get the nested `:87` socket. Declarative `KWin.registerShortcut` is unavailable on the tested wrapper. The harness's QQuickWindow is `visible=True, exposed=False`; the virtual session requires `QSG_USE_SIMPLE_ANIMATION_DRIVER=1` for QML Timer advancement. The separate optional `--windowed` path displayed a private nested-compositor window on the host desktop and produced GL framebuffer warnings, so rendering quality is not established. These results establish KWin 6.7.5 behavior only.

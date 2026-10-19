# v1.0.3 release review

Date: 2026-10-19. Supported matrix remains Fedora 44 x86_64, KWin 6.7.5, Qt/PySide6 6.11.2, Plasma Wayland.

## Behavior

Cairo-Dock receives the same exported Qt menu used by OWG's tray. Movement and brightness independently show Start or Pause. Move Now, Skip and Restore previous position are disabled when movement is paused; Restore Brightness is disabled when brightness is paused. Existing readiness, warning and restore checks still apply. The all-guard action starts the selected guards when both are paused and pauses all when either is active.

Fixed desktop-entry actions are removed to avoid duplicate menus. The launcher continues to open OWG when it is closed. The live menu requires the running application, Plasma's status notifier host and Cairo-Dock's D-Bus plug-in with SetMenu/libdbusmenu support. It is optional; other launchers, the dashboard, tray and CLI remain available. Old Cairo-Dock quicklist caches can require one dock reopen after upgrading. Custom copied desktop entries may need replacing with the packaged launcher.

## Implementation and boundaries

The bridge discovers the tray through the status notifier watcher, verifies its bus owner PID against OWG, and accepts one unambiguous menu implementing com.canonical.dbusmenu version 2. It attaches only to class=oled-window-guard. It shares the existing QAction objects, so no separate guard state, intent routing, movement planner or writer is introduced. The bridge never changes settings or launcher configuration.

Optional bounded IPC runs in a short-lived client process from a single background worker. This avoids Qt tray self-call deadlocks and leaves the GUI/controller event loop responsive. Refreshes are serialized and idempotent, cover dock restarts or replaced launchers, and do not overlap. Cleanup follows pending work and checks the dock owner and replacement controller PID before detaching. A missing plug-in, unavailable host, ambiguous export or failed attachment never changes guarding readiness.

The attachment contract was checked against Cairo-Dock's upstream [D-Bus interface](https://github.com/Cairo-Dock/cairo-dock-plug-ins/blob/master/Dbus/src/interface-main-methods.c) and [menu handling](https://github.com/Cairo-Dock/cairo-dock-core/blob/master/src/gldit/cairo-dock-user-menu.c). No external source was copied into the application.

## Validation and distribution

UI tests cover all four independent guard states, action enabled states, warning Skip availability, optional bridge failure and nonblocking execution. Private-session integration uses a real Qt tray export and menu activation with fake Cairo-Dock/watcher services. It checks Start intent dispatch, live labels, enabled states, attachment refresh and cleanup. The same test is repeated against the exact RPM payload, along with launcher CLI routes and the package lifecycle checks. No user windows are moved by these checks.

The native observer is freshly compiled; movement and dimming engines are unchanged from the physically accepted baseline. Settings and login preferences are retained through the existing upgrade coordinator, and OWG reopens paused. Binary RPM, source RPM, complete corresponding source and SHA-256 checksums accompany this release. Earlier releases remain unchanged.

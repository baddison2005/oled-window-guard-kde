# v1.0.1 release review

Date: 2026-10-19. Target remains Fedora 44 x86_64, KWin 6.7.5, Qt/PySide6 6.11.2, Plasma Wayland.

## Changes

The installed desktop entry exposes nine ordered actions: Open OLED Window Guard, Pause movement guarding, Pause brightness guarding, Pause all guarding, Move Now, Skip, Restore previous position, Restore Brightness and Quit. Cairo-Dock consumes these standard actions through its existing [application action loader](https://github.com/Cairo-Dock/cairo-dock-core/blob/master/src/gldit/cairo-dock-class-manager.c) and [menu builder](https://github.com/Cairo-Dock/cairo-dock-core/blob/master/src/cairo-dock-user-menu.c); no dock plugin or new controller is introduced.

The CLI adds pause-movement and pause-brightness routes to the existing single-instance controller. Non-Open commands do not launch a controller when none exists, and refused operations do not start guarding. SessionHost retains all existing capability and safety checks. Dock/CLI Move Now follows the Overview preview display if it is connected and selected for movement; otherwise it falls back to the first connected movement display. An explicit output selection still takes precedence. Restore targets the last verified inverse.

The tray Restore item is renamed Restore previous position. Tray Start/Pause state remains dynamic; desktop action labels are static, and unavailable requests are refused by the controller. Cairo-Dock may require reopening to refresh a cached action list after an RPM upgrade. Existing pinned launchers using the installed desktop entry require no configuration rewrite.

## Validation scope

Release checks cover CLI routing/refusal and no-second-controller behavior, the nine desktop-entry names/order, independent tray pause actions, target-display selection/fallback, the dashboard/session and packaging regressions, desktop-file validation and GLib action discovery/activation on a private D-Bus session. Exact-RPM runtime checks cover fresh paused startup, duplicate Open, controller/adapter recovery, Quit and reopen. The native observer is compiled afresh against matching development headers.

Source validation passed 99 UI/session tests, 41 packaging tests, desktop-file validation, nine Gio action dispatches and eight missing-controller refusals.

The private launcher test uses the same Gio desktop-action API used by Cairo-Dock and records each action reaching an isolated controller stub. It does not claim a physical right-click test of the operator's normal dock. The native geometry and dimming engines are unchanged from v1.0.0; the previous physical acceptance remains the baseline.

## Distribution

Binary RPM, corresponding source RPM, complete application source and SHA-256 checksums are included. RPMs remain unsigned. See README.md and SOURCE.md for the exact supported runtime matrix and build instructions. The v1.0.0 release is retained unchanged.

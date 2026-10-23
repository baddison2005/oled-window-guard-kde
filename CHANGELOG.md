# Changelog

## 1.0.4 — 2026-10-23

- Keep the companion-owned compositor runtime separate from the disabled KWin package toggle, preserving guards across Window Layouts saves and KWin reconfiguration.
- Recover registered but silent runtimes and retry failed starts without requiring a dashboard restart. Unexpected runtime loss recovers paused.
- Add Dim selected display now and Dim all guarded displays now to the live tray and Cairo-Dock menus. Respect brightness scope, monitor settings and app exemptions; interaction or Restore Brightness clears manual dimming.
- Put Care for your OLED on its own subtitle line.

## 1.0.3 — 2026-10-19

- Attach the existing live tray menu to Cairo-Dock, with independent Start/Pause labels for movement and brightness.
- Grey out movement and brightness actions while their guard is paused, retaining controller safety checks.
- Reconnect after dock/launcher reloads and remove duplicate fixed desktop-entry actions.
- Isolate optional dock IPC from the dashboard process and detach on quit.

## 1.0.2 — 2026-10-19

- About now checks GitHub for newer stable releases when requested.
- Offer confirmed Download and upgrade with bounded HTTPS downloads, SHA-256 verification and RPM identity/runtime checks.
- Request administrator authorization through DNF after safely closing OWG; reopen paused with preferences retained.
- Report network, validation and installation failures distinctly, and reopen after cancelled authorization.

## 1.0.1 — 2026-10-19

- Add nine desktop-entry actions for Cairo-Dock and compatible launchers.
- Add independent pause-movement and pause-brightness CLI commands routed to the existing controller.
- Rename the tray Restore item to Restore previous position.
- Make dock/CLI Move Now use the display selected in Overview when available.

## 1.0.0 — 2026-10-18

First public release of OLED Window Guard for KDE Plasma.

- Guarded Shift, Swap and layout-group movement with previews, countdown warnings, grace-period interaction cancellation and verified Restore.
- Ordered multi-window moves, partial-failure reporting and bounded rounding adjustments for tightly arranged windows.
- Independent movement and brightness guarding, display selections, per-display settings and application exemptions.
- Inactive-window and monitor dimming with delays, fades, desktop-click behavior and Restore brightness.
- Optional login/minimized startup and resume after unlock, idle timeout or suspend; fresh startup remains paused.
- Continued guarding on surviving displays after monitor disconnection, with unique monitor identity migration across connectors.
- Movement and brightness status lights, updated help text and package installation documentation.
- Fedora 44 x86_64 RPM for KWin 6.7.5 and Qt 6.11.2, with corresponding source.

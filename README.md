# OLED Window Guard for KDE Plasma

**Keep your windows moving. Care for your OLED.**

OLED Window Guard automatically shifts and rearranges application windows and dims inactive content. Movement and brightness guarding have independent controls, display selections and settings. The application opens paused so you can review your desktop before starting either guard.

Created by **Dr. Brett Addison**. This is the KDE Plasma edition; the [macOS edition](https://github.com/baddison2005/oled-window-guard) is maintained separately.

## Features

- **Shift position:** move windows within a configurable range, with optional horizontal and vertical order changes.
- **Swap positions:** rearrange windows and groups of similar neighbours, including tightly occupied layouts. Small, bounded size adjustments accommodate rounding at adjacent edges.
- **Layout groups:** move within zones or rotate among them. Use built-in layouts or import the read-only library from [Window Layouts for KDE](https://github.com/baddison2005/window-layouts-kde).
- **Movement preview:** view current window locations, application names, exclusions and proposed destinations for the selected display. Previewed destinations are used by Move after warning while the plan remains valid; the warning refreshes the proposal and checks again after its grace period.
- **Warnings and Restore:** a visible countdown, optional sound, Skip, and one-shot Restore of the last verified move, including size changes. Restore previews its destinations and refuses stale geometry.
- **Independent display timers:** select movement displays, set per-display overrides and see when the next check is due. Displays with no eligible windows are checked silently.
- **Window and monitor dimming:** independently set darkness, activation delay and fade duration. Dimming tracks window movement and continues during warnings, moves and restores.
- **Separate app exemptions:** exclude applications from movement, dimming or both. Docks and desktop surfaces are not treated as ordinary application windows.
- **Desktop-click behavior:** keep the clicked display bright or temporarily restore brightness and restart its dimming timers. Other displays retain their timers.
- **Restore brightness:** use the dashboard, tray or **Meta+Shift+B**. The shortcut can be disabled or customised through KDE.
- **Session continuity:** optionally resume previously active guarding after unlock or wake. Guarding on a remaining display continues when another monitor disconnects. Fresh app and system startup remain paused.
- **Desktop integration:** system tray, launch at login, start minimized, independent Start/Pause controls, and labeled movement/brightness status lights.

## Supported system

The v1.0.2 RPM targets **Fedora 44, x86_64, KDE Plasma Wayland, KWin 6.7.5 and Qt/PySide6 6.11.2**. Python 3.11 or newer is required.

The native observer uses version-specific KWin interfaces. The RPM deliberately requires the matching KWin and Qt versions; it is not a generic package for every Plasma release or distribution. A system update to different KWin/Qt versions requires a compatible OWG build. XWayland application windows are supported inside the tested Wayland session; an X11 desktop session is not supported.

## Install

1. Download `oled-window-guard-1.0.2-1.fc44.x86_64.rpm` and `SHA256SUMS` from [v1.0.2](https://github.com/baddison2005/oled-window-guard-kde/releases/tag/v1.0.2).
2. In the download directory, verify the RPM checksum against `SHA256SUMS`:

   ```sh
   sha256sum oled-window-guard-1.0.2-1.fc44.x86_64.rpm
   ```

3. Quit an existing OWG session, then install:

   ```sh
   oled-window-guard quit  # Only needed if OWG is already running.
   sudo dnf install ./oled-window-guard-1.0.2-1.fc44.x86_64.rpm
   ```

4. Open **OLED Window Guard** from the application launcher, or run `oled-window-guard open`.

The release RPM is unsigned; checksums accompany the downloads. Dependencies come from your configured Fedora repositories. In About, choose **Check for updates** to query the latest stable GitHub release, then **Download and upgrade** when a newer compatible RPM is offered. Manual downloads and installation with DNF remain available. Installation does not enable login startup or start guarding.

For an upgrade, quit OWG before installing the new RPM. If you previously installed a user-local development copy, remove it using its installer/ownership manifest before using the RPM so that it cannot shadow the packaged launcher or compositor script.

## In-app upgrades

**About → Check for updates** queries the official GitHub repository only when requested. A newer Fedora 44 x86_64 RPM shows **Download and upgrade**. A failed/offline check is reported as an error rather than as “up to date.” Checking alone does not download an RPM or install anything.

Confirm the upgrade to download it. OWG verifies GitHub's SHA-256 asset digest, the RPM name/version/architecture and its exact KWin/Qt requirements before closing. The session helper closes the existing controller and unloads its compositor runtime, then DNF requests administrator authorization. Settings and login preferences are backed up and retained. The app reopens paused, including after cancelled authorization or an installation failure. The reopened About page reports the result. Failed download or preflight checks leave the running app in place.

Upgrades cannot silently change the compositor/Qt version. The installer uses installed dependencies without enabling remote repositories; if a future release needs additional dependencies, use manual DNF installation with the configured Fedora repositories. RPMs are currently unsigned; verified downloads use the official HTTPS GitHub release and its SHA-256 digest. There is no unattended upgrade, forced downgrade or new autostart opt-in.

## First use

1. In **Overview**, select the movement displays and the display to preview.
2. In **Movement**, choose a movement style, interval and window limit. Per-display overrides are optional.
3. Select **Preview safe moves** to inspect the proposal. Start movement guarding and choose **Move after warning** for an immediate cycle, or wait for the scheduled check.
4. In **Dimming**, select displays and enable the desired effects. Start brightness guarding separately.
5. In **Safety & alerts**, configure warning length, interaction grace, app movement exclusions, launch at login, minimized startup and resume after unlock or wake.

The bottom-left lights show each guard separately: **green = active**, **grey = paused**, **amber = waiting or unavailable**. Text labels also identify the state. Closing the dashboard leaves the tray/controller running; **Quit** stops both guards and removes dimming.

## Cairo-Dock and launcher menu

Right-click the OWG application icon in Cairo-Dock to access **Open OLED Window Guard**, **Pause movement guarding**, **Pause brightness guarding**, **Pause all guarding**, **Move Now**, **Skip**, **Restore previous position**, **Restore Brightness**, and **Quit**. Other launchers that support desktop-entry actions can show the same controls.

Move Now uses the display selected in Overview when it is still selected for movement and connected, otherwise the first connected movement display. Restore previous position restores the last verified move. All existing warnings, capability checks and stale-plan refusals apply. Pause actions only pause; starting a guard remains available from the dashboard or tray.

Launcher action labels are fixed and may remain visible when an operation is unavailable; the controller refuses unavailable operations safely. Cairo-Dock may cache the old action list after an upgrade. If the menu has not refreshed, quit and reopen Cairo-Dock. Existing pinned launchers referring to `/usr/share/applications/oled-window-guard.desktop` pick up the package entry; a standalone copied/custom launcher may need to be replaced with the installed application launcher.

## Interaction and safety behavior

Focused but idle windows may move. During a warning's initial grace period you can finish an interaction. After that cutoff, typing, scrolling, clicking or dragging on the affected display cancels its move. Moving the pointer alone or interacting on an unrelated display does not cancel the warning. Held input and unknown input state still block writes.

If startup readiness asks for input state, click and release on each desktop, then release all held keys and buttons. Readiness details appear in Overview.

Every move is validated against current geometry, exclusions, output identity and native session/input observations. The writer checks each step and reads back the result. A partial or uncertain result pauses movement; it is reported rather than presented as a successful move. Restore is available only while its original window identities and expected geometry remain valid. Manual movement, resizing, closing windows or topology changes can invalidate it.

Full-screen, maximized, minimized, modal, unsupported and spanning windows may be ineligible. Existing substantial overlaps and limited free space can prevent a safe plan. Bounded planning can legitimately return no plan; a warning is shown only when a proposal is available. Adjacent rounding allowances do not permit new substantive final overlaps.

Dimming uses compositor overlays, not hardware brightness controls. Window and display darkness do not add together. Exempt app areas remain bright; a whole-display effect covers the movement warning. **Restore brightness** grants at least five seconds of brightness, respecting longer configured delays. Manual pauses remain paused across unlock/wake; resumption requires the corresponding option and previously active guarding. A disconnected target leaves its guard scope; reconnecting it does not automatically arm new work.

Movement and dimming cannot guarantee prevention of image retention or OLED burn-in. Keep your display's built-in care and sleep features enabled.

## Settings, privacy and troubleshooting

Preferences are stored in `~/.config/oled-window-guard/settings.json` (or under `$XDG_CONFIG_HOME`). Changes save automatically. Runtime window identities, pending moves and Restore authority are memory-only. Uninstalling the RPM preserves your preferences.

OWG uses window geometry, application identity, focus and input activity timing. It does not capture window contents or record typed text. Preview draws rectangles and app names. Imported Window Layouts files are read-only. Routine diagnostic snapshot logging is disabled.

- **Unavailable after an update:** check About and verify the supported KWin/Qt versions. Reopen OWG after installing a compatible build.
- **No eligible windows:** check the selected display, app exemptions, geometry and movement mode. Preview shows eligibility; resizing or rearranging the layout can make a move possible.
- **Restore refused:** the desktop no longer matches the verified move. OWG does not force windows back over subsequent user changes.
- **Shortcut conflict:** resolve Meta+Shift+B in KDE's shortcut settings or disable OWG's shortcut and use Restore brightness in the dashboard.
- **Remove the app:** quit OWG, disable Launch at login if enabled, then run `sudo dnf remove oled-window-guard`.

Report reproducible issues through [GitHub Issues](https://github.com/baddison2005/oled-window-guard-kde/issues), including OWG, Fedora, KWin and Qt versions, display scaling, movement style and the exact status message. Review diagnostics for personal information before attaching them.

## Build and test

See [SOURCE.md](SOURCE.md) and the [RPM build instructions](packaging/rpm/README.md). The release includes a complete source archive and source RPM corresponding to the binary package. Native builds require development headers matching the supported runtime versions.

From the source root, run the non-desktop suites:

```sh
for test in tests/core/*.test.js tests/safety/*.test.js; do node "$test" || exit; done
QT_QPA_PLATFORM=offscreen python3 -m unittest discover -s tests/ui
python3 -m unittest discover -s tests/settings
python3 -m unittest discover -s tests/integration -p 'test_*.py'
python3 -m unittest discover -s tests/packaging
```

`tests/integration/run_*.py` contains separate private-compositor and physical acceptance runners. They are not part of the unit-test commands above; read their prerequisites before running them. Some historical DNF/signing rehearsal runners require private development artifacts that are not included in this repository.

See the [v1.0.2 review](docs/RELEASE_REVIEW_v1.0.2.md) and [changelog](CHANGELOG.md) for release scope and validation.

## License

Copyright © 2026 **Dr. Brett Addison**.

The original KDE controller, dashboard, compositor, native observer, tools and artwork are licensed under **GNU GPL version 3 or later**. Shared settings schema and interface definitions are **MIT** licensed. See [LICENSE](LICENSE), [NOTICE](NOTICE) and [third-party notices](packaging/licenses/THIRD_PARTY_NOTICES.md). The macOS edition has its own licensing terms.

# v1.0.4 release review

Date: 2026-10-23. Fedora 44 x86_64, Plasma Wayland, KWin 6.7.5 and Qt/PySide6 6.11.2 remain the supported matrix.

## Window Layouts and reconfiguration

Window Layouts configuration saves invoke KWin reconfigure; enabling a helper can also invoke the global scripting start method. KWin's [package scan](https://github.com/KDE/kwin/blob/v6.7.5/src/scripting/scripting.cpp) unloads registered scripts whose installed package is disabled. OWG previously registered its externally owned observer using that package ID even though its package intentionally defaults off. Recovery persisted a pause, so unrelated saves could stop both guards. Registration alone also failed to detect a script with no working QML root or heartbeat. KWin 6.7.5 assigns new script IDs from its current list length; holes in that list can cause a per-ID D-Bus path collision, so a successful run call may target another script. Startup now uses the global scripting entry point, which invokes registered objects directly. The separate runtime identity survives its package scan. Fresh observer contact also bypasses synchronous watchdog bus probes on the GUI thread.

The package remains disabled by default. The companion now loads its runtime as `oled-window-guard-runtime`, separate from `oled-window-guard-adapter`. Global package scans leave the running companion-owned runtime intact. No KWin configuration or Window Layouts files are changed. Startup removes legacy and current orphan registrations; Quit unloads the owned runtime. The updater checks both names before installation. Its existing helper in v1.0.3 remains usable for the first upgrade because that version unloads the legacy name.

Recovery also checks actual observer contact, tolerates startup, retries failed starts with a bounded interval, and replaces a registered but silent runtime. Unexpected module loss still cancels work and recovers paused; it never reuses an old plan or inverse or arms a new writer automatically. Fresh app/system startup remains paused. Ordinary reconfiguration preserves the same root/native observer, guarding state and intervals.

## Manual monitor dimming and UI

The tray menu, shared live with Cairo-Dock, adds Dim selected display now and Dim all guarded displays now. The selected action uses Overview's display; the all action resolves the current brightness scope at execution. Both require active, healthy brightness guarding and configured monitor dimming with nonzero darkness. Compositor checks repeat the scope and safety validation.

Manual dimming skips the activation delay while retaining the configured fade, darkness and app exemptions. It can dim focused content. Interaction on the affected display clears its manual dimming; other displays retain their state. Restore Brightness, pause and unsafe lifecycle transitions remove the effect. A manual request during Restore Brightness does not end another display's bright hold. Movement previews and writer authority are unchanged. The sidebar subtitle has an explicit line break before Care for your OLED.

## Verification and distribution

Tests cover runtime identity, registered-silent recovery, failed-start retry, scoped/all manual dimming, disabled menu actions, output rejection, retained movement previews, restore holds, interaction scope and fail-bright cleanup. The private-compositor continuity fixture reproduces the old unload and checks the real adapter/native object across repeated global reloads. Its guard flags are seeded with its scheduler disconnected; it does not claim physical input or movement acceptance. Exact-RPM checks separately exercise the production companion, repeated reloads without changing observer epoch or settings, silent-runtime recovery, lifecycle and updater handoff. Native source is unchanged and the release rebuilds the observer against matching headers.

The release includes binary RPM, source RPM, complete corresponding application source and checksums. Existing settings are retained. The operator installs through About → Check for updates; development does not automatically upgrade the installed app.

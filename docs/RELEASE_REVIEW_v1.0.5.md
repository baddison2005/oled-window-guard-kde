# v1.0.5 release review

Date: 2026-10-23. Supported matrix remains Fedora 44 x86_64, Plasma Wayland, KWin 6.7.5 and Qt/PySide6 6.11.2.

## Named monitor actions

The shared tray/Cairo-Dock menu replaces the ambiguous Overview-selected dimming target and single Move Now entry with actions naming each connected monitor selected for movement or brightness guarding. Labels include the connector when available. Stable actions are updated in place; disconnected targets are removed. Each callback captures its own output identity, independent of Overview. Existing guard, planning and compositor safety checks still apply. Monitor restore entries require visible, confirmed renderer masks. Restore brightness on all displays and Dim all guarded displays now remain available when applicable.

A scoped RestoreBrightness command carries an output identity validated against the current renderer report at the companion and compositor. Only that output's effects and clocks are cleared, with an independent restore hold based on its configured delays. Other outputs retain their clocks, masks and manual dimming. Explicit dimming can override its target's hold. The existing all-output shortcut, dashboard action and CLI retain their global restore behavior. No movement plan, inverse or geometry authority is acquired by brightness commands.

## Shell interaction and pointer attribution

Dock clicks were classified as desktop clicks, and transient shell focus could make a dimmed output bright. Shell panel, dock and menu interactions now remain separate from application/desktop input. Shell focus preserves the previous visible application's dimming focus, and shell events do not clear forced monitor dimming. Real application and empty-desktop interactions retain their existing scoped behavior.

A read-only native query returns KWin's current pointer-input recipient. It respects input regions, avoiding attribution to a transparent auto-hide dock rectangle over an application. The dimming classifier uses that identity when available, with geometric classification as a fallback. The query never writes application state or grants movement authority. Native source and its matching-header binary are included in the release; immutable runtime/module identities prevent stale code reuse after upgrade.

## Validation and remaining operator check

Regression coverage includes explicit monitor dispatch independent of Overview, live menu availability and disconnection, scoped restore validation and independent timers/masks, shell focus/click classification, and transparent dock input-region attribution. Private KWin checks exercise actual dimming planes, the exported Qt/Cairo menu and native observer startup/reload. Final RPM lifecycle checks cover paused startup, KWin reconfiguration, recovery and updater handoff. Synthetic facts in isolated rendering checks do not establish physical panel/dock behavior.

After the operator upgrades through About → Check for updates, confirm that right-clicking either icon leaves dimming intact, named monitor actions affect only that monitor, and restore-all clears both. Existing settings are retained and the reopened app starts paused. Development does not automatically install the release.

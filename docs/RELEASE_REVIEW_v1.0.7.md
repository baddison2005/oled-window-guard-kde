# v1.0.7 emergency compatibility review

Date: 2026-11-02. Build target: Fedora 44 x86_64, Plasma Wayland, KWin/Plasma 6.8.2, Qt/PySide6 6.11.2. The observer is rebuilt against the signed Fedora kwin-devel 6.8.2-2 headers and matching installed libraries. The used C++ APIs compile without source changes; isolated runtime checks exercise their behavior. Updated header hashes and upstream notices are included.

## Desktop upgrade recovery

Exact runtime requirements in earlier releases could prevent KWin and Qt from updating with the rest of KDE. The local 1.0.6-2 recovery package removed those requirements while deliberately retaining the old native target. This release keeps unversioned runtime dependencies and exact build dependencies. Automatic requirements/provides for the optional native ELF are excluded, preventing its historical SONAME from retaining obsolete desktop libraries. Required system runtime packages remain explicitly declared.

The updater reads `oled-window-guard-native-kwin` and `oled-window-guard-native-qt` build-target provides, which do not constrain system packages. Missing, conflicting or mismatched target declarations are rejected. Releases predating those declarations retain their exact-requirement validation. DNF remains responsible for the administrator-authorized installation transaction.

## Native import gate

The real running compositor's `supportInformation` response must report one exact KWin and Qt version matching the validated, stamped native build metadata. Unknown, malformed, ambiguous, failed, mismatched or timed-out responses never import the native module. Late and duplicate responses cannot reopen a closed gate. The bundled loader cannot fall back to an older globally registered module after a packaged import failure. Existing native context/version/thread checks remain a second layer; guarding requires fresh compositor-local observation and cancellation capability.

This permits future desktop updates while leaving OWG unavailable on an unverified runtime until a matching build is installed. It does not claim support for all KWin 6.8 releases or future versions. Fresh startup remains paused. Existing user preferences are retained; the settings schema is unchanged.

## Panel and dock menus

The upgraded Plasma platform backend exports DBusMenu version 4 instead of the previous Qt version 2. The dock bridge now accepts those two known versions while retaining PID ownership checks and refusing unknown versions. Private compositor checks exercise the actual exported layout and action events, including named monitor actions and the single last-move restore.

Older v1.0.6 updater code expects exact runtime requirements and cannot validate the new declarations. The emergency transition therefore uses the manual DNF installation path; v1.0.7’s updater understands both old and new formats.

## Verification and acceptance

Tests cover native preflight refusal and matching responses, updater target declarations and legacy validation, dependency diagnostics and core safety behavior. Private KWin sessions check native startup, session-owner transitions, immutable reloads, real dimming planes and the complete packaged companion. The isolated DNF fixture uses the real RPM and stub future KWin/Qt packages to check that dependency constraints allow a proposed upgrade without removing OWG; no DNF transaction executes and no host package database is changed. Stub packages establish solver freedom, not future compositor compatibility.

On 2026-11-02, Dr. Brett Addison confirmed physical movement, Restore previous position, dimming and Restore Brightness with the installed v1.0.7 candidate, then authorized emergency publication. Installed-runtime checks confirmed the RPM-owned companion/native observer, readiness and retained settings. Final publication updates the README and this review; all functional payload bytes must match the accepted candidate. The release includes the binary RPM, source RPM, complete corresponding source and checksums.

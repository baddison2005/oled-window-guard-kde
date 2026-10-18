# v1.0.0 release review

Review date: 2026-10-18. Target: Fedora 44 x86_64, KWin 6.7.5, Qt/PySide6 6.11.2, Plasma Wayland.

## Scope

Review covers the dashboard, status presentation, settings and startup integration, native observer/session continuity, guarded execution and Restore boundaries, output retention, dimming behavior and release packaging. It includes comparison with the macOS dashboard at commit `c02eab7ba93b0013c4a111f119403191bc5e13c4`, verified as the upstream HEAD on the review date. This is a focused release review supported by regression tests, not a claim of exhaustive proof or a third-party security audit.

## Findings addressed

- Added independent labeled movement/brightness indicators: green active, grey paused, amber waiting/unavailable. Pending session resumption is distinguished from active guarding.
- Corrected Dimming help that incorrectly said warnings/movement clear dimming.
- Removed obsolete About text describing wake-from-sleep as unverified, following the operator's physical acceptance.
- Corrected update guidance: GitHub distributes downloadable RPMs; a Fedora update repository is not configured.
- Promoted application/KPackage/RPM version metadata to 1.0.0 and included the README, changelog and review with source delivery.
- Prepared a clean publication tree excluding historical logs, screenshots containing the operator's desktop, settings, SDK downloads and previous build artifacts.

The KDE UI retains the macOS page structure, product wording, display-specific preview/actions, movement choices and sliders while presenting KDE-specific readiness and shortcuts. Independent guarding controls and status lights reflect the KDE runtime state.

## Validation

All source suites pass: 40 JavaScript core/safety test files, 95 UI/session tests, 31 settings tests, 329 integration unit tests and 41 packaging tests. The planner subprocess and KConfig settings tests required execution outside the development sandbox; those reruns passed. A clean publication-tree packaging run also passes.

A fresh native compiler build produced the v1.0.0 RPM using extracted matching development headers and the installed runtime libraries. No precompiled native-module override was used. Exact packaged private KWin validation passes 11 session-resume cases, 15 independent-control cases, seven output-retention cases and 18 movement/Restore/audio/exclusion/dimming regressions. RPM startup, duplicate Open, orphan-controller replacement, adapter recovery, Quit and reopen also pass. These private runs do not operate on the user's normal desktop.

All six UI pages were rendered in light and dark palettes for layout review. Overview, Movement, Safety & alerts, Dimming and About were visually inspected; text states and accessible names accompany the indicators. Scrolling remains available for settings that extend beyond the window.

No unresolved release blocker was found in the reviewed paths. The existing operator acceptance confirms movement, Restore, warning cancellation, dimming/exemptions, tight Swap/group layouts, suspend/resume, idle timeout, lock/unlock and guarding continuity on the remaining display after disconnection. These are operator observations accumulated during development, not a newly repeated physical matrix on the release build.

## Distribution scope

The package intentionally requires exact KWin/Qt versions because it contains a native observer using KWin interfaces. Other distributions and runtime versions are not certified by this release. The release RPM is unsigned and distributed with SHA-256 checksums, complete corresponding source and a source RPM. No disposable development signing identity is used for publication. Future signed distribution can be added separately.

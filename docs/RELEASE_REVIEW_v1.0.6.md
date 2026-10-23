# v1.0.6 release review

Date: 2026-10-23. Supported matrix remains Fedora 44 x86_64, Plasma Wayland, KWin 6.7.5 and Qt/PySide6 6.11.2.

The shared Restore previous position menu action inherited the Overview restore button's availability, which required Overview's selected display to equal the last move's output. With HDMI selected, an LG move could have a valid inverse but no enabled restore action.

Restore now follows the runtime's verified `restoreOutputId`, independent of the preview selection. The target must remain connected, selected and active for movement guarding. Readiness, planning, transaction and restore availability checks remain in place. The menu names the target monitor; dispatch explicitly pins that output and selects it in Overview so the restore preview is shown. A changed inverse between display and submission remains subject to the session/compositor target-mismatch checks.

The operator agreed to retain one restore action and one global last-move record. No per-monitor history, controller protocol, native source or compositor writer changes are introduced. Existing geometry validation, warnings and stale-restore refusal remain authoritative. The native module is rebuilt from unchanged source for the release package.

Regression tests cover both LG-last and HDMI-last cases while Overview selects the other monitor, monitor naming, preview selection, and disabled actions for paused, busy, disconnected and inactive targets. Private exported-menu checks dispatch Restore for both monitors; exact-RPM lifecycle and menu checks validate packaged behavior. Physical confirmation after the operator upgrades remains separate. Development does not automatically install the release.

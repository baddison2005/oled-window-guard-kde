# v1.1.0 warning placement review

Date: 2026-11-12. Supported matrix remains Fedora 44 x86_64, Plasma Wayland, KWin/Plasma 6.8.2 and Qt/PySide6 6.11.2. The native observer is rebuilt with the matching signed Fedora kwin-devel 6.8.2-3 headers; native C++ source is unchanged.

## Settings and behavior

Safety & alerts adds Warning dialog position with Top left, Top centre, Top right and Random within top 25%, matching the published macOS choices. Existing KDE settings default to their previous top-centre position; the new optional enum keeps schema version 1 and does not rewrite settings merely by reading them. Invalid values follow the existing settings validation policy.

A pure geometry helper uses logical compositor coordinates and both the full monitor rectangle and its usable work area. Fixed choices retain KDE's existing 12-pixel inset. Random placement fits the whole card inside the monitor's top quarter where possible, reducing its top inset if needed. If the card cannot fit in that band, it stays at the work area's top edge and still varies horizontally. It remains inside the usable area on narrow displays, negative-origin layouts and fractional logical coordinates. Work-area edges rounded outward by up to one logical pixel are intersected with the full monitor bounds before placement.

Manual movement warnings, single-window writer warnings and ordered/group Restore warnings pass the validated setting and target monitor geometry to the same presenter. Position is sampled once at show time and retained through confirmation, countdown and dismissal. Existing internal-window ownership checks, geometry readback, no-focus behavior, countdown start on confirmed visibility, grace-period cancellation, dimming masks and confirmed removal before application writes remain intact. No application geometry is assigned by the placement helper.

## Validation and distribution

Tests cover persistence, legacy defaults, invalid settings, UI choices, every warning route, fixed anchors, random bounds and variation, panels, negative/fractional origins and short-screen fallbacks. A private KWin fixture checks actual warning-window readback, stable placement, focus preservation, Skip/hide cleanup and both movement/Restore presentations on two virtual monitors. Final RPM checks repeat placement against the packaged component and exercise controller lifecycle, UI rendering, menu dispatch and package dependencies. Virtual checks do not claim a new physical input acceptance run.

The release includes binary RPM, source RPM, complete corresponding source and checksums. The installed application is left for the operator to upgrade through About → Check for updates → Download and upgrade, preserving preferences and reopening paused.

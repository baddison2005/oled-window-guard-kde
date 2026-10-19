# Screenshots and demonstrations

These images show OLED Window Guard for KDE Plasma v1.0.3 on Fedora 44 / Plasma Wayland. Static screenshots were supplied by Dr. Brett Addison; the demonstrations were recorded on the LG display on 2026-10-19.

- `animations/movement-and-restore.gif`: three application windows move after the warning and all three return after the Restore warning. Both countdowns play at normal speed.
- `animations/dimming-and-restore.gif`: inactive-window dimming, whole-monitor dimming and Restore Brightness. Movement guarding is paused throughout. Idle waiting between stages is trimmed; fades play at normal speed. The ending holds the fully restored desktop for ten seconds after OWG confirms that all dimming masks are cleared.

The looping GIFs are 1280 × 588 with movement at up to 12 frames per second and dimming at up to 10, using palettes of up to 256 colours and captions above the desktop capture. Colour encoding and frame optimisation keep the files suitable for the README. GIFs have no audio. They show the real compositor output; window movements and dimming were not simulated.

The main README includes the Overview screenshot, both demonstrations, and expandable galleries for movement, warnings, safety, dimming, layout groups, desktop menus and update checks. Original screenshot filenames are preserved.

Raw recordings and capture logs remain local under `build/readme-media/` and are not published with the repository. These documentation assets do not require an application upgrade.

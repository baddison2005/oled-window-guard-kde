# v1.0.8 KDE theme review

Date: 2026-11-02. Supported matrix remains Fedora 44 x86_64, Plasma Wayland, KWin/Plasma 6.8.2 and Qt/PySide6 6.11.2.

A background/terminal launch without KDE desktop theme environment variables gave Qt's generic Fusion light palette even though the user's KDE colour configuration remained dark. A fresh-process desktop probe reproduced the white Window/Base colours; selecting the installed KDE Qt platform integration restored the configured dark colours and Breeze style.

Before creating its QApplication, OWG now defaults an unset `QT_QPA_PLATFORMTHEME` to `kde`. An explicitly configured platform theme is retained. Existing QApplication instances are not replaced. The unversioned plasma-integration runtime dependency makes the backend available without pinning KDE package versions. KDE remains responsible for colours, fonts, widget styling and live theme changes; the existing palette-change stylesheet refresh remains intact. No global desktop configuration or OWG user preference is changed.

Fresh-process render checks remove desktop/theme environment variables and use isolated KDE colour configurations for dark and light schemes. They verify the actual Qt colour roles, render every settings page without controller intents, and retain an explicit alternate platform-theme choice. Final RPM UI/menu and lifecycle checks validate the packaged frontend. Movement, dimming, writer authority and the v1.0.7 compatibility gate are unchanged; native source is unchanged and rebuilt against matching headers.

The release includes binary RPM, source RPM, complete corresponding source and checksums. Installation remains user-controlled through About → Check for updates → Download and upgrade. The tested installed v1.0.7 app is not automatically replaced during publication.

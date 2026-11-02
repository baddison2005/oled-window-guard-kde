# Native builds currently omit separate debug information.
%global debug_package %{nil}
%global app_id oled-window-guard
%global app_root %{_libexecdir}/oled-window-guard
%global kpackage_id oled-window-guard-adapter
# This optional, preflight-gated plugin must not keep an obsolete KWin SONAME
# installed after a future desktop ABI transition. Runtime packages are explicit.
%global __requires_exclude_from ^%{_datadir}/kwin/scripts/%{kpackage_id}/.*libowg_native_observer\.so$
%global __provides_exclude_from ^%{_datadir}/kwin/scripts/%{kpackage_id}/.*libowg_native_observer\.so$
%{!?owg_native_prefix:%global owg_native_prefix /usr}

Name:           oled-window-guard
Version:        1.0.8
Release:        %{?owg_release}%{!?owg_release:1}%{?dist}
Summary:        Window movement and software dimming for KDE Plasma
License:        GPL-3.0-or-later AND MIT
Source0:        %{name}-%{version}.tar.gz
BuildRequires:  python3
%if !%{defined owg_native_module}
BuildRequires:  gcc-c++
BuildRequires:  kwin-devel = 6.8.2
BuildRequires:  qt6-qtbase-devel = 6.11.2
BuildRequires:  qt6-qtdeclarative-devel = 6.11.2
BuildRequires:  kf6-kcoreaddons-devel
BuildRequires:  libepoxy-devel
BuildRequires:  pixman-devel
BuildRequires:  libinput-devel
BuildRequires:  libxcb-devel
BuildRequires:  xcb-util-wm-devel
BuildRequires:  xcb-util-cursor-devel
BuildRequires:  xcb-util-keysyms-devel
%endif

# Build dependencies remain pinned to the exercised native target. Runtime
# packages must be free to update together. The QML loader checks running
# KWin/Qt versions BEFORE importing the bundled native plugin.
Requires:       python3 >= 3.11
Requires:       python3-pyside6
Requires:       python3-rpm
Requires:       /usr/bin/pkexec
Requires:       /usr/bin/dnf
Requires:       kwin
Requires:       qt6-qtbase
Requires:       qt6-qtdeclarative
Requires:       kf6-kcoreaddons
Requires:       plasma-integration
# Build-target declarations inform the updater without constraining the system
# packages. Unknown future ABIs are refused before the native ELF is imported.
Provides:       oled-window-guard-native-kwin = 6.8.2
Provides:       oled-window-guard-native-qt = 6.11.2

%description
OLED Window Guard provides a desktop dashboard and guarded KWin window
movement with warnings, input cancellation and Restore. The installed
application always starts paused. Software dimming darkens inactive windows
and selected monitors, with temporary Restore Brightness and fail-bright cleanup.

%prep
%autosetup -n %{name}-%{version}

%build
%if %{defined owg_native_module}
# Development lifecycle tests may reuse a source/hash-validated native build.
python3 tools/build_kwin_package.py built-kwin-package --native-module "%{owg_native_module}"
%else
native_build_dir=$(mktemp -d /tmp/owg-rpm-native.XXXXXX)
python3 tools/build_native_observer.py --sysroot %{owg_native_prefix} --output "$native_build_dir"
python3 tools/build_kwin_package.py built-kwin-package --native-module "$native_build_dir"
%endif
# Retain version and binary provenance inside the installed package.
# The temporary compiler output is not installed globally or enabled here.

%install
install -d %{buildroot}%{app_root}/oled_window_guard
install -d %{buildroot}%{app_root}/schema
install -d %{buildroot}%{_bindir}
install -d %{buildroot}%{_datadir}/applications
install -d %{buildroot}%{_datadir}/kwin/scripts/%{kpackage_id}/contents/ui

install -m 0644 oled_window_guard/*.py %{buildroot}%{app_root}/oled_window_guard/
install -m 0644 schema/settings.schema.json %{buildroot}%{app_root}/schema/
install -m 0755 packaging/rpm/oled-window-guard-launcher %{buildroot}%{_bindir}/oled-window-guard
install -m 0644 packages/desktop/oled-window-guard.desktop %{buildroot}%{_datadir}/applications/
install -m 0644 built-kwin-package/metadata.json %{buildroot}%{_datadir}/kwin/scripts/%{kpackage_id}/metadata.json
install -m 0644 built-kwin-package/COPYING built-kwin-package/NOTICE %{buildroot}%{_datadir}/kwin/scripts/%{kpackage_id}/
cp -a built-kwin-package/contents/. %{buildroot}%{_datadir}/kwin/scripts/%{kpackage_id}/contents/

install -D -m 0644 assets/sounds/warning.wav %{buildroot}%{app_root}/assets/sounds/warning.wav

# Ship the original product icon for the launcher, tray and dashboard.
install -D -m 0644 assets/icons/oled-window-guard.png %{buildroot}%{app_root}/assets/icons/oled-window-guard.png
install -D -m 0644 assets/icons/oled-window-guard.png %{buildroot}%{_datadir}/icons/hicolor/512x512/apps/oled-window-guard.png

%check
# RPM's post-install processing may strip the ELF; record the shipped bytes.
python3 - <<'PYINFO'
import hashlib, json
from pathlib import Path
package=Path("%{buildroot}%{_datadir}/kwin/scripts/%{kpackage_id}")
paths=list(package.rglob("build-info.json"))
assert len(paths)==2, "canonical and immutable native metadata required"
for path in paths:
    info=json.loads(path.read_text())
    assert info["kwinVersion"]=="6.8.2" and info["qtVersion"]=="6.11.2"
    binaries=list(path.parent.rglob("libowg_native_observer.so"))
    assert len(binaries)==1
    info["unstrippedPluginSHA256"]=info.get("unstrippedPluginSHA256",info["pluginSHA256"])
    info["pluginSHA256"]=hashlib.sha256(binaries[0].read_bytes()).hexdigest()
    path.write_text(json.dumps(info,indent=2)+"\n")
PYINFO
python3 -m json.tool schema/settings.schema.json >/dev/null
python3 -m json.tool packages/kwin-script/metadata.json >/dev/null
sh -n packaging/rpm/oled-window-guard-launcher
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate packages/desktop/oled-window-guard.desktop
fi

%files
%license LICENSE NOTICE packaging/licenses/MIT.txt packaging/licenses/GPL-2.0-or-later.txt packaging/licenses/LGPL-3.0.txt packaging/licenses/THIRD_PARTY_NOTICES.md packaging/licenses/native-header-notices.json
%doc SOURCE.md README.md CHANGELOG.md
%doc packaging/rpm/RELEASE-LICENSE-GATE.txt
%{_bindir}/oled-window-guard
%{app_root}/
%{_datadir}/applications/oled-window-guard.desktop
%{_datadir}/icons/hicolor/512x512/apps/oled-window-guard.png
%{_datadir}/kwin/scripts/%{kpackage_id}/

%changelog
* Mon Nov 02 2026 Dr. Brett Addison - 1.0.8-1
- Select KDE Qt theme integration for background and terminal launchers
- Honor desktop light/dark colours and explicit platform-theme overrides

* Mon Nov 02 2026 Dr. Brett Addison - 1.0.7-1
- Support KWin 6.8.2 without constraining future KDE package upgrades
- Gate native imports on running versions and declare updater build targets

* Mon Nov 02 2026 Dr. Brett Addison - 1.0.6-2
- Allow desktop upgrades without holding back KWin or Qt runtime packages
- Check running compositor versions before importing the native observer
- Keep guarding disabled on unvalidated KWin versions pending compatibility testing

* Fri Oct 23 2026 Dr. Brett Addison - 1.0.6-1
- Restore the last verified move independently of the Overview display
- Name the restore monitor in menus and select it for the restore preview

* Fri Oct 23 2026 Dr. Brett Addison - 1.0.5-1
- Name monitor targets in movement, dimming and brightness restore menus
- Preserve dimming through shell menus and support per-monitor restore holds

* Fri Oct 23 2026 Dr. Brett Addison - 1.0.4-1
- Preserve the companion-owned observer across KWin/Window Layouts reloads
- Recover registered silent runtimes and add scoped manual monitor dimming

* Mon Oct 19 2026 Dr. Brett Addison - 1.0.3-1
- Reuse the live tray menu in Cairo-Dock with independent Start/Pause controls
- Disable unavailable guard actions and remove duplicate fixed launcher actions

* Mon Oct 19 2026 Dr. Brett Addison - 1.0.2-1
- Add explicit GitHub update checks and verified, authorized in-app RPM upgrades
- Preserve settings and reopen paused after upgrade or cancelled authorization

* Mon Oct 19 2026 Dr. Brett Addison - 1.0.1-1
- Add Cairo-Dock desktop actions and independent CLI pause controls
- Rename tray Restore and use the Overview display for dock Move Now

* Sun Oct 18 2026 Dr. Brett Addison - 1.0.0-1
- First public release: guarded movement, layout groups and independent dimming
- Independent status lights and session/display continuity

* Fri Sep 25 2026 OLED Window Guard contributors - 0.1.0-1
- Add Fedora 44 / KWin 6.7.5 release package scaffold

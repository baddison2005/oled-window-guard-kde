# Native builds currently omit separate debug information.
%global debug_package %{nil}
%global app_id oled-window-guard
%global app_root %{_libexecdir}/oled-window-guard
%global kpackage_id oled-window-guard-adapter
%{!?owg_native_prefix:%global owg_native_prefix /usr}

Name:           oled-window-guard
Version:        1.0.3
Release:        %{?owg_release}%{!?owg_release:1}%{?dist}
Summary:        Window movement and software dimming for KDE Plasma
License:        GPL-3.0-or-later AND MIT
Source0:        %{name}-%{version}.tar.gz
BuildRequires:  python3
%if !%{defined owg_native_module}
BuildRequires:  gcc-c++
BuildRequires:  kwin-devel = 6.7.5
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

# PySide6 supplies Qt 6 and QtDBus bindings. KWin is constrained to the
# exercised target. The passive native observer is bundled in the script;
# test injectors are never packaged. Runtime mismatch still fails closed.
Requires:       python3 >= 3.11
Requires:       python3-pyside6
Requires:       python3-rpm
Requires:       /usr/bin/pkexec
Requires:       /usr/bin/dnf
Requires:       kwin = 6.7.5
Requires:       qt6-qtbase = 6.11.2
Requires:       qt6-qtdeclarative = 6.11.2

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
    assert info["kwinVersion"]=="6.7.5" and info["qtVersion"]=="6.11.2"
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

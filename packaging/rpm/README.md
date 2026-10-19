# Fedora RPM build

The RPM includes the Python/Qt dashboard, settings schema, desktop entry, icon,
warning sound and KWin package with its passive native observer. Test injectors
are not installed. Runtime dependencies require Fedora's KWin 6.7.5 and Qt 6.11.2;
Python/PySide6 are provided by the system. The app starts paused and login startup
is opt-in.

Install the matching development dependencies listed in [SOURCE.md](../../SOURCE.md),
plus `rpm-build`, then build from the source root:

```sh
rpm_top=$(mktemp -d /tmp/owg-rpm.XXXXXX)
mkdir -p "$rpm_top"/{SOURCES,SPECS,BUILD,BUILDROOT,RPMS,SRPMS,tmp}
python3 packaging/rpm/make-source.py
cp oled-window-guard-1.0.2.tar.gz "$rpm_top/SOURCES/"
rpmbuild -ba --define "_topdir $rpm_top" --define "_tmppath $rpm_top/tmp" packaging/rpm/oled-window-guard.spec
```

The resulting binary RPM is under `$rpm_top/RPMS/x86_64/`; corresponding source
RPM is under `$rpm_top/SRPMS/`. Retain the source archive with binary releases.
The builder verifies native source and binary hashes. The RPM records hashes
again after its ELF processing so metadata describes shipped bytes.

An extracted matching development prefix may be supplied with
`--define 'owg_native_prefix /absolute/path/to/usr'`. This compiles the native
observer from source against that prefix and installed matching runtime libraries.
`--define 'owg_release N'` chooses a package release number. The optional
`owg_native_module` override reuses an already validated native module for
private lifecycle development; official builds compile it afresh.

The original KDE implementation is GPL-3.0-or-later; shared schema and protocols
are MIT. The package includes full notices. See [source instructions](../../SOURCE.md).
The v1.0.2 RPM is unsigned; the GitHub release provides SHA-256 checksums and
corresponding source. No signing rehearsal key is used as a release identity.

Private RPM runtime validation uses an extracted payload and isolated KWin:

```sh
python3 tests/integration/run_rpm_runtime.py /path/to/release.rpm
```

This requires the supported desktop runtime. Historical DNF/signing runners in
`tests/integration/` are development tools requiring their documented private
artifacts and isolation; they are not an installation procedure for users.

# v1.0.2 release review

Date: 2026-10-19. Supported matrix remains Fedora 44 x86_64, KWin 6.7.5, Qt/PySide6 6.11.2, Plasma Wayland.

## Behavior

About loads the installed RPM version locally at startup. Check for updates explicitly queries the official repository's latest stable GitHub release. A newer compatible Fedora/architecture asset enables Download and upgrade; failed checks never claim that the app is up to date. Confirmation starts the download in the background, without freezing the dashboard or closing the running controller on a download/preflight failure.

An independent user-session helper revalidates the download, backs up settings/login preferences, authenticates the requesting controller PID on the session bus, closes it and verifies that its compositor script unloaded. It then asks PolicyKit to run DNF for the single verified local RPM. DNF uses installed dependencies without remote repository updates. Installation success is verified against the expected RPM version and installed file hashes, with preferences checked for byte preservation. The helper reopens the app paused after success or after an authorization/installation failure; About reports the outcome. No guard is automatically armed.

## Validation and trust boundaries

The checker accepts only stable numeric version tags and a single Fedora 44 x86_64 RPM from the named repository's exact release URL. Redirects remain HTTPS on GitHub's release infrastructure. Metadata/download sizes and network waits are bounded. The asset must have the SHA-256 digest described by the [GitHub release API](https://docs.github.com/en/rest/releases/releases). Download size, checksum, RPM name/version/release/epoch/architecture and matching installed KWin/Qt requirements are rechecked before installation. A download older than or equal to the installed RPM is refused.

Tests cover update comparison, current/older releases, offline checks, invalid release/assets/digests, unsafe redirects, corrupt/incomplete/cancelled downloads, tampered or incompatible RPMs, command invocation without a shell, settings retention, successful/cancelled/failed transactions and preflight refusal without closing the app. UI cases exercise explicit network checks, confirmation, offer visibility and failed-download recovery. A private exact-RPM session exercises real controller/adapter shutdown and paused restart with administrator cancellation substituted for the privileged transaction.

The live GitHub check on the operator's installed v1.0.0 correctly discovers v1.0.1 as newer during development. Source UI/session and packaging suites are used along with the exact packaged launcher and lifecycle regressions. The native observer is freshly compiled; geometry and dimming engines are unchanged from the physically accepted baseline.

## Distribution

The first upgrade from a version without this updater requires installing the v1.0.2 RPM. Later updates can use About. The package adds explicit RPM Python bindings, DNF and PolicyKit runtime requirements. RPMs remain unsigned; downloads are authenticated through GitHub HTTPS and its published SHA-256 digest. Complete corresponding source, source RPM and checksums accompany the binary. Earlier releases remain unchanged.

# v1.2.0 Fedora compatibility review

OWG builds from one source release in separate Fedora 44 and 45 x86_64 environments. Both builds use the matching KWin 6.8.3 and Qt 6.11.2 development packages. The KWin header provenance manifest records the signed Fedora packages and per-release file hashes.

The native observer still checks the running compositor versions before loading. RPM runtime dependencies permit normal desktop updates, while the build-target capabilities let the updater check the installed KWin and Qt versions before installation. The Fedora release and architecture determine which GitHub asset is offered. A mismatched Fedora target is rejected both before download and during installation preflight.

On Fedora 45, use the Fedora 45 RPM to upgrade from v1.1.0 or earlier. Fedora 44 users on v1.0.7 through v1.1.0 can use their existing in-app updater if their desktop has the supported KWin and Qt versions. Settings retain the existing schema and package paths.

The release checks cover both RPM identities and dependencies, matching update offers, cross-release rejection, settings retention and paused startup in private virtual KWin sessions. Warning placement and cleanup are exercised using each RPM's packaged component. The virtual sessions do not exercise physical input, GPU or monitor hardware. The release includes binary RPMs, source RPMs, corresponding source and SHA-256 checksums.

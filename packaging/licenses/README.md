# Adopted GNU GPL licensing

On October 12, 2026, Dr. Brett Addison selected GPL-3.0-or-later for the KDE
controller/dashboard and compositor. The grant covers all original KDE project
implementation, build tools, tests, packaging and KDE copies of original artwork.
The root LICENSE contains the full GPLv3; NOTICE declares the or-later grant,
copyright, warranty disclaimer and scope. The shared schema and protocol
definitions retain the previously approved MIT terms, also included in their
own directories. Dependencies keep their existing licenses and notices.

The RPM expression is `GPL-3.0-or-later AND MIT`; the KPackage implementation
license is `GPL-3.0-or-later`. No evaluation-only restrictions apply to this
KDE source. Earlier mixed-license drafts are archived under
`docs/evidence/license-review-2026-10-12/superseded-mixed-license-proposal/`.
Historical artifacts do not override the adopted terms. The macOS repository
and its license are unchanged.

The original-project rights and attribution were confirmed by the owner.
GPLv3 is compatible with the inspected GPL-2.0-or-later KWin headers and LGPLv3
Qt dependencies. The former proprietary-controller/GPL-compositor IPC boundary
gate no longer applies. Third-party notices and exact header provenance remain
in THIRD_PARTY_NOTICES.md and native-header-notices.json.

See SOURCE.md at the project root for source bundling and build instructions.
A binary release must provide its exact corresponding source and meet applicable
dependency obligations. This change does not publish a release, create a signing
identity, change native implementation bytes or deploy to the active desktop.

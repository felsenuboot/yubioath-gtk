# Changelog

All notable changes to YubiOath. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/) and stay at 0.x until the feature
set settles.

## [Unreleased]

Towards v0.3.0 (Efficiency) and v0.4.0 (Portability).

### Changed
- Tray: `LayoutUpdated` is emitted only when the menu actually changed, not at
  every TOTP rollover (#24).
- The countdown timer runs only while the window is visible and a TOTP code is
  counting down; hidden in the tray the process is idle (#25).
- At rollover the old code stays visible, dimmed, until the new one arrives
  instead of blinking to dots (#26).
- The "service not running" page shows pcscd install commands for the detected
  distro; startup checks GTK ≥ 4.14 and libadwaita ≥ 1.6 and prints one
  readable line instead of a traceback (#27).
- libsecret is optional: the key store is pluggable (libsecret, the `keyring`
  package, or none, in which case "Remember on this computer" is hidden) (#30).

### Fixed
- Two config flushes racing could land the older snapshot last.

### Pending (open pull request #47, needs a hardware check)
- Event-driven key detection via `SCardGetStatusChange`; no more per-second
  device-info reads from the key; PC/SC conditions detected by result code
  (#15, #16, #29).

## [0.2.0] – 2026-09-06

Hardening release: no new features, fewer surprises.

### Fixed
- Config file could be corrupted when the backend thread and the UI saved at
  the same time; writes are now locked, use a private temp file, and are
  coalesced (#14).
- "Scan QR code on screen" froze the window while grim and zbarimg ran, up to
  30 s; the scan now runs on a worker thread and reports tool failures
  separately from "no QR code found" (#17).
- An `otpauth://` URI with an unusual period or 7 digits was silently saved
  with different parameters; the form now offers 6/7/8 digits and periods of
  1–3600 s and refuses anything it cannot represent (#18).
- A stale saved password was reported as "Wrong password" (#23).
- Race between the backend thread and dialogs reading device state could raise
  on key removal; the UI now receives immutable snapshots (#22).
- Deprecated positional `maxsplit` in the icon matcher (a `DeprecationWarning`
  on Python 3.13+), deprecated `StyleContext.lookup_color` and
  `Texture.new_for_pixbuf` (#19).
- Issuer icons for e-mail style account names now match every domain label
  (`alice@mail.google.com` finds "google").

### Changed
- Version is declared once, in `pyproject.toml`; the About dialog reads it from
  the installed metadata or the checkout (#21).

### Added
- Test suite (pytest) for the pure-Python modules and a GitHub Actions
  workflow running ruff, the tests, `pip-audit` and CodeQL on every push and
  pull request; Dependabot for pip and Actions (#20).
- This changelog, tags and GitHub releases per milestone (#21).

## [0.1.0] – 2026-09-03

First tagged state.

### Added
- Account list with TOTP countdown ring, click to copy, touch-required and
  HOTP accounts calculate on click.
- Favorites, search (Ctrl+F), rename, delete, add from `otpauth://` URI, QR
  code on screen (grim + zbarimg) or by hand.
- OATH password: unlock, remember in the system keyring, set/change/remove,
  reset the applet.
- Device info page, switch between several connected keys.
- Preferences: theme override, hide codes until clicked, clear clipboard after
  a delay, Aegis-format icon packs.
- Tray icon (StatusNotifierItem + DBusMenu) with a quick-copy menu,
  close-to-tray and start-hidden options.

[Unreleased]: https://github.com/felsenuboot/yubioath-gtk/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/felsenuboot/yubioath-gtk/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/felsenuboot/yubioath-gtk/releases/tag/v0.1.0

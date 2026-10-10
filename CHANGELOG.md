# Changelog

All notable changes to MInstAll.

## [Unreleased]

### Fixed
- Redact URL credentials, query strings and fragments in proxy, download and exception logs.
- Isolate installer downloads with unique files and per-task temporary directories; remove them after installation and reject truncated downloads.
- Parse quoted Windows installer paths and arguments without POSIX backslash stripping.
- Complete GUI self-updates by closing the main window on its event thread, saving state and stopping timers.
- Require a valid, available SHA-256 before downloading or applying an application update.
- Stage Windows updates in a unique directory and use a UTF-8 manifest for the replacement helper, preserving Unicode paths and restart arguments.
- Bundle catalog, translations, profiles and icons in both Windows distributions; preserve editable sidecar overrides.

### Changed
- Build and smoke-test both onefile EXE and portable ZIP from clean Unicode paths on x86/x64, including real Windows updater/GUI lifecycle checks.
- Verify every EXE/ZIP checksum and packaged resource set after artifact downloads.
- Pin GitHub Actions to reviewed commit SHAs; restrict release write permission to its publishing job and retain the 40% coverage gate.

## [2.2.1] — 2026-07-02

### Added
- System tray icon with notifications on install completion
- CLI arguments: `--update`, `--uninstall` for Linux package management
- `--export-profile` to save install set as a profile
- `--check-program-updates` to check catalog programs for newer versions
- Program editor dialog (edit/add programs via GUI)
- Status filter combo box (all/missing/outdated/installed/actions)
- `tray.py` module for system tray support
- `program_editor.py` for GUI program editing
- `core_impl.py` extracted from `core.py`
- `utils.py` with shared utilities
- Tests: `test_cli_args.py`, `test_fixes.py`

### Changed
- Refactored `core.py` into `core.py` (public API) + `core_impl.py` (implementation)
- Improved Linux detection: flatpak, snap, pacman support
- Windows-only components hidden on Linux
- Updated i18n: English, Russian, Chinese translations expanded
- Multilingual README (EN/RU/ZH)

### Fixed
- AppImage filtering on Linux
- Various install and build fixes

## [2.2.0] — 2026-06

### Added
- Cross-platform support: Linux (apt/dpkg, flatpak, snap, .deb, .AppImage, bash)
- CI: tests on Linux + Windows, ruff gate, coverage report
- Unified version source in `config.py`
- Dependency cleanup

### Changed
- Refactored platform-specific code for cross-platform compatibility
- Improved installer detection on Linux via `dpkg-query`

## [2.1.0] — 2026-05

### Added
- Parallel installation with topological dependency levels
- Watchdog for hung installer detection
- Retry with exponential backoff
- Rollback on install failure
- URL download with SHA-256 verification
- Auto-update via GitHub Releases
- Profiles system (`--install-profile`)
- CLI mode (`--install`, `--list`, `--list-installed`)
- i18n system with Russian and English
- Window state persistence

## [2.0.0] — 2026-04

### Added
- Initial wxPython GUI
- Silent batch installation for Windows
- Registry-based installed program detection
- System components: .NET Framework, DirectX, VC++ Redistributables
- software/ folder auto-scanning
- Context menu actions
- Log panel

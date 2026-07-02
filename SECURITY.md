# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 2.2.x   | :white_check_mark: |
| 2.1.x   | :white_check_mark: |
| < 2.1   | :x:                |

## Reporting a Vulnerability

If you discover a security vulnerability in MInstAll, please report it
responsibly.

**Do not open a public issue.** Instead, report via:

- GitHub Security Advisories: https://github.com/assassins377/Python_Install/security/advisories/new
- Or open a private issue and mark it as sensitive

### What to include

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Possible impact
- Suggested fix (if any)

### Response timeline

- Acknowledgment: within 48 hours
- Assessment: within 5 business days
- Fix release: depends on severity (critical: ASAP, low: next release)

## Security Design

MInstAll takes the following security measures:

- **Command validation**: all install commands are checked against shell metacharacters (`& | ; $ \` > < ^`) and restricted to allowed extensions
- **SHA-256 verification**: downloaded files are verified against published hashes
- **Redirect control**: HTTP redirects are limited to prevent open redirect attacks
- **No telemetry**: the application does not send any data to external servers except for update checks (GitHub API)
- **Open source**: all code is publicly auditable

## Known Limitations

- The application currently has no code-signing certificate (Windows SmartScreen warning)
- Install commands run with the user's privileges; no sandboxing of child processes
- The `software/` folder is scanned for executables; only place trusted installers there

# Security Policy

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting feature for this repository.
Do not open a public issue containing credentials, account identifiers, portfolio
data, OAuth tokens, exploit details, or other sensitive information.

Include the affected version, a minimal reproduction, expected impact, and any
suggested mitigation. Maintainers will acknowledge a complete report as soon as
practical and coordinate disclosure after a fix is available.

## Secret handling

- Never commit `.env`, broker exports, account databases, logs, or OAuth tokens.
- Static secrets belong only in the gitignored project-root `.env`.
- Provider-managed rotating tokens belong only in `data/secrets/` with owner-only
  permissions.
- Use `.env.example` for key names and safe, non-secret defaults.

If a credential may have been exposed, revoke or rotate it immediately. Removing
it from Git history is not a substitute for rotation.

## Supported versions

Until the first stable release, security fixes are made on the default branch only.

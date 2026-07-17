# Security Policy

Proxion is an end-to-end encrypted messenger; security reports are taken
seriously and handled with priority.

## Reporting a vulnerability

**Please do not open a public issue.** Instead, use GitHub's private
vulnerability reporting: go to the repository's **Security** tab →
**Report a vulnerability**. Reports are acknowledged within a week.

Include what you can: affected component (gateway / web client / desktop
wrapper / federation protocol), reproduction steps, and impact assessment.

## Scope

In scope: anything in this repository — the Python gateway, the web client,
the Tauri wrapper, the federation/relay protocol, and the release pipeline.

Particularly interesting: E2E encryption breaks, cross-gateway authentication
or signature bypasses, capability-certificate forgery, and pod-data exposure.

## Supported versions

Only the latest release receives fixes. The project is pre-1.0; there are no
security backports.

## Design documentation

The threat model and security baseline live in
[`docs/security/`](docs/security/) — start with
`definition_of_secure_enough.md` and `control_baseline_v1.md`.

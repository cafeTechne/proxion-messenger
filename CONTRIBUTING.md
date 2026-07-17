# Contributing to Proxion

Thanks for helping build a messenger that keeps people's data theirs. This
guide covers setup, the test gates every change must pass, and how to send a
good PR.

## Development setup

```bash
git clone https://github.com/cafeTechne/proxion-messenger.git
cd proxion-messenger
pip install -e "./proxion-messenger-core[gateway,cli,test]"
python run_gateway.py            # gateway + web client on http://localhost:8080
cd web && npm install            # frontend test/tooling deps
```

No `.env` is needed for local development — the gateway runs pod-less by
default. See `.env.example` for the knobs.

## Project layout

- `web/` — frontend: vanilla JS ES modules, no framework. `main.js` is the
  composition root; features live in their own modules with a `*.test.js` next
  to each.
- `proxion-messenger-core/` — Python backend library + gateway server.
- `tauri-app/` — Rust/Tauri desktop wrapper (bundles the gateway as a sidecar).
- `landing/` — the GitHub Pages install page.

## Test gates

Backend (from `proxion-messenger-core/`):

```bash
pytest -m "not integration"   # unit + e2e; integration needs a running CSS pod
```

Frontend (from `web/`):

```bash
npm test                      # vitest units
npm run smoke:a11y            # axe-core WCAG 2.2 AA gate
npm run smoke:keyboard        # mouse-free journey
npm run check:i18n            # locale key coverage
```

CI runs the backend suite on Linux/macOS/Windows plus the frontend units — a
PR needs all of it green. If you touch UI strings, follow the i18n workflow in
[`web/locales/README.md`](web/locales/README.md) (no markup in strings; run the
pseudo-locale regeneration).

## Guardrails

A few project decisions that PRs should not relitigate casually (see
[`docs/ROADMAP_2.md`](docs/ROADMAP_2.md) for the reasoning):

- **No frontend framework.** Vanilla JS + modules is deliberate.
- **No central services** — no SFU, no directory server, no telemetry. The
  target deployment is a person's own machine.
- **Accessibility is a gate, not a feature**: changes must keep the a11y and
  keyboard smokes green.

## Sending a PR

- Branch from `main`; keep PRs focused on one change.
- Explain *why* in the description; link the issue if one exists.
- New behavior needs a test that fails without the change.

## Licensing

Proxion is [AGPL-3.0](LICENSE). By contributing you agree your contribution is
licensed under the same terms (inbound = outbound). There is no CLA.

## Security issues

Please **do not** open public issues for vulnerabilities — see
[SECURITY.md](SECURITY.md).

# Proxion Messenger

Personal data sovereignty messenger built on the Solid Protocol. Users own their data in self-hosted Solid pods. Real-time messaging, WebRTC voice, E2E encryption, rooms, DMs, file attachments, reactions, pinned messages, disappearing messages, scheduling.

## Stack

- `web/` — frontend (vanilla JS, no framework). Single-page app served by the gateway.
- `proxion-messenger-core/` — Python backend library + WebSocket/HTTP gateway server
- `run_gateway.py` — process entry point (loads keys, starts ProxionGateway)
- `tauri-app/` — Rust/Tauri desktop app wrapper (bundles gateway as PyInstaller sidecar)
- `build_sidecar.py` — builds native `proxion-gateway[.exe]` for all 6 platform triples

## Running locally

```
pip install -e ./proxion-messenger-core[gateway]
cp .env.example .env   # fill in values
python run_gateway.py
# open http://localhost:8080
```

## Building a native executable

```
pip install pyinstaller
pip install -e ./proxion-messenger-core[gateway]
python build_sidecar.py          # → tauri-app/src-tauri/sidecar/proxion-gateway-<triple>[.exe]
cd tauri-app && cargo tauri build # → native installer
```

## Key architecture

- `proxion-messenger-core/src/proxion_messenger_core/gateway.py` — `ProxionGateway`: all WebSocket routing, HTTP serving, room/DM/voice logic
- `proxion-messenger-core/src/proxion_messenger_core/persist.py` — `AgentState`: Ed25519 identity + X25519 store key management
- `proxion-messenger-core/src/proxion_messenger_core/local_store.py` — SQLite persistence (rooms, messages, relationships, display names)
- `proxion-messenger-core/src/proxion_messenger_core/solid_client.py` — DPoP-authenticated Solid pod I/O
- `web/main.js` — all client-side WebSocket logic, WebRTC, UI state
- `web/style.css` — mobile-first styles; `@media (min-width: 769px)` for desktop enhancements

## Service worker

Cache name is `proxion-shell-v2`. Bump version in `web/sw.js` to force eviction after asset changes.

## Testing

```
cd proxion-messenger-core
pytest                          # unit tests
pytest tests/e2e/               # E2E (real WebSocket connections)
pytest -m "not integration"     # skip tests that need a running CSS pod
```

Web client gates (`cd web`): `npm test` (vitest) plus the smokes —
`smoke:a11y` (axe-core WCAG 2.2 AA), `smoke:keyboard` (mouse-free journey),
`smoke:pseudo` (pseudo-locale + RTL), `check:i18n`, `check:contrast`. See
[`web/locales/README.md`](web/locales/README.md) for the i18n workflow (adding
a locale, the no-markup rule, pseudo-locale regeneration).

## Deployment target

Native executables via PyInstaller + Tauri. No Docker dependency. Supports Windows x64/ARM64, macOS Intel/Apple Silicon, Linux x64/ARM64.

## Solid Pod integration

Gateway connects to a Community Solid Server (CSS) instance. Configure `PROXION_CSS_URL`, `PROXION_CSS_EMAIL`, `PROXION_CSS_PASSWORD` in `.env`. Leave blank to run without pod backing (local rooms only).


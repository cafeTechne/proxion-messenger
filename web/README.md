# Proxion web client

The single-page app for Proxion: vanilla JavaScript ES modules, no framework and
no bundler. The gateway serves this directory as-is and the desktop app bundles
it. `scripts/build-web.mjs` makes the gateway-less "Proxion Web" build published
to GitHub Pages, which talks to Solid pods directly.

## Running it

Start a gateway from the repository root and open the address it prints:

```bash
pip install -e "./proxion-messenger-core[gateway]"
python run_gateway.py            # Web UI : https://127.0.0.1:8080 (self-signed cert)
```

The page reads the gateway's WebSocket URL from the `x-gateway-url` meta tag the
gateway injects, falling back to `ws://127.0.0.1:7474` (the desktop app's
gateway).

## Layout

- `main.js` is the composition root: it owns the WebSocket dispatch and wires
  the feature modules (`connection.js`, `rendering.js`, `view.js`, `voice.js`,
  `pod.js`, and the rest).
- `e2e.js` runs the Double Ratchet for direct messages in the browser;
  `e2e-secondary.js` seals DM edits, reactions and files on the same session.
  The gateway only ever stores and relays ciphertext for a keyed DM peer. The
  scheme is specified in [docs/E2E_ENCRYPTION.md](../docs/E2E_ENCRYPTION.md).
- `callsec.js` signs and checks the DTLS fingerprint of calls
  ([docs/SECURITY-MODEL.md](../docs/SECURITY-MODEL.md#calls)).
- `locales/` holds the UI strings; see [locales/README.md](locales/README.md).
- `sw.js` is the service worker. Bump its cache name after changing shell assets.

## Tests

```bash
npm ci
npm test                 # vitest unit tests
npm run check:i18n       # referenced keys exist, no markup in strings
npm run check:contrast   # WCAG contrast of the palette
npm run check:spacing    # padding, margin and gap on the --space-* scale
npm run check:type       # font sizes, line heights, weights on the type scale
npm run smoke:ci         # browser smokes against real gateways (what CI runs)
```

Each smoke starts its own gateways. `npm run smoke:all` adds the pod and visual
smokes.

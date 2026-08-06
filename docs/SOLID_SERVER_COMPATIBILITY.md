# Solid server compatibility

Proxion's gateway holds your keys and reads/writes your data on a Solid pod. The pod I/O
itself is standard Solid (LDP + Solid-OIDC), so the gateway works with more than one Solid
server. Where servers differ is how a headless client provisions an account and gets a
token; the gateway detects the server and routes to the right path.

Point the gateway at any supported server with the same three settings (`PROXION_CSS_URL`,
`PROXION_CSS_EMAIL`, `PROXION_CSS_PASSWORD`); the URL decides which server is used. The
names keep the `CSS_` prefix for backward compatibility.

## Supported servers

| Server | Detection | Auth to the pod | Account/pod creation |
|--------|-----------|-----------------|----------------------|
| **CSS** (Community Solid Server) | `/.account/` returns JSON with a `controls` object | DPoP client credentials (`css_auth.py`) | `/.account/` cookie API (`css_setup.py`) |
| **JSS** (JavaScript Solid Server) | `/idp/credentials` returns 200 | Bearer access token from `/idp/credentials` (`jss_setup.py`) | `POST /.pods` (created on first connect if missing) |
| **NSS** (Node Solid Server) | neither of the above; standard OIDC | OIDC password grant, bearer token (`nss_setup.py`) | out of band (web UI) |

Detection lives in `nss_setup.detect_server_type()`; the server-agnostic factory
`nss_setup.make_pod_client()` routes to the right adapter, and the gateway's connect path
(`_gateway_pod._connect_css_sync`) uses the same detection. Detection that is inconclusive
(server briefly unreachable) falls back to CSS, the historical default.

## JSS specifics (verified against JSS v0.0.220)

JSS speaks standard Solid-OIDC (OAuth2 client credentials, DPoP, dynamic client
registration at `/idp/reg`), but its simplest headless path, and the one Proxion uses, is
bearer-token, like NSS and unlike CSS:

- **Login:** `POST {base}/idp/credentials` with `{"email","password"}` returns
  `{"access_token": <Solid-OIDC JWT>}`. Proxion caches it and derives the WebID from the
  token's `webid` claim.
- **Pod I/O:** `Authorization: Bearer <access_token>` (no DPoP). Verified: PUT then GET a
  pod resource round-trips.
- **Provisioning:** `POST {base}/.pods` with `{"name","email","password"}` returns 201 with
  `webId` and `podUri`. Proxion creates the pod on first connect if login fails, then logs in.

The adapter (`jss_setup.py`) mirrors the NSS bearer credentials rather than CSS's DPoP
flow. The protocol-level pod I/O (`solid_client.py`, `dpop.py`) is unchanged and shared
across all three servers.

## Running a JSS pod for testing

```
npm install -g javascript-solid-server   # needs Node 22+
jss start --port 4455 --idp
```

Then point the gateway at it:

```
PROXION_CSS_URL=http://127.0.0.1:4455
PROXION_CSS_EMAIL=you@example.org
PROXION_CSS_PASSWORD=your-password
```

The JSS adapter is exercised by `tests/test_jss_setup.py` (unit tests with mocked
endpoints, plus a live-integration test that runs against a reachable JSS via
`PROXION_JSS_URL`).

## Credit

JSS support was requested and analyzed by @bourgeoa. The concrete flow above was confirmed
against a running JSS, which corrected a few details of the original analysis (JSS's
headless auth is bearer via `/idp/credentials`, and pod creation is `POST /.pods`).

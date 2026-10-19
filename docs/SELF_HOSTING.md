# Proxion Self-Hosting Guide

The desktop app runs its own gateway and needs none of this. This guide is for
running an always-on gateway from source on a server or a spare machine.

For reaching your desktop from a phone without a server, the **Connect a phone**
button in the invite dialog opens a Cloudflare tunnel to the local gateway
(it needs `cloudflared`, which release builds can bundle; see
[RELEASE.md](RELEASE.md)).

## Prerequisites

- Python 3.12 or newer
- For a public deployment: a domain name pointing at the server, ports 80 and 443
  open, and a reverse proxy (Caddy or Nginx examples below)

---

## Quick start (one machine)

```bash
pip install -e "./proxion-messenger-core[gateway]"
cp .env.example .env     # optional: pod credentials; leave blank for local-only rooms
python run_gateway.py
```

The gateway prints its address when it is ready:

```
PROXION_GATEWAY_READY
  Web UI  : https://127.0.0.1:8080
```

On first start `run_gateway.py` creates a self-signed certificate in
`<data dir>/tls/` and serves the web UI on `https://127.0.0.1:8080` and the
WebSocket on `wss://127.0.0.1:7474`. Your browser shows a certificate warning
once. Set `PROXION_AUTO_TLS=0` to serve plain `http://` and `ws://` instead
(fine on loopback; browsers only allow camera and microphone on a non-loopback
address over https).

The data dir defaults to `./data` next to `run_gateway.py` (a packaged
executable uses `~/.proxion/data`). Set `PROXION_DATA_DIR` to move it. It holds
the identity and store keys, the SQLite database, logs, the TLS cert and the
invite stash.

The gateway does not start a Solid server. To back it with a pod, set
`PROXION_CSS_URL`, `PROXION_CSS_EMAIL` and `PROXION_CSS_PASSWORD` for an existing
CSS, JSS or NSS account; see
[SOLID_SERVER_COMPATIBILITY.md](SOLID_SERVER_COMPATIBILITY.md).

---

## Public deployment behind a reverse proxy

The proxy terminates TLS, so the gateway runs plain http/ws on loopback.
Put this in `.env` (replace `chat.example.com`):

```
PROXION_AUTO_TLS=0
PROXION_HOST=127.0.0.1
PROXION_PUBLIC_URL=wss://chat.example.com/ws
PROXION_HTTP_PUBLIC_URL=https://chat.example.com
PROXION_ALLOWED_ORIGINS=https://chat.example.com
PROXION_REQUIRE_AUTH=1
PROXION_ADMIN_API_TOKEN=<output of: openssl rand -hex 32>
```

Why each one:

- `PROXION_PUBLIC_URL` is the WebSocket URL the page connects to and that peers
  are told. The gateway accepts WebSocket connections on any path, so `/ws` lets
  one hostname carry both the page and the socket.
- `PROXION_HTTP_PUBLIC_URL` is the base URL peer gateways use for `/relay` and
  the other federation endpoints.
- `PROXION_ALLOWED_ORIGINS` adds your site to the WebSocket Origin allowlist.
  Without it the browser's handshake from `https://chat.example.com` is refused
  (only loopback and the desktop webview are allowed by default).
- `PROXION_REQUIRE_AUTH=1`: authentication is required by default unless the
  gateway is bound to loopback. Behind a proxy it is bound to loopback, so turn
  it on explicitly.
- `PROXION_ADMIN_API_TOKEN`: see [Owner endpoints](#owner-endpoints-behind-a-reverse-proxy).

Setting `PROXION_PUBLIC_URL` also skips the UPnP port-mapping attempt that
`run_gateway.py` otherwise makes on start.

### Option A: Caddy (automatic certificates)

```
chat.example.com {
    @ws path /ws
    reverse_proxy @ws 127.0.0.1:7474

    reverse_proxy 127.0.0.1:8080
}
```

Caddy forwards WebSocket upgrades and adds `X-Forwarded-For` on its own.

### Option B: Nginx + Certbot

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;
    ssl_certificate     /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;

    # WebSocket gateway
    location = /ws {
        proxy_pass http://127.0.0.1:7474;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }

    # Web UI and HTTP endpoints
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}

server {
    listen 80;
    server_name chat.example.com;
    return 301 https://$host$request_uri;
}
```

Obtain the cert with `certbot --nginx -d chat.example.com`.

### Owner endpoints behind a reverse proxy

A reverse proxy connects to the gateway from 127.0.0.1, so the gateway cannot use
the source address to tell the owner from an internet caller. A request that
carries forwarding headers (`X-Forwarded-For`, `X-Real-IP`, `Forwarded`,
`CF-Connecting-IP`) or a non-local `Host` is treated as remote, and once
`PROXION_PUBLIC_URL` or `PROXION_HTTP_PUBLIC_URL` names a public host, the owner
endpoints (`/backup`, `/restore`, `/export`, `/import`, `/contacts`,
`/message-edits`, `/security-snapshot`) refuse every caller without the token.
Set `PROXION_ADMIN_API_TOKEN` to a long random value and send it as
`Authorization: Bearer <token>`, for example:

```bash
curl -H "Authorization: Bearer $PROXION_ADMIN_API_TOKEN" \
     -H "X-Proxion-Passphrase: <backup passphrase>" \
     https://chat.example.com/backup -o proxion-backup.json
```

Without a token these endpoints return 403 on a proxied gateway, including the
in-app backup, restore and import buttons. `PROXION_API_TOKEN` is the older name
for the same token and is used when `PROXION_ADMIN_API_TOKEN` is unset.

---

## Security checklist

- [ ] `PROXION_PUBLIC_URL` uses `wss://` and `PROXION_ALLOWED_ORIGINS` names only your site
- [ ] Ports 7474 and 8080 are reachable only from the proxy (`PROXION_HOST=127.0.0.1`)
- [ ] `PROXION_REQUIRE_AUTH=1`
- [ ] `PROXION_ADMIN_API_TOKEN` is a long random value
- [ ] `PROXION_CSS_PASSWORD`, if used, is a strong random password
- [ ] The TLS certificate renews automatically
- [ ] The data dir is backed up (it holds the identity key)

---

## Calls and TURN

Calls try a direct path first, then STUN. The web client also includes the free
public relay `openrelay.metered.ca` as a last resort for networks with no direct
path (see `DEFAULT_TURN` in `web/connectivity.js`). Media stays end-to-end
encrypted (DTLS-SRTP), so a relay forwards ciphertext only. Users can turn that
default off or enter their own relay in Settings > Calls.

To offer your own coturn to every client of this gateway, set both:

```bash
TURN_URL=turn:relay.example.com:3478
TURN_SECRET=<same shared secret coturn uses (use-auth-secret / static-auth-secret)>
```

The gateway then hands out time-limited TURN credentials over the authenticated
WebSocket. Both must be set for the relay to be offered. More in
[CALLS.md](CALLS.md).

---

## Upgrading

```bash
git pull
pip install -e "./proxion-messenger-core[gateway]"
# restart run_gateway.py (or your service unit)
```

The data dir is untouched by upgrades.

---

## Backup and restore

Stop the gateway and copy the data dir. That is a complete backup of this
gateway: keys, database and settings.

For an encrypted, portable identity backup while the gateway runs, use
Settings > Backup & recovery in the app, or `/backup` with the admin token as shown above.

Messages kept on Solid pods live on those pod servers and are not part of the
gateway backup.

---

## Troubleshooting

**"Can't connect" in the browser:**
- `PROXION_PUBLIC_URL` must be the WebSocket URL the browser reaches (`wss://chat.example.com/ws`), not the internal address
- `PROXION_ALLOWED_ORIGINS` must contain the page's origin exactly (scheme, host, and port if not 443)
- The proxy must forward the WebSocket upgrade headers (`Connection: Upgrade`, `Upgrade: websocket`)
- With auto-TLS on, the upstream speaks https/wss; behind a proxy set `PROXION_AUTO_TLS=0`

**The gateway exits right after start:**
- Another program holds port 8080 or 7474. The gateway prints
  `PROXION_GATEWAY_ERROR port_in_use <port>`; pick other ports with
  `PROXION_HTTP_PORT` and `PROXION_WS_PORT`.

**Calls fail on a restrictive network:**
- Run Settings > Calls > Test call connectivity, then add a TURN relay as above.

All settings are listed in [CONFIGURATION.md](CONFIGURATION.md).

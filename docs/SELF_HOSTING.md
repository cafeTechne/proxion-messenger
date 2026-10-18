# Proxion Self-Hosting Guide

## Prerequisites

- Docker + Docker Compose v2
- A domain name pointing to your server's public IP
- Ports 80 and 443 open in your firewall
- Ports 7474 and 8080 blocked from external access (handled by your reverse proxy)

---

## Quick start (local only, no TLS)

```bash
cp .env.example .env
# Edit .env: set PROXION_CSS_EMAIL and PROXION_CSS_PASSWORD
docker compose up -d
```

Open `http://localhost:8080/`. The CSS pod server starts automatically.

---

## Production setup with TLS

### Option A — Caddy (recommended, auto-cert)

Install Caddy on the host, then create a `Caddyfile`:

```
chat.example.com {
    # HTTP web UI and REST endpoints
    reverse_proxy / localhost:8080
    reverse_proxy /invite localhost:8080
    reverse_proxy /.well-known/* localhost:8080
    reverse_proxy /relay localhost:8080
    reverse_proxy /file/* localhost:8080

    # WebSocket gateway — upgrade headers required
    @ws {
        path /ws
        header Connection *Upgrade*
        header Upgrade websocket
    }
    reverse_proxy @ws localhost:7474

    # Catch-all for WebSocket connections to the gateway port
    @wsroot {
        header Connection *Upgrade*
        header Upgrade websocket
    }
    reverse_proxy @wsroot localhost:7474
}
```

Start Caddy:
```bash
caddy start --config Caddyfile
```

Set in `.env`:
```
PROXION_PUBLIC_URL=wss://chat.example.com
```

### Option B — Nginx + Certbot

```nginx
server {
    listen 443 ssl http2;
    server_name chat.example.com;
    ssl_certificate     /etc/letsencrypt/live/chat.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/chat.example.com/privkey.pem;

    # Web UI
    location / {
        proxy_pass http://localhost:8080;
        proxy_set_header Host $host;
    }

    # WebSocket gateway
    location /ws {
        proxy_pass http://localhost:7474;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}

server {
    listen 80;
    server_name chat.example.com;
    return 301 https://$host$request_uri;
}
```

Obtain the cert:
```bash
certbot --nginx -d chat.example.com
```

---

## Environment variables

Copy `.env.example` to `.env` and fill in values. Required variables:

| Variable | Description |
|---|---|
| `PROXION_CSS_EMAIL` | Email for the gateway's CSS pod account (auto-created on first start) |
| `PROXION_CSS_PASSWORD` | Password for the CSS pod account — use a strong random value |
| `PROXION_PUBLIC_URL` | Publicly reachable WebSocket URL — `wss://` for production |

Optional variables:

| Variable | Default | Description |
|---|---|---|
| `GATEWAY_WS_PORT` | `7474` | Host port for the WebSocket gateway |
| `GATEWAY_HTTP_PORT` | `8080` | Host port for the web UI |
| `PROXION_SSL_CERT` | _(empty)_ | Path to TLS cert for gateway-native TLS (use Nginx/Caddy instead) |
| `PROXION_SSL_KEY` | _(empty)_ | Path to TLS key |
| `PROXION_AUTO_TLS` | `1` | `run_gateway.py` makes a self-signed cert when no cert is set. `0` keeps plain http/ws (the desktop app sets this) |
| `PROXION_ALLOW_PRIVATE_RELAY` | _(empty)_ | Set to `1` for local federation testing only |
| `PROXION_REQUIRE_AUTH` | `0` | Set to `1` to require Ed25519 DID ownership proof on WebSocket connect |
| `PROXION_ADMIN_API_TOKEN` | _(empty)_ | Bearer token for the owner endpoints (`/backup`, `/restore`, `/export`, `/import`, `/contacts`, `/message-edits`, `/security-snapshot`). Required behind a reverse proxy, see below |
| `PROXION_API_TOKEN` | _(empty)_ | Older name for the same token, used when `PROXION_ADMIN_API_TOKEN` is unset |

### Owner endpoints behind a reverse proxy

A reverse proxy connects to the gateway from 127.0.0.1, so the gateway cannot use
the source address to tell the owner from an internet caller. A request that
carries forwarding headers (`X-Forwarded-For`, `X-Real-IP`, `Forwarded`,
`CF-Connecting-IP`) or a non-local `Host` is treated as remote, and once
`PROXION_PUBLIC_URL` or `PROXION_HTTP_PUBLIC_URL` names a public host, the owner
endpoints above refuse every caller without the token. Set
`PROXION_ADMIN_API_TOKEN` to a long random value and send it as
`Authorization: Bearer <token>`, for example:

```bash
curl -H "Authorization: Bearer $PROXION_ADMIN_API_TOKEN" \
     -H "X-Proxion-Passphrase: <backup passphrase>" \
     https://chat.example.com/backup -o proxion-backup.json
```

Without a token these endpoints return 403 on a proxied gateway, including the
in-app backup, restore and import buttons.

---

## Security checklist

- [ ] `PROXION_CSS_PASSWORD` is a strong random password (not the example value)
- [ ] `PROXION_PUBLIC_URL` uses `wss://` (not `ws://`)
- [ ] CSS container has no exposed ports on the host (it's on `proxion-internal` network)
- [ ] Firewall blocks ports 7474 and 8080 from external access — only your reverse proxy reaches them
- [ ] TLS certificate is valid and renews automatically
- [ ] `PROXION_REQUIRE_AUTH=1` in production to prevent DID spoofing
- [ ] `PROXION_ADMIN_API_TOKEN` is set to a long random value (owner endpoints refuse proxied callers without it)

---

## Upgrading

```bash
docker compose pull          # pull latest images
docker compose build         # rebuild gateway image
docker compose up -d         # restart with new images
```

Data (identity keys, SQLite database, pod files) persists in Docker named volumes
(`pod-data`, `gateway-data`) and survives upgrades.

---

## Full stack (with TURN server for WebRTC)

Use `docker-compose.full.yml` which adds a coturn TURN server for reliable voice calls
through NAT. Set `TURN_SECRET` in `.env` to a strong random value:

```bash
TURN_SECRET=$(openssl rand -hex 32)
echo "TURN_SECRET=$TURN_SECRET" >> .env
docker compose -f docker-compose.full.yml up -d
```

The gateway automatically generates time-limited TURN credentials using `TURN_SECRET`
and sends them to clients over the authenticated WebSocket.

If you run the gateway directly (the `pip install` path, not `docker-compose.full.yml`),
point it at your own coturn with both variables:

```bash
TURN_URL=turn:relay.example.com:3478
TURN_SECRET=<same shared secret coturn uses (use-auth-secret / static-auth-secret)>
```

Both must be set for the relay to be offered. With neither, calls use STUN only:
they connect on ordinary home networks but cannot get through symmetric NAT or a
strict firewall, and a call that finds no direct path ends with a message telling
the user a relay is needed rather than failing silently.

---

## Backup and restore

Gateway data lives in the `gateway-data` Docker volume. To back up:

```bash
docker run --rm -v gateway-data:/data -v $(pwd):/backup alpine \
    tar czf /backup/gateway-data-$(date +%Y%m%d).tar.gz /data
```

To restore:
```bash
docker compose down
docker run --rm -v gateway-data:/data -v $(pwd):/backup alpine \
    tar xzf /backup/gateway-data-YYYYMMDD.tar.gz -C /
docker compose up -d
```

Pod data (messages on Solid pods) is stored on users' individual pod servers and
is not part of the gateway backup.

---

## Troubleshooting

**"Can't connect" in browser:**
- Check `PROXION_PUBLIC_URL` — must be the full WebSocket URL clients reach, not the internal Docker URL
- Verify your reverse proxy forwards WebSocket upgrade headers (`Connection: Upgrade`, `Upgrade: websocket`)

**CSS pod fails to start:**
- Check `docker compose logs css` — CSS needs write access to the `pod-data` volume

**Voice calls fail through corporate NAT:**
- Deploy the full stack (`docker-compose.full.yml`) with a TURN server
- STUN-only works in simple NAT scenarios but fails with symmetric NAT

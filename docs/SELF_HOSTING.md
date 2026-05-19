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
| `PROXION_ALLOW_PRIVATE_RELAY` | _(empty)_ | Set to `1` for local federation testing only |
| `PROXION_REQUIRE_AUTH` | `0` | Set to `1` to require Ed25519 DID ownership proof on WebSocket connect |

---

## Security checklist

- [ ] `PROXION_CSS_PASSWORD` is a strong random password (not the example value)
- [ ] `PROXION_PUBLIC_URL` uses `wss://` (not `ws://`)
- [ ] CSS container has no exposed ports on the host (it's on `proxion-internal` network)
- [ ] Firewall blocks ports 7474 and 8080 from external access — only your reverse proxy reaches them
- [ ] TLS certificate is valid and renews automatically
- [ ] `PROXION_REQUIRE_AUTH=1` in production to prevent DID spoofing

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

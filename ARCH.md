# Proxion Reference Architecture

Authoritative "north star" for the system. Use this after any reboot/sleep to restore state.
For hands-on commands see **COMMANDS.md**.

---

## 0. Recovery

After any sleep or reboot, run (from the project root):
```
python recover.py
```
This restarts *arr containers, syncs Prowlarr indexers, clears Helsinki stale state, restarts the ingest daemon, and reports health. See COMMANDS.md for manual steps.

---

## 1. Alpine Bridge VM (Hyper-V)

| Property | Value |
|---|---|
| VM Name | `SovereignBridge-01` |
| OS | Alpine Linux (amnesic — boots from RAM copy of `master.vhdx`) |
| Static IP | `172.16.0.42` |
| Hyper-V Switch | `SovereignSwitch` (Internal, NAT `172.16.0.0/24`, gateway `172.16.0.1`) |
| Golden Image | `infrastructure\stealth-bridge\home\hyper-v\master.vhdx` |

**Important**: The VM is amnesic. All changes must be committed back to `master.vhdx` or they are lost on restart.

Commit procedure:
1. Stop the VM: `Stop-VM SovereignBridge-01 -Force`
2. Copy live disk back to golden image: the `provision_vm.ps1` script manages staging to a RAM-disk (Z:\) — re-run it to re-provision from scratch, or manually copy `master.vhdx` after editing it offline.

---

## 2. SSH Tunnel (`sovereign-bridge-loop.sh`)

The bridge runs a persistent SSH tunnel to the Helsinki VPS providing:

| Tunnel | Local (Alpine) | Remote (Helsinki) | Purpose |
|---|---|---|---|
| `-L` | `0.0.0.0:9091` | `10.100.0.1:9091` | Transmission RPC/Web UI |
| `-L` | `0.0.0.0:9696` | `10.100.0.1:9696` | Transmission alt port |
| `-D` | `0.0.0.0:1080` | (SOCKS5 exit) | Proxy for containers + Windows |

Script location on Alpine: `/usr/local/bin/sovereign-bridge-loop.sh`
Managed by: OpenRC service `sovereign-bridge`

---

## 3. Windows Port Relays (netsh portproxy)

`Start-Proxion.ps1` sets up Windows-native relays so Windows apps reach the Alpine tunnel:

| Windows | → Alpine | Purpose |
|---|---|---|
| `127.0.0.1:9091` | `172.16.0.42:9091` | Transmission Web UI / ingest daemon |
| `127.0.0.1:1080` | `172.16.0.42:1080` | SOCKS5 proxy (exit through Helsinki) |

---

## 4. DNS Stack

```
Windows clients
    └── AdGuard Home (127.0.0.1:53, Docker 172.20.0.25)
            ├── Ad/tracker blocking
            ├── Local rewrites (*.proxion → 192.168.1.101)
            └── Upstream: Unbound (172.20.0.53:53, Docker)
                    └── Recursive resolution → root servers
```

- **No third-party resolver** — Unbound walks the DNS tree itself (DNSSEC + QNAME minimisation)
- **No fallback** — Windows DNS is set to `127.0.0.1` only; DNS fails closed if AdGuard is down
- **AdGuard UI**: http://localhost:3055
- **Config on disk**: `proxion-core/storage/network/adguard/conf/`
- **AdGuard + Unbound are excluded from Watchtower** (never auto-updated — DNS is critical infrastructure)

---

## 5. Helsinki VPS (`$VPS_IP`)

| Service | Port | Purpose |
|---|---|---|
| nginx | 80, 443 | Web UI, Transmission reverse proxy, static file index |
| Caddy | 8443 | NaiveProxy forward proxy (TLS) |
| Transmission | 9091 (native `transmission@$VPS_HOME_USER` systemd service) | Torrent client |
| SSH | 22 | Bridge tunnel endpoint |

DuckDNS hostname: see `DUCKDNS_DOMAIN` in `.env`

**Transmission paths (on Helsinki):**
- Config/state: `/home/$VPS_HOME_USER/.config/transmission-daemon/`
- Torrent files: `/home/$VPS_HOME_USER/.config/transmission-daemon/torrents/`
- Downloads: `/home/$VPS_HOME_USER/transmission/downloads/{radarr,sonarr,lidarr}/`
- Credentials: `TRANSMISSION_USER` / `TRANSMISSION_PASS` in `.env`

**SSH aliases** (`~/.ssh/config`):

| Alias | Connects to | User |
|---|---|---|
| `helsinki` / `helsinki-cmd` | `$VPS_IP` | root |
| `helsinki-domain` | `$DUCKDNS_DOMAIN` | root |
| `alpine` | `$BRIDGE_IP` | root |

All use key: `$SSH_KEY` (see `.env`)

---

## 6. Docker Network (Local)

All containers share `proxion-network` (`172.20.0.0/16`).
Each service has its own compose file under `integrations/<name>-integration/docker-compose.yml`.

### Service Port Map

| Port | Container | Purpose |
|---|---|---|
| 53 | adguardhome | DNS (TCP+UDP) |
| 3055 | adguardhome | Web UI (localhost only) |
| 2283 | immich_server | Photos |
| 4533 | navidrome | Music streaming |
| 5056 | jellyseerr | Media requests |
| 6767 | bazarr | Subtitles |
| 7878 | radarr | Movie management |
| 8083 | searxng-nginx | Search engine |
| 8084 | freshrss | RSS reader |
| 8085 | calibre-nginx | Books |
| 8096 | jellyfin | Media server |
| 8191 | flaresolverr | CF bypass |
| 8265-8266 | tdarr | Transcoding |
| 8337 | beets | Music tagging |
| 8384 | syncthing | File sync (localhost) |
| 8686 | lidarr | Music management |
| 8853/5443 | adguardhome | DNS-over-HTTPS/TLS |
| 8989 | sonarr | TV management |
| 9091 | transmission | Docker seedbox (separate from Helsinki) |
| 9696 | prowlarr | Indexer management |
| 13378 | audiobookshelf | Audiobooks/podcasts |
| 20211 | pialert | Network monitor |
| 21027/22000 | syncthing | Sync protocol |

### Tubifarry (Lidarr Plugin)

Tubifarry is a native Lidarr plugin that adds:
- **Search Sniper** — periodically picks N random missing albums and searches for them (avoids hammering all indexers at once)
- **YouTube download client** — yt-dlp backed, routes through Helsinki via `ALL_PROXY`

Install: Lidarr → System → Plugins → `https://github.com/TypNull/Tubifarry`

Search Sniper settings (Lidarr → Settings → General after plugin install):

| Setting | Value |
|---|---|
| Picks Per Interval | `5` |
| Min Refresh Interval | `30` minutes |
| Pause When Queued | `10` |
| Search for Missing Albums | ✓ |
| Cache Type | Memory |

Lidarr proxy env vars (`integrations/lidarr-integration/docker-compose.yml`):
- `ALL_PROXY=socks5h://172.16.0.42:1080`
- `HTTP_PROXY=socks5h://172.16.0.42:1080`
- `HTTPS_PROXY=socks5h://172.16.0.42:1080`
- `NO_PROXY=localhost,127.0.0.1,::1,172.16.0.42,prowlarr,radarr,sonarr,transmission`
  (LAN/Docker addresses bypass proxy — required because SOCKS5 exit is Helsinki which can't reach 172.16.0.0/24)

yt-dlp YouTube traffic exits through Helsinki SOCKS5 (same as Prowlarr/Watchtower).

---

### API Keys

Stored in `.env` — see `RADARR_API_KEY`, `SONARR_API_KEY`, `LIDARR_API_KEY`, `PROWLARR_API_KEY`.
(Find each key in the app's Settings → General → API Key, or copy from the container's `config.xml`.)

### Fixed IPs

| IP | Container |
|---|---|
| `172.20.0.53` | unbound |
| `172.20.0.25` | adguardhome |

---

## 7. Media Library Paths

Defined in `.env` at the project root:

| Variable | Description | Used by |
|---|---|---|
| `MEDIA_MOVIES` | Host path to movie library | Radarr, Jellyfin, Bazarr, Tdarr |
| `MEDIA_SHOWS` | Host path to TV library | Sonarr, Jellyfin, Bazarr |
| `MEDIA_MUSIC` | Host path to music library | Lidarr, Navidrome, Beets, Tdarr |
| `MEDIA_PHOTOS` | Host path to photos | Immich |
| `STASH_ROOT` | Container config + staging root | All container configs, ingest staging |
| `CORE_STORAGE` | proxion-core storage dir | AdGuard, Vaultwarden, Syncthing |

All paths defined in `.env`. Staging (pre-import, written by ingest daemon):
- Movies: `$LOCAL_DOWNLOADS/radarr/`
- TV: `$LOCAL_DOWNLOADS/sonarr/`
- Music: direct to `$MEDIA_MUSIC/` (Lidarr imports in-place)

---

## 8. Ingest Pipeline

```
Helsinki Transmission (native systemd)
    → ingest_daemon.py polls RPC every 30s via SSH tunnel (127.0.0.1:19091)
    → SCP via ProxyCommand through Alpine ($BRIDGE_IP) to Windows staging:
        radarr → $LOCAL_DOWNLOADS/radarr/
        sonarr → $LOCAL_DOWNLOADS/sonarr/
        lidarr → $MEDIA_MUSIC/  (direct to library)
    → Notifies Radarr/Sonarr/Lidarr to scan & import
    → Removes torrent + data from Helsinki (no seeding)
    → Cleans local staging after 300s (gives *arr time to copy cross-drive)
```

Status files (all in project root):

| File | Purpose |
|---|---|
| `ingest_daemon.log` | Full rolling log |
| `ingest_daemon.pid` | PID of running daemon |
| `ingest_synced_ids.json` | Torrents synced, awaiting Helsinki removal (survives restarts) |
| `ingest_transfer.json` | Active SCP transfer progress (read by Seedbox UI every 3s) |

---

## 9. Dashboard (Electron App)

| Property | Value |
|---|---|
| Location | `proxion-keyring/dashboard/` |
| Stack | Electron 40 + React 19 + Vite 7 |
| Dev mode | `npm run electron:dev` (Vite dev server + Electron) |
| Production build | `npm run build` then `npm run electron` |
| IPC bridge | `electron/preload.cjs` exposes `window.electronAPI` to React |
| Main process | `electron/main.js` — handles IPC, spawns/monitors daemon, reads logs |

---

## 10. Watchtower (Auto-Updates)

- Polls Docker Hub every **3600s** (1 hour)
- Automatically pulls and restarts containers with new images
- Removes old images after update (`WATCHTOWER_CLEANUP=true`)
- Routes pulls through Helsinki SOCKS5 proxy (`socks5h://172.16.0.42:1080`)
- **Excluded** (never auto-updated): `adguardhome`, `unbound` — both labeled `com.centurylinklabs.watchtower.enable=false`

---

## 11. Startup Sequence

Run `Start-Proxion.ps1` as Administrator. It:

1. Starts / verifies `SovereignBridge-01` Alpine VM
2. Provisions `SovereignSwitch` + NAT (`172.16.0.0/24`)
3. Sets up netsh port proxies (9091, 1080)
4. Brings up all Docker containers (`unbound` and `adguardhome` first)
5. Starts `ingest_daemon.py` (background, polls Helsinki Transmission)
6. Launches Proxion dashboard (`npm run electron:dev`)

---

## 12. Troubleshooting Checklist

- **After sleep/reboot — run `recover.py` first.**

- **Transmission unreachable (9091)?**
  - Check Alpine VM: `Get-VM SovereignBridge-01`
  - Check SSH tunnel on Alpine: `ssh alpine "rc-service sovereign-bridge status"`
  - Check netsh relay: `netsh interface portproxy show all`

- **Ingest daemon stuck ("Found 1 completed, 0 new" loop)?**
  - Transmission was restarted; torrent IDs were reused — daemon's in-memory set is stale
  - Fix: `python recover.py` (step 6 kills + restarts daemon)

- **Radarr/Sonarr showing "indexers unavailable 6h"?**
  - Run `recover.py` — triggers Prowlarr sync + RSS sync
  - Some indexers (TPB, BitSearch, etc.) are CF-blocked — permanent external issue

- **Helsinki disk filling up?**
  - Check: `ssh helsinki-cmd "df -h /home/hobo"`
  - Orphaned `.part` files (not in Transmission queue) are safe to delete
  - `recover.py` auto-detects and removes them

- **DNS down?**
  - `docker ps | grep -E "adguard|unbound"`
  - If Docker is down entirely, restart Docker Desktop

- **Proxy / IP leak?**
  - `ssh alpine "curl -s ifconfig.me"` → should return `$VPS_IP`
  - `ssh alpine "rc-service sovereign-bridge status"`

- **Alpine changes lost after reboot?**
  - Changes were not committed to `master.vhdx` — re-apply and commit offline

# PLAN_ROUND_33: Cross-Gateway Voice + Automatic NAT Traversal

The gateway is a backend implementation detail. A user should be able to
download a `.exe`, run it, share their Proxion address, and have everything
work — voice included — without understanding what a gateway is.

Three interconnected problems:
1. Voice channels are gateway-local. Friends on separate gateways can't
   share a voice channel even when their gateways are reachable.
2. Most home users are behind NAT. Their gateway isn't reachable from the
   outside, so federation, voice relay, and push all silently fail.
3. When NAT is detected, the only guidance is "set PROXION_PUBLIC_URL in
   .env" — meaningless to a non-technical user.

---

## T1 — Cross-gateway voice channels

### The problem (exact code)

`_handle_join_voice_channel` (`_gateway_voice.py:375`) stores members as
`{webid: websocket}` — local WebSocket connections only. There is no path
for a user on gateway-B to join a voice channel hosted on gateway-A.

`voice_peer_joined` and `voice_peer_present` include only `channel_id` and
`peer_webid` — no `gateway_url`. `handleVoicePeerJoined` (`main.js:3702`)
calls `initWebRTC(activeView.id, null, true)` with no target, assuming the
peer is always on the same gateway.

### Architecture of the fix

**New member storage:** change `channel["members"]` from
`{webid: websocket}` to `{webid: {"ws": websocket_or_None, "gateway_url": str_or_None}}`.

Local members: `{"ws": websocket, "gateway_url": None}`
Remote members: `{"ws": None, "gateway_url": "https://bob.example.com"}`

**New relay content type `voice_channel_join`:** Bob on gateway-B sends
`join_voice_channel` to his own gateway. Gateway-B looks up the room's host
gateway (from `room_federated_members` or `_peer_gateway_urls`) and relays:

```json
{
  "content_type": "voice_channel_join",
  "channel_id": "room-123",
  "from_webid": "did:key:zBob",
  "origin_gateway_url": "https://bob.example.com"
}
```

Gateway-A's `_handle_relay_post` dispatches to `_handle_voice_channel_join_relay`.

**Relay leave:** same pattern with `content_type: "voice_channel_leave"`.

### Backend changes

**`_gateway_voice.py` — `_handle_join_voice_channel`:**

```python
async def _handle_join_voice_channel(self, websocket, data: dict) -> None:
    channel_id = data.get("channel_id", "")
    joiner_webid = self._client_webids.get(websocket, "")
    if not channel_id or not joiner_webid:
        return

    channel = self._voice_channels.setdefault(channel_id, {"members": {}})
    existing = dict(channel["members"])

    if len(existing) >= 6:
        await websocket.send(json.dumps({...}))  # existing crowded warning

    # Store with gateway_url=None (local member)
    channel["members"][joiner_webid] = {"ws": websocket, "gateway_url": None}

    own_gw = self._gateway_http_url()

    for member_webid, member_info in existing.items():
        member_gateway = member_info.get("gateway_url")
        if member_info["ws"]:
            # Local member — notify directly
            try:
                await member_info["ws"].send(json.dumps({
                    "type": "voice_peer_joined",
                    "channel_id": channel_id,
                    "peer_webid": joiner_webid,
                    "gateway_url": "",  # local peer, no relay needed
                }))
            except Exception:
                pass
        elif member_gateway:
            # Remote member — relay the notification
            asyncio.create_task(self._relay_ephemeral(member_gateway, {
                "content_type": "voice_channel_peer_joined",
                "channel_id": channel_id,
                "peer_webid": joiner_webid,
                "peer_gateway_url": own_gw,
            }))

        # Tell joiner about this existing member, including their gateway
        try:
            await websocket.send(json.dumps({
                "type": "voice_peer_present",
                "channel_id": channel_id,
                "peer_webid": member_webid,
                "gateway_url": member_gateway or "",
            }))
        except Exception:
            pass
```

**New `_handle_voice_channel_join_relay(data)` in `_gateway_voice.py`:**

```python
async def _handle_voice_channel_join_relay(self, data: dict) -> tuple[str, str]:
    channel_id = data.get("channel_id", "")
    from_webid = data.get("from_webid", "")
    origin_gw  = data.get("origin_gateway_url", "")
    if not channel_id or not from_webid or not origin_gw:
        return "400 Bad Request", '{"error":"missing_fields"}'

    channel = self._voice_channels.setdefault(channel_id, {"members": {}})
    existing = dict(channel["members"])
    channel["members"][from_webid] = {"ws": None, "gateway_url": origin_gw}

    own_gw = self._gateway_http_url()

    # Notify all local members of the remote joiner
    for member_webid, member_info in existing.items():
        if member_info["ws"]:
            try:
                await member_info["ws"].send(json.dumps({
                    "type": "voice_peer_joined",
                    "channel_id": channel_id,
                    "peer_webid": from_webid,
                    "gateway_url": origin_gw,
                }))
            except Exception:
                pass

    # Tell the remote joiner who is already in the channel
    for member_webid, member_info in existing.items():
        peer_gw = member_info.get("gateway_url") or own_gw
        asyncio.create_task(self._relay_ephemeral(origin_gw, {
            "content_type": "voice_channel_peer_present",
            "channel_id": channel_id,
            "peer_webid": member_webid,
            "peer_gateway_url": peer_gw,
        }))

    return "200 OK", '{"status":"ok"}'
```

**`_handle_leave_voice_channel`:** update to relay leave to remote members
(same pattern — if `member_info["ws"]` is None, use `_relay_ephemeral`).

**`gateway.py` `_handle_relay_post` — add dispatch:**
```python
if data.get("content_type") == "voice_channel_join":
    return await self._handle_voice_channel_join_relay(data)
if data.get("content_type") == "voice_channel_leave":
    return await self._handle_voice_channel_leave_relay(data)
if data.get("content_type") == "voice_channel_peer_joined":
    return await self._handle_voice_channel_peer_joined_relay(data)
if data.get("content_type") == "voice_channel_peer_present":
    return await self._handle_voice_channel_peer_present_relay(data)
```

The two `peer_joined`/`peer_present` relay handlers deliver the event to
the local user's WebSocket (the intended recipient on this gateway).

**`_ALLOWED_RELAY_KEYS`:** add `"channel_id"`, `"peer_gateway_url"`.

**`gateway.py` `process_command` — intercept for federated room:**
When `join_voice_channel` is sent and the channel doesn't match a local room
known to this gateway, look it up in `room_federated_members` and relay
`voice_channel_join` to the host gateway instead.

### Frontend changes

**`handleVoicePeerJoined(event)`** — use `event.gateway_url` to route correctly:

```javascript
function handleVoicePeerJoined(event) {
    showToast(`${event.peer_webid.slice(0, 20)} joined the voice channel`, "info");
    if (event.gateway_url) {
        // Remote peer — send offer through relay
        _initiateRelayedChannelCall(event.peer_webid, event.channel_id);
    } else {
        // Local peer — existing path
        if (activeView) initWebRTC(activeView.id, null, true).catch(console.warn);
    }
}

function _initiateRelayedChannelCall(targetWebid, channelId) {
    // Creates a WebRTC offer addressed to targetWebid, routes through relay
    // by sending a voice_invite via relay (existing _relay_voice_signal path)
    // The session_id encodes the channel so the answer comes back correctly
    socket.send(JSON.stringify({
        cmd: "voice_invite",
        target_webid: targetWebid,
        channel_id: channelId,
    }));
}
```

**`handleVoicePeerPresent(event)`** — mirror the same gateway_url check
(existing members will receive an offer from the new joiner via relay).

**New cases in WebSocket message handler** for
`voice_channel_peer_joined` and `voice_channel_peer_present` (relay
deliveries from own gateway → browser):
```javascript
case "voice_channel_peer_joined": handleVoicePeerJoined(event); break;
case "voice_channel_peer_present": handleVoicePeerPresent(event); break;
```

**`joinVoice(roomId)` — federated join path:**

```javascript
async function joinVoice(roomId) {
    socket.send(JSON.stringify({cmd: "join_voice_channel", room_id: roomId}));
    _inVoiceChannel = roomId;
    document.getElementById("leave-voice-channel-btn")?.style?.setProperty("display", "");
}
```

No frontend change needed here — the backend now handles the relay
transparently when the room is hosted on a different gateway.

**New tests:** `tests/test_voice_channel_relay.py` (5 tests)
- Remote join relays `voice_peer_joined` to local members with `gateway_url`
- Local join notifies remote members via `_relay_ephemeral`
- Leave relays to remote members
- `_handle_voice_channel_join_relay` delivers `voice_peer_present` back to remote joiner
- `voice_channel_join` blocked by unknown keys check passes through

---

## T2 — UPnP automatic port mapping

### The problem

99% of home users are behind NAT. The gateway starts and nothing in the
federation works. The only fix requires editing a `.env` file and knowing
your router's external IP.

### `upnp.py` — new module

```python
"""UPnP port mapping for automatic gateway reachability."""
from __future__ import annotations
from typing import Optional

def try_upnp_map(internal_port: int, external_port: int | None = None,
                 protocol: str = "TCP") -> Optional[str]:
    """
    Attempt to create a UPnP port mapping.
    Returns the external IP:port URL if successful, None if unavailable.
    Silently returns None on any error — UPnP is best-effort.
    """
    try:
        import miniupnpc
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        ndevices = upnp.discover()
        if ndevices == 0:
            return None
        upnp.selectigd()
        ext_port = external_port or internal_port
        # Remove any stale mapping first
        try:
            upnp.deleteportmapping(ext_port, protocol)
        except Exception:
            pass
        upnp.addportmapping(
            ext_port, protocol,
            upnp.lanaddr, internal_port,
            "Proxion Gateway", ""
        )
        external_ip = upnp.externalipaddress()
        if external_ip:
            return f"http://{external_ip}:{ext_port}"
        return None
    except Exception:
        return None

def remove_upnp_map(external_port: int, protocol: str = "TCP") -> None:
    """Best-effort removal of UPnP mapping on shutdown."""
    try:
        import miniupnpc
        upnp = miniupnpc.UPnP()
        upnp.discoverdelay = 200
        if upnp.discover() > 0:
            upnp.selectigd()
            upnp.deleteportmapping(external_port, protocol)
    except Exception:
        pass
```

### `pyproject.toml` — add `miniupnpc` to gateway extra

```toml
gateway = ["websockets>=12.0", "pytest-asyncio", "miniupnpc>=2.2; platform_system!='Linux' or sys_platform!='linux'"]
```

Note: `miniupnpc` has wheels for Windows and macOS; Linux users often have
it system-packaged. Make it optional with a try/except in the module.

### `run_gateway.py` — call UPnP before starting

After the TLS cert generation block (R28), before constructing `GatewayConfig`:

```python
# Attempt UPnP port mapping if no public URL is configured
_upnp_mapped_port: int | None = None
if not os.environ.get("PROXION_PUBLIC_URL"):
    try:
        from proxion_messenger_core.upnp import try_upnp_map
        _http_port = int(os.environ.get("PROXION_HTTP_PORT", "8080"))
        _upnp_result = try_upnp_map(_http_port)
        if _upnp_result:
            os.environ["PROXION_PUBLIC_URL"] = _upnp_result
            os.environ["PROXION_UPNP_MAPPED"] = "1"
            _upnp_mapped_port = _http_port
            print(f"✓ UPnP: gateway is publicly reachable at {_upnp_result}", flush=True)
        else:
            print("⚠ UPnP not available — federation may be limited (see app for setup guide)", flush=True)
    except Exception as _upnp_err:
        pass
```

Store the UPnP result in a gateway config field so the frontend can show it.

**`GatewayConfig`** — add two optional fields:
```python
upnp_mapped:    bool = field(default_factory=lambda: os.environ.get("PROXION_UPNP_MAPPED") == "1")
local_ip:       Optional[str] = None  # populated at runtime
```

**`gateway.py` `__init__`** — detect local IP on startup:
```python
import socket as _socket
try:
    _s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
    _s.connect(("8.8.8.8", 80))
    self._local_ip: str = _s.getsockname()[0]
    _s.close()
except Exception:
    self._local_ip = "127.0.0.1"
```

**`_build_discovery_data()`** — include UPnP and local IP in `.well-known/proxion`:
```python
data["upnp_mapped"] = self.config.upnp_mapped
data["local_ip"] = self._local_ip
data["local_port"] = self.config.http_port or 8080
```

**Shutdown cleanup:** in `run_gateway.py`, register an `atexit` handler:
```python
if _upnp_mapped_port:
    import atexit
    from proxion_messenger_core.upnp import remove_upnp_map
    atexit.register(remove_upnp_map, _upnp_mapped_port)
```

**New tests:** `tests/test_upnp.py` (3 tests)
- `try_upnp_map` returns None gracefully when miniupnpc unavailable
- `try_upnp_map` returns URL string when miniupnpc mock succeeds
- `remove_upnp_map` silently succeeds when mapping doesn't exist

---

## T3 — Smart connectivity guidance

### Replace the NAT warning with an actionable status

**`gateway.py` `GET /connectivity`** — new endpoint (add after `/health`):

```python
if method == "GET" and path == "/connectivity":
    conn_body = json.dumps({
        "public_url_set": bool(self.config.public_url),
        "upnp_mapped":    self.config.upnp_mapped,
        "local_ip":       self._local_ip,
        "local_port":     self.config.http_port or 8080,
        "relay_capable":  bool(self.config.public_url),
    }).encode()
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + _SEC_HDR + _NO_STORE_HDR
        + b"Access-Control-Allow-Origin: *\r\n"
        b"Content-Length: " + str(len(conn_body)).encode() + b"\r\n\r\n" + conn_body
    )
    await writer.drain()
    return
```

### Replace `_showNatWarning` with `_showConnectivityGuide`

**`web/main.js`** — replace the existing `_showNatWarning` function:

```javascript
function _showNatWarning() {
    if (document.getElementById("nat-warning-banner")) return;
    // Fetch connectivity details to give actionable guidance
    fetch('/connectivity').then(r => r.json()).then(c => {
        if (c.public_url_set) return; // already reachable, nothing to do

        const banner = document.createElement("div");
        banner.id = "nat-warning-banner";
        banner.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2000;background:#78350f;color:#fef3c7;padding:10px 16px;font-size:0.85em;";

        const port = c.local_port || 8080;
        const localIp = c.local_ip || "192.168.x.x";

        let guide;
        if (c.upnp_mapped === false) {
            // UPnP was tried and failed — give specific manual options
            guide = `
                <strong>Your gateway isn't reachable from the internet yet.</strong>
                Friends on other gateways won't be able to message or call you until this is fixed.
                <details style="margin-top:6px;">
                  <summary style="cursor:pointer;">Fix this ▾</summary>
                  <div style="margin-top:8px;line-height:1.8;">
                    <strong>Option 1 — Port forward your router (most reliable):</strong><br>
                    In your router's admin page, forward port <code>${port}</code> (TCP) to
                    <code>${localIp}</code>, then set
                    <code>PROXION_PUBLIC_URL=http://YOUR_EXTERNAL_IP:${port}</code> in your
                    <code>.env</code> file. <a href="https://portforward.com" target="_blank" style="color:#fcd34d;">portforward.com</a> has guides for every router.<br><br>
                    <strong>Option 2 — Cloudflare Tunnel (free, no router config needed):</strong><br>
                    <code>cloudflared tunnel --url http://localhost:${port}</code> — copy the
                    <code>https://xxxx.trycloudflare.com</code> URL it gives you and set it as
                    <code>PROXION_PUBLIC_URL</code>.
                  </div>
                </details>`;
        } else {
            // UPnP not attempted or unknown
            guide = `Federation is limited: gateway not publicly reachable. Set <code>PROXION_PUBLIC_URL</code> in <code>.env</code>.`;
        }

        banner.innerHTML = `<div style="display:flex;gap:12px;align-items:flex-start;">
            <span style="flex:1">${guide}</span>
            <button onclick="this.closest('#nat-warning-banner').remove();sessionStorage.setItem('proxion_nat_dismissed','1')"
                    style="background:transparent;border:none;color:#fef3c7;cursor:pointer;font-size:1.1em;flex-shrink:0;">✕</button>
        </div>`;
        document.body.prepend(banner);
    }).catch(() => {});
}
```

**Onboarding step-6 — update the connectivity hint:**

In `web/index.html`, the `ob-my-addr-section` text currently says
"Set `PROXION_PUBLIC_URL` in `.env`". Replace it with:

```html
<p style="margin:6px 0 0;font-size:0.78em;color:#64748b;">
  Share this address with contacts so they can add you.
  For friends on other gateways to reach you,
  <a id="ob-connectivity-link" href="#" style="color:#94a3b8;">
    make sure your gateway is reachable
  </a> — the app will guide you.
</p>
```

Clicking the link opens the connectivity guide (same expand/collapse panel as
the NAT warning banner, but inline in onboarding).

### Settings panel — connectivity status

In `web/main.js`, update the Federation section fetch (already added in R28):

```javascript
fetch('/connectivity').then(r => r.json()).then(c => {
    const el = document.getElementById('settings-federation-status');
    if (!el) return;
    const tick = ok => ok ? '<span style="color:#4ade80">✓</span>'
                          : '<span style="color:#f87171">✗</span>';
    const reachable = c.relay_capable;
    const rows = [
        `${tick(reachable)} Internet reachable: ${reachable
            ? (c.upnp_mapped ? 'via UPnP' : 'manually configured')
            : `<span style="color:#fbbf24">no — <a href="#" id="fix-connectivity-link" style="color:#fbbf24">fix this</a></span>`}`,
        `${tick(c.turn_configured)} TURN server: ${c.turn_configured ? 'configured' : '<span style="color:#fbbf24">not set</span>'}`,
        `${tick(c.pod_available)} Solid Pod: ${c.pod_available ? 'connected' : 'offline'}`,
    ];
    el.innerHTML = rows.join('<br>');
    document.getElementById('fix-connectivity-link')?.addEventListener('click', e => {
        e.preventDefault();
        _showNatWarning();
    });
}).catch(() => {});
```

**New tests:** `tests/test_connectivity_endpoint.py` (2 tests)
- `/connectivity` returns correct fields reflecting config
- `upnp_mapped` field reflects `PROXION_UPNP_MAPPED` env var

---

## T4 — Human-readable startup output

### `run_gateway.py` — replace `PROXION_GATEWAY_READY` with useful output

Currently the only stdout output is `"PROXION_GATEWAY_READY"` (used by
Tauri to detect readiness). Keep that signal but add context:

```python
print("PROXION_GATEWAY_READY", flush=True)
print(f"  Address: {gw._proxion_address()}", flush=True)
print(f"  Web UI:  http://127.0.0.1:{config.http_port or 8080}", flush=True)
if config.public_url:
    if config.upnp_mapped:
        print(f"  Public:  {config.public_url}  (via UPnP)", flush=True)
    else:
        print(f"  Public:  {config.public_url}", flush=True)
else:
    print(f"  Public:  not reachable from internet (run app for setup guide)", flush=True)
```

This means when a user opens the terminal / console window after running the
`.exe`, they immediately see whether their gateway is reachable and their
Proxion address — without needing to understand any configuration.

---

## Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_voice_channel_relay.py` | 5 | T1 |
| `tests/test_upnp.py` | 3 | T2 |
| `tests/test_connectivity_endpoint.py` | 2 | T3 |
| **Total new** | **10** | |

---

## Out of scope for R33

- Cloudflare Tunnel auto-setup (requires OAuth / account creation; can't
  automate without user interaction — the guide is the right approach)
- Full mesh VPN / WireGuard overlay (the existing WireGuard tables are for
  a separate homelab system; don't conflate)
- SFU media server (out of scope for 2-6 player use case)
- Screen share

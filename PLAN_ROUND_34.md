# PLAN_ROUND_34: Group Voice Fix + Channel UI + Room History REST

Three confirmed bugs/gaps from the R33 implementation audit.

---

## Critical bug: `peerConnections` undefined

`leaveVoiceChannel()` (`main.js:2830`) and `handleVoicePeerLeft()` (`main.js:3748`)
both reference `peerConnections` which is **never declared** — only `let pc = null`
(line 237) exists. In group voice, when a peer leaves and code tries
`peerConnections[event.peer_webid].close()`, it throws a `ReferenceError` and
silently breaks the call.

Additionally, `initWebRTC` always writes to the single `pc` variable. For group
calls, Alice calling Bob and then Carol would overwrite `pc` with the Carol
connection — Bob's connection is orphaned with no way to close it cleanly.

---

## T1 — Multi-peer WebRTC: define `peerConnections` + per-peer connection tracking

### Scope

Fix group voice so N peers each have their own `RTCPeerConnection` tracked
in a map, ICE candidates route to the right peer, and audio from each peer
plays independently. The existing 1:1 DM call flow (using `pc`) is unchanged.

### `web/main.js` changes

**Add at line 237 alongside `let pc = null`:**

```javascript
let pc = null;                  // 1:1 DM call connection
const peerConnections = {};     // group voice: {peer_webid: RTCPeerConnection}
const peerAudioElements = {};   // group voice: {peer_webid: HTMLAudioElement}
let _channelSessionIds = {};    // {peer_webid: session_id} for group ICE routing
```

**New function `initWebRTCForPeer(targetWebid, sessionId, isCaller, sdpOffer)`:**

Mirrors `initWebRTC` but targets a specific peer and stores the connection
in `peerConnections[targetWebid]`. Key differences:

```javascript
async function initWebRTCForPeer(targetWebid, sessionId, isCaller = false, sdpOffer = null) {
    // Close any existing connection to this peer
    if (peerConnections[targetWebid]) {
        try { peerConnections[targetWebid].close(); } catch (_) {}
        delete peerConnections[targetWebid];
    }

    const iceServers = await _getIceServers();
    const peerPc = new RTCPeerConnection({ iceServers });
    peerConnections[targetWebid] = peerPc;
    if (sessionId) _channelSessionIds[targetWebid] = sessionId;

    const stream = await getMedia();
    if (stream) stream.getTracks().forEach(t => peerPc.addTrack(t, stream));

    peerPc.ontrack = (event) => {
        let audio = peerAudioElements[targetWebid];
        if (!audio) {
            audio = new Audio();
            audio.autoplay = true;
            peerAudioElements[targetWebid] = audio;
        }
        audio.srcObject = event.streams[0];
        _updateChannelParticipantUI(targetWebid, "connected");
    };

    peerPc.onicecandidate = (e) => {
        if (!e.candidate) return;
        const sid = _channelSessionIds[targetWebid] || sessionId;
        const payload = {
            cmd: "ice_candidate",
            target_webid: targetWebid,
            session_id: sid,
            candidate: e.candidate.candidate,
            sdp_mid: e.candidate.sdpMid,
            sdp_mline_index: e.candidate.sdpMLineIndex,
        };
        socket?.send(JSON.stringify(payload));
    };

    peerPc.oniceconnectionstatechange = () => {
        _updateChannelParticipantUI(targetWebid, peerPc.iceConnectionState);
        if (peerPc.iceConnectionState === "failed") {
            peerPc.restartIce();
        }
    };

    if (isCaller) {
        const offer = await peerPc.createOffer();
        await peerPc.setLocalDescription(offer);
        socket?.send(JSON.stringify({
            cmd: "voice_invite",
            target_webid: targetWebid,
            sdp_offer: offer.sdp,
            channel_id: _inVoiceChannel || "",
        }));
    } else if (sdpOffer) {
        await peerPc.setRemoteDescription({ type: "offer", sdp: sdpOffer });
        const answer = await peerPc.createAnswer();
        await peerPc.setLocalDescription(answer);
        socket?.send(JSON.stringify({
            cmd: "voice_answer",
            target_webid: targetWebid,
            session_id: sessionId,
            sdp_answer: answer.sdp,
        }));
    }
    return peerPc;
}
```

**Extract `_getIceServers()` helper** (split out of `initWebRTC` so both
functions share it):

```javascript
async function _getIceServers() {
    const servers = [{ urls: 'stun:stun.l.google.com:19302' }];
    if (!_turnIceServer) {
        try {
            const tc = await fetch('/turn-credentials').then(r => r.json());
            if (tc?.urls?.length > 0) {
                _turnIceServer = { urls: tc.urls, username: tc.username, credential: tc.credential };
            }
        } catch (_) {}
    }
    if (_turnIceServer) servers.push(_turnIceServer);
    return servers;
}
```

**Update `handleVoicePeerJoined`** to call `initWebRTCForPeer`:

```javascript
function handleVoicePeerJoined(event) {
    showToast(`${event.peer_webid.slice(0, 20)} joined the voice channel`, "info");
    _addChannelParticipant(event.peer_webid);
    if (_inVoiceChannel) {
        // We are the existing member — call the new joiner
        initWebRTCForPeer(event.peer_webid, null, true).catch(console.warn);
    }
}
```

**Update `handleVoicePeerPresent`** — existing member sends us an invite,
we don't call them (they will call us). Just add them to the participant UI:

```javascript
function handleVoicePeerPresent(event) {
    showToast(`${event.peer_webid.slice(0, 20)} is in the voice channel`, "info");
    _addChannelParticipant(event.peer_webid);
}
```

**Update `leaveVoiceChannel`** — now `peerConnections` is defined so this works:

```javascript
function leaveVoiceChannel() {
    if (!_inVoiceChannel) return;
    socket.send(JSON.stringify({cmd: "leave_voice_channel", room_id: _inVoiceChannel}));
    _inVoiceChannel = null;
    document.getElementById("leave-voice-channel-btn")?.style.setProperty("display", "none");
    for (const [webid, peerPc] of Object.entries(peerConnections)) {
        try { peerPc.close(); } catch (_) {}
        delete peerConnections[webid];
    }
    for (const [webid, audio] of Object.entries(peerAudioElements)) {
        audio.srcObject = null;
        delete peerAudioElements[webid];
    }
    _channelSessionIds = {};
    _hideChannelPanel();
    showToast("Left voice channel");
}
```

**Update `handleVoicePeerLeft`:**

```javascript
function handleVoicePeerLeft(event) {
    showToast(`${event.peer_webid.slice(0, 20)} left the voice channel`, "info");
    const peerPc = peerConnections[event.peer_webid];
    if (peerPc) { try { peerPc.close(); } catch (_) {} delete peerConnections[event.peer_webid]; }
    const audio = peerAudioElements[event.peer_webid];
    if (audio) { audio.srcObject = null; delete peerAudioElements[event.peer_webid]; }
    delete _channelSessionIds[event.peer_webid];
    _removeChannelParticipant(event.peer_webid);
}
```

### Backend: route `target_webid` in ICE candidate handler

When `ice_candidate` carries a `target_webid` (group call), the gateway should
route it to that peer's socket rather than using `cert_id` for lookup.

In `_gateway_voice.py` `_handle_ice_candidate`:

```python
target_webid = data.get("target_webid")
if target_webid:
    target_ws = self._any_socket(target_webid)
    if target_ws:
        await target_ws.send(json.dumps({
            "type": "ice_candidate",
            "from_webid": sender_webid,
            "session_id": data.get("session_id", ""),
            "candidate": data.get("candidate"),
            "sdp_mid": data.get("sdp_mid"),
            "sdp_mline_index": data.get("sdp_mline_index"),
        }))
    else:
        # Cross-gateway — relay
        peer_gw = self._resolve_peer_gateway(target_webid)
        if peer_gw:
            asyncio.create_task(self._relay_voice_signal(
                target_webid, "ice_candidate",
                {"session_id": data.get("session_id", ""), ...data...}
            ))
    return
# Fall through to existing cert_id-based routing for 1:1 calls
```

**New tests:** `tests/test_group_voice_multi_peer.py` (4 tests)
- `peerConnections` is not undefined (import-level sanity — verifiable in backend by ensuring no runtime crashes)
- `_handle_ice_candidate` routes to `target_webid` when present
- `_handle_ice_candidate` falls back to `cert_id` when `target_webid` absent
- `_handle_voice_channel_join_relay` followed by `_handle_voice_channel_leave_relay` leaves channel empty

---

## T2 — Voice channel participant panel

A persistent panel showing who's in the active voice channel — replacing the
toast-only feedback with a proper persistent UI.

### `web/index.html` — new panel (beside the chat header)

Add after the `#leave-voice-channel-btn`:

```html
<div id="voice-channel-panel"
     style="display:none;position:fixed;bottom:0;left:0;right:0;
            background:#0f172a;border-top:1px solid #1e293b;
            padding:8px 16px;z-index:800;display:none;align-items:center;gap:12px;">
  <span style="font-size:0.8em;color:#4ade80;font-weight:600;">&#x1F50A; Voice channel</span>
  <div id="voice-channel-participants" style="display:flex;gap:8px;flex:1;flex-wrap:wrap;"></div>
  <button id="voice-channel-mute-btn"
          style="background:#334155;border:none;color:#f1f5f9;padding:4px 10px;
                 border-radius:4px;cursor:pointer;font-size:0.8em;">
    Mute
  </button>
</div>
```

### `web/main.js` — participant management

```javascript
const _channelParticipants = {};  // {webid: {name, state}}

function _addChannelParticipant(webid) {
    _channelParticipants[webid] = { name: webid.slice(-12), state: "connecting" };
    _renderChannelPanel();
}

function _removeChannelParticipant(webid) {
    delete _channelParticipants[webid];
    if (Object.keys(_channelParticipants).length === 0 && !_inVoiceChannel) {
        _hideChannelPanel();
    } else {
        _renderChannelPanel();
    }
}

function _updateChannelParticipantUI(webid, state) {
    if (_channelParticipants[webid]) {
        _channelParticipants[webid].state = state;
        _renderChannelPanel();
    }
}

function _renderChannelPanel() {
    const container = document.getElementById("voice-channel-participants");
    if (!container) return;
    const stateColor = { connected: "#4ade80", connecting: "#fbbf24",
                          disconnected: "#f87171", failed: "#f87171", checking: "#fbbf24" };
    container.innerHTML = Object.entries(_channelParticipants).map(([webid, info]) => {
        const color = stateColor[info.state] || "#94a3b8";
        return `<span style="background:#1e293b;padding:3px 8px;border-radius:12px;
                             font-size:0.78em;color:#f1f5f9;display:flex;align-items:center;gap:4px;">
            <span style="width:6px;height:6px;border-radius:50%;background:${color};display:inline-block;"></span>
            ${escHtml(info.name)}
        </span>`;
    }).join("");
}

function _showChannelPanel() {
    const p = document.getElementById("voice-channel-panel");
    if (p) p.style.display = "flex";
}

function _hideChannelPanel() {
    const p = document.getElementById("voice-channel-panel");
    if (p) p.style.display = "none";
    Object.keys(_channelParticipants).forEach(k => delete _channelParticipants[k]);
}
```

Call `_showChannelPanel()` in `joinVoice()`. Wire the mute button:

```javascript
attachListener('#voice-channel-mute-btn', 'click', () => {
    isMuted = !isMuted;
    // Mute/unmute all peer connections' local tracks
    for (const peerPc of Object.values(peerConnections)) {
        peerPc.getSenders().forEach(s => {
            if (s.track?.kind === "audio") s.track.enabled = !isMuted;
        });
    }
    document.getElementById("voice-channel-mute-btn").textContent = isMuted ? "Unmute" : "Mute";
    document.getElementById("voice-channel-mute-btn").style.background = isMuted ? "#7f1d1d" : "#334155";
});
```

**New tests:** None (frontend-only). Visual behavior verified by existing group voice relay tests.

---

## T3 — REST room history endpoint

### The problem

When a user on gateway-B joins a room on gateway-A via `announce_room_join`,
they receive the last 50 messages as a `room_history` snapshot. But:
- The snapshot is only 50 messages
- There's no way to page back further
- Members that reconnect after a disconnect get no catch-up

A `GET /room-history/{room_id}` endpoint lets any federated gateway fetch
room history on demand without maintaining a permanent WebSocket.

### Backend: `gateway.py` new HTTP endpoint

Add after the `/connectivity` handler:

```python
                # ── GET /room-history/{room_id} — fetch room message history ──
                if method == "GET" and path.startswith("/room-history/"):
                    _rh_room_id = path[len("/room-history/"):]
                    _rh_code = parsed_qs.get("code", [""])[0]
                    _rh_limit = min(int(parsed_qs.get("limit", ["50"])[0]), 200)
                    _rh_before = parsed_qs.get("before", [""])[0] or None

                    # Auth: caller must provide the room code
                    _room = self._local_rooms.get(_rh_room_id)
                    if not _room:
                        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 9\r\n\r\nNot found")
                        await writer.drain()
                        return
                    import hmac as _hm
                    _stored_code = _room.get("code", "")
                    if not (_stored_code and _rh_code and
                            _hm.compare_digest(_rh_code.encode(), _stored_code.encode())):
                        writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 9\r\n\r\nForbidden")
                        await writer.drain()
                        return

                    messages = []
                    if self._store:
                        messages = self._store.get_messages(
                            _rh_room_id, before_timestamp=_rh_before, limit=_rh_limit
                        )
                    rh_body = json.dumps({"room_id": _rh_room_id, "messages": messages}).encode()
                    writer.write(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                        + _SEC_HDR + _NO_STORE_HDR
                        + b"Access-Control-Allow-Origin: *\r\n"
                        b"Content-Length: " + str(len(rh_body)).encode() + b"\r\n\r\n" + rh_body
                    )
                    await writer.drain()
                    return
```

The URL needs `parsed_qs` — check how query string parsing is done in the
existing HTTP handler (search for `parsed_qs` or `query_string` in
`_serve_http`). If not already parsed, add:

```python
from urllib.parse import urlparse as _urlparse, parse_qs as _parse_qs
_parsed_url = _urlparse(path)
path = _parsed_url.path
parsed_qs = _parse_qs(_parsed_url.query)
```

### Frontend: use REST endpoint for channel catch-up

In `web/main.js`, after `_handle_announce_room_join` receives
`federated_room_joined`, fetch additional history pages:

```javascript
case "federated_room_joined": {
    if (!event.same_gateway && event.room_id) {
        showToast("Room joined via federation — syncing history…", "info");
        // Fetch older history via REST (complements the 50-msg WebSocket snapshot)
        const code = _local_rooms[event.room_id]?.code || "";
        if (code) {
            fetch(`/room-history/${encodeURIComponent(event.room_id)}?code=${encodeURIComponent(code)}&limit=100`)
                .then(r => r.ok ? r.json() : null)
                .then(data => {
                    if (!data?.messages?.length) return;
                    const existing = new Set(allMessages.map(m => m.message_id));
                    const newMsgs = data.messages.filter(m => !existing.has(m.message_id));
                    if (!newMsgs.length) return;
                    allMessages = [...newMsgs, ...allMessages];
                    newMsgs.forEach(m => { messageMap[m.message_id] = m; });
                    if (activeView?.id === event.room_id) renderMessages();
                })
                .catch(() => {});
        }
    }
    break;
}
```

**New tests:** `tests/test_room_history_rest.py` (3 tests)
- `/room-history/{id}?code=correct` returns messages
- `/room-history/{id}?code=wrong` returns 403
- `/room-history/{id}?limit=200` is capped at 200 messages

---

## Tests summary

| File | Tests | Coverage |
|------|-------|----------|
| `tests/test_group_voice_multi_peer.py` | 4 | T1 backend |
| `tests/test_room_history_rest.py` | 3 | T3 |
| **Total new** | **7** | |

T2 (participant panel) is frontend-only — no new backend tests.

---

## Out of scope for R34

- Large file chunking (>512 KB) — separate round
- Audio level / speaking indicators (Web Audio API `AnalyserNode`) — separate round
- Screen share — out of scope
- SimulCast / SFU — out of scope for 2-6 player target

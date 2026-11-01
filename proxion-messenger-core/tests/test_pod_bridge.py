"""Pod bridge (_gateway_podbridge): the owner-only, policy-scoped pod fetch the
desktop client uses to speak the browser build's drop-box protocol.

Built in process with a fake pod client (no gateway thread, no network):
foreign requests go through a patched network.async_safe_request."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

WEBID = "https://pod.example/alice/profile/card#me"
ROOT = "https://pod.example/alice/"
PEER_ROOT = "https://other.example/bob/"
PEER = "https://other.example/bob/profile/card#me"


class FakePod:
    """Stands in for the gateway's DPoP/bearer SolidClient."""

    def __init__(self):
        self.calls = []
        self.routes = {}

    def request(self, method, url, body=None, headers=None, max_bytes=0):
        self.calls.append((method, url, body, dict(headers or {})))
        r = self.routes.get((method, url))
        if callable(r):
            return r()
        return r or (404, {}, b"")


@pytest.fixture
def gw(tmp_path, monkeypatch):
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    g = ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                       config=GatewayConfig(db_path=str(tmp_path / "t.db"), host="127.0.0.1"))
    g._pod_webid = WEBID
    g._pod_url = ROOT
    g._pod = FakePod()
    g.own_pod_clients[WEBID] = (None, g._pod)
    return g


class _Ws:
    """A bare socket: no handshake headers, so never looks proxied."""

    def __init__(self, ip):
        self.send = AsyncMock()
        self.remote_address = (ip, 1)


def make_ws(did="did:key:z6MkBrowser", ip="127.0.0.1"):
    return _Ws(ip), did


def register(g, ws, did, ip="127.0.0.1"):
    g._client_webids[ws] = did
    g._session_signing_did[ws] = did
    g._session_meta[ws] = {"ip_addr": ip}
    g._webid_sockets.setdefault(did, set()).add(ws)


def run(coro):
    return asyncio.run(coro)


def sent(ws):
    return [json.loads(c.args[0]) for c in ws.send.call_args_list if c.args]


def fetch(g, ws, method, url, body=None, headers=None, req_id="r1"):
    run(g._handle_pod_fetch(ws, {"cmd": "pod_fetch", "req_id": req_id, "method": method,
                                 "url": url, "body": body, "headers": headers or {}}))
    return [m for m in sent(ws) if m.get("type") == "pod_fetch_result"][-1]


def be_consumer(g, did):
    g._pod.routes[("GET", ROOT + "proxion/identity/signer.json")] = (
        200, {}, json.dumps({"version": 1, "signer": did, "account_did": None}).encode())


# ── owner-only ───────────────────────────────────────────────────────────────

def test_non_owner_gets_no_bridge(gw):
    gw._force_auth = True   # a remote stranger with a self-claimed did
    ws, did = make_ws(ip="203.0.113.9")
    register(gw, ws, did, ip="203.0.113.9")
    run(gw._handle_pod_bridge_info(ws, {}))
    info = sent(ws)[-1]
    assert info["type"] == "pod_bridge" and info["ready"] is False and info["reason"] == "not_owner"
    assert "webid" not in info
    res = fetch(gw, ws, "GET", PEER_ROOT + "proxion/identity/signer.json")
    assert res["status"] == 403 and res["error"] == "not_owner"
    assert gw._pod.calls == []


def test_bridge_commands_are_owner_only_in_policy():
    from proxion_messenger_core.security_policy import _OWNER_ONLY_COMMANDS
    from proxion_messenger_core.command_validation import MUTATING_COMMANDS, READ_ONLY_COMMANDS
    assert {"pod_fetch", "pod_watch", "pod_unwatch", "pod_thread"} <= _OWNER_ONLY_COMMANDS
    for c in ("pod_bridge_info", "pod_fetch", "pod_watch", "pod_unwatch", "pod_thread"):
        assert (c in MUTATING_COMMANDS) != (c in READ_ONLY_COMMANDS)


def test_info_ready_for_the_loopback_owner(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    be_consumer(gw, did)
    with patch.object(type(gw), "_pb_ensure_boxes", AsyncMock()):
        run(gw._handle_pod_bridge_info(ws, {}))
    info = sent(ws)[-1]
    assert info["ready"] is True and info["webid"] == WEBID and info["pod_root"] == ROOT
    assert info["consumer"] is True


def test_info_refuses_split_storage(gw):
    gw._pod_url = "https://storage.example/alice/"
    ws, did = make_ws()
    register(gw, ws, did)
    run(gw._handle_pod_bridge_info(ws, {}))
    info = sent(ws)[-1]
    assert info["ready"] is False and info["reason"] == "root_mismatch"
    assert fetch(gw, ws, "GET", ROOT + "proxion/presence.json")["status"] == 503


# ── SSRF / path policy ───────────────────────────────────────────────────────

def test_foreign_reads_are_unauthenticated_and_drop_client_auth_headers(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    seen = {}

    async def fake(method, url, body=None, headers=None, timeout=0, max_bytes=0):
        seen.update(method=method, url=url, headers=headers, max_bytes=max_bytes)
        return 200, {"content-type": "application/json", "set-cookie": "x=1"}, b'{"version":1,"signer":"did:key:zB"}'
    with patch("proxion_messenger_core.network.async_safe_request", fake):
        res = fetch(gw, ws, "GET", PEER_ROOT + "proxion/identity/signer.json",
                    headers={"Authorization": "DPoP abc", "DPoP": "x", "Cookie": "c=1", "Accept": "application/json"})
    assert res["status"] == 200 and "signer" in res["body"]
    assert "set-cookie" not in res["headers"]
    assert seen["headers"] == {"Accept": "application/json"}
    assert seen["max_bytes"] == 64 * 1024
    assert gw._pod.calls == []   # the pod credentials never left for a foreign host


@pytest.mark.parametrize("method,url", [
    ("PUT", PEER_ROOT + "proxion/identity/signer.json"),
    ("DELETE", PEER_ROOT + "proxion/dm-inbox/x"),
    ("GET", PEER_ROOT + "proxion/dm-inbox/"),
    ("GET", PEER_ROOT + "private/notes.ttl"),
    ("GET", "http://169.254.169.254/latest/meta-data/proxion/presence.json?x=1"),
    ("GET", PEER_ROOT + "proxion/%2e%2e/presence.json"),
    ("GET", PEER_ROOT + "a/../proxion/presence.json"),
    ("GET", "https://user@other.example/bob/proxion/presence.json"),
    ("GET", "file:///etc/passwd"),
    ("PUT", ROOT + "proxion/settings.jsonld"),
    ("DELETE", ROOT + "proxion/identity/signer.json"),
    ("GET", ROOT + "rooms/x/room.json"),
    ("PUT", ROOT + "profile/card"),
])
def test_off_policy_requests_are_refused_before_any_io(gw, method, url):
    ws, did = make_ws()
    register(gw, ws, did)
    called = AsyncMock()
    with patch("proxion_messenger_core.network.async_safe_request", called):
        res = fetch(gw, ws, method, url)
    assert res["status"] == 403
    assert gw._pod.calls == [] and not called.called


def test_request_body_is_capped(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    big = json.dumps({"from_webid": WEBID, "pad": "x" * 70000})
    res = fetch(gw, ws, "POST", PEER_ROOT + "proxion/dm-inbox/", body=big)
    assert res["status"] == 413


def test_foreign_response_cap_and_upstream_errors(gw):
    from proxion_messenger_core.network import NetworkError
    ws, did = make_ws()
    register(gw, ws, did)
    with patch("proxion_messenger_core.network.async_safe_request",
               AsyncMock(side_effect=NetworkError("GET x: response exceeds 64 KB"))):
        assert fetch(gw, ws, "GET", PEER_ROOT + "proxion/presence.json")["error"] == "too_large"
    with patch("proxion_messenger_core.network.async_safe_request",
               AsyncMock(side_effect=NetworkError("blocked or unresolvable URL"))):
        assert fetch(gw, ws, "GET", PEER_ROOT + "proxion/presence.json")["error"] == "upstream"


def test_drop_into_a_peer_box_must_speak_as_our_webid(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    ok = AsyncMock(return_value=(201, {}, b""))
    with patch("proxion_messenger_core.network.async_safe_request", ok):
        assert fetch(gw, ws, "POST", PEER_ROOT + "proxion/dm-inbox/",
                     body=json.dumps({"from_webid": PEER}))["error"] == "bad_sender"
        assert fetch(gw, ws, "POST", PEER_ROOT + "proxion/dm-inbox/",
                     body=json.dumps({"from_webid": WEBID, "e2e": True}),
                     headers={"Content-Type": "application/json"})["status"] == 201
    assert ok.call_count == 1


def test_signer_names_this_device_and_never_an_account(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    url = ROOT + "proxion/identity/signer.json"
    gw._pod.routes[("PUT", url)] = (205, {}, b"")
    gw_did = gw._own_gateway_did()
    assert fetch(gw, ws, "PUT", url, body=json.dumps({"version": 1, "signer": did, "account_did": gw_did}))["error"] == "bad_signer"
    assert fetch(gw, ws, "PUT", url, body=json.dumps({"version": 1, "signer": "did:key:zOther", "account_did": None}))["error"] == "bad_signer"
    assert fetch(gw, ws, "PUT", url, body=json.dumps({"version": 1, "signer": did, "account_did": None}))["status"] == 205
    assert gw._pb()["consumer"] == did
    puts = [c for c in gw._pod.calls if c[0] == "PUT"]
    assert len(puts) == 1 and json.loads(puts[0][2])["account_did"] is None


def test_own_requests_carry_no_client_auth_and_redirects_come_back_unfollowed(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    url = ROOT + "proxion/presence.json"
    gw._pod.routes[("GET", url)] = (302, {"location": "http://169.254.169.254/"}, b"")
    res = fetch(gw, ws, "GET", url, headers={"Authorization": "Bearer stolen", "Cookie": "c"})
    assert res["status"] == 302
    assert len(gw._pod.calls) == 1
    assert "Authorization" not in gw._pod.calls[0][3] and "Cookie" not in gw._pod.calls[0][3]


@pytest.mark.asyncio
async def test_async_safe_request_never_follows_a_redirect(monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from proxion_messenger_core.network import async_safe_request

    class H(BaseHTTPRequestHandler):
        def do_GET(self):   # noqa: N802
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        status, headers, _ = await async_safe_request("GET", f"http://127.0.0.1:{srv.server_address[1]}/proxion/presence.json")
        assert status == 302 and headers["location"].endswith("/secret")
    finally:
        srv.shutdown()
        srv.server_close()
    monkeypatch.delenv("PROXION_ALLOW_PRIVATE_RELAY")
    from proxion_messenger_core.network import NetworkError
    with pytest.raises(NetworkError):
        await async_safe_request("GET", "http://127.0.0.1:9/proxion/presence.json")


# ── one consuming device, block prefilter ───────────────────────────────────

def test_only_the_signer_device_reads_the_drop_boxes(gw):
    ws_a, did_a = make_ws("did:key:z6MkDeviceA")
    ws_b, did_b = make_ws("did:key:z6MkDeviceB")
    register(gw, ws_a, did_a)
    register(gw, ws_b, did_b)
    be_consumer(gw, did_a)
    box = ROOT + "proxion/dm-inbox/"
    gw._pod.routes[("GET", box)] = (200, {}, b"")
    assert fetch(gw, ws_a, "GET", box)["status"] == 200
    assert fetch(gw, ws_b, "GET", box)["error"] == "not_consumer"
    assert fetch(gw, ws_b, "DELETE", box + "abc")["error"] == "not_consumer"
    # Creating the box is open to any owner connection.
    gw._pod.routes[("PUT", box)] = (201, {}, b"")
    assert fetch(gw, ws_b, "PUT", box, headers={"Link": '<http://www.w3.org/ns/ldp#BasicContainer>; rel="type"'})["status"] == 201
    run(gw._handle_pod_watch(ws_b, {"url": box}))
    assert sent(ws_b)[-1] == {"type": "pod_watch_refused", "url": box, "reason": "not_consumer"}


def test_blocked_sender_drop_is_deleted_unread(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    be_consumer(gw, did)
    gw._store.set_block(did, PEER, True)
    drop = ROOT + "proxion/dm-inbox/d1"
    gw._pod.routes[("GET", drop)] = (200, {}, json.dumps({"from_webid": PEER, "content": "ct"}).encode())
    gw._pod.routes[("DELETE", drop)] = (205, {}, b"")
    res = fetch(gw, ws, "GET", drop)
    assert res["status"] == 404 and res["error"] == "blocked" and res["body"] == ""
    assert ("DELETE", drop) in [(c[0], c[1]) for c in gw._pod.calls]


# ── watches and closed-app push ──────────────────────────────────────────────

def test_watch_intervals_and_change_events(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    be_consumer(gw, did)
    box = ROOT + "proxion/dm-inbox/"
    pres = PEER_ROOT + "proxion/presence.json"
    run(gw._handle_pod_watch(ws, {"url": box}))
    run(gw._handle_pod_watch(ws, {"url": pres}))
    run(gw._handle_pod_watch(ws, {"url": PEER_ROOT + "proxion/dm-inbox/"}))
    assert sent(ws)[-1]["type"] == "pod_watch_refused"   # a peer's box is not watchable
    sigs = iter(["a", "a", "b", "b"])
    with patch.object(type(gw), "_pb_signature", AsyncMock(side_effect=lambda *a: next(sigs))):
        run(gw._pb_poll_watches(100.0))   # baseline both
        w = gw._pb()["watches"]
        assert w[box]["next"] == 105.0 and w[pres]["next"] == 130.0
        run(gw._pb_poll_watches(105.0))   # box unchanged
        run(gw._pb_poll_watches(110.0))   # box changed
    changed = [m for m in sent(ws) if m.get("type") == "pod_changed"]
    assert changed == [{"type": "pod_changed", "url": box}]
    run(gw._handle_pod_watch(ws, {"url": box, "fast": True}))
    with patch.object(type(gw), "_pb_signature", AsyncMock(return_value="b")):
        run(gw._pb_poll_watches(200.0))
    assert gw._pb()["watches"][box]["next"] == 201.0
    gw._pb_forget_socket(ws)
    assert gw._pb()["watches"] == {}


def _listing(*names):
    inner = ", ".join(f"<{n}>" for n in names)
    return f"<> <http://www.w3.org/ns/ldp#contains> {inner}.".encode() if names else b"<> a <x>."


def test_closed_app_push_skips_blocked_senders(gw):
    did = "did:key:z6MkConsumer"
    be_consumer(gw, did)
    gw._store.set_block(did, PEER, True)
    box = ROOT + "proxion/dm-inbox/"
    gw._pod.routes[("GET", box)] = (200, {}, _listing())
    gw._pod.routes[("GET", ROOT + "proxion/call-inbox/")] = (200, {}, _listing())
    pushes = []
    with patch.object(type(gw), "_pb_push", lambda self, ident: pushes.append(ident) or True):
        run(gw._pb_closed_app_check())                       # baseline
        gw._pod.routes[("GET", box)] = (200, {}, _listing("d1"))
        gw._pod.routes[("GET", box + "d1")] = (200, {}, json.dumps({"from_webid": PEER}).encode())
        run(gw._pb_closed_app_check())                       # blocked: deleted, no push
        assert pushes == []
        assert ("DELETE", box + "d1") in [(c[0], c[1]) for c in gw._pod.calls]
        gw._pod.routes[("GET", box)] = (200, {}, _listing("d1", "d2"))
        gw._pod.routes[("GET", box + "d2")] = (200, {}, json.dumps({"from_webid": "https://c.example/profile/card#me"}).encode())
        run(gw._pb_closed_app_check())
    assert pushes == [did]


def test_closed_app_push_says_missed_call_for_a_call_invite(gw):
    did = "did:key:z6MkConsumer"
    be_consumer(gw, did)
    dm_box, call_box = ROOT + "proxion/dm-inbox/", ROOT + "proxion/call-inbox/"
    gw._pod.routes[("GET", dm_box)] = (200, {}, _listing())
    gw._pod.routes[("GET", call_box)] = (200, {}, _listing())
    pushes = []
    with patch.object(type(gw), "_pb_push", lambda self, ident, kind="dm": pushes.append(kind) or True):
        run(gw._pb_closed_app_check())                       # baseline
        gw._pod.routes[("GET", call_box)] = (200, {}, _listing("c1"))
        gw._pod.routes[("GET", call_box + "c1")] = (200, {}, json.dumps(
            {"type": "ice_candidate", "from_webid": "https://c.example/profile/card#me"}).encode())
        run(gw._pb_closed_app_check())                       # a stray candidate rings nobody
        assert pushes == []
        gw._pod.routes[("GET", call_box)] = (200, {}, _listing("c1", "c2"))
        gw._pod.routes[("GET", call_box + "c2")] = (200, {}, json.dumps(
            {"type": "voice_invite", "from_webid": "https://c.example/profile/card#me"}).encode())
        run(gw._pb_closed_app_check())
    assert pushes == ["missed_call"]


def test_push_is_content_free_and_rate_limited(gw):
    gw._vapid_private_pem, gw._vapid_subject = "pem", "mailto:x@example.com"
    gw._store.get_push_subscriptions = lambda ident: [{"endpoint": "https://push.example/1", "p256dh_b64": "a", "auth_b64": "b"}]
    payloads = []

    async def fan(subs, payload, vpk, vsub):
        payloads.append(payload)

    async def go():
        with patch.object(type(gw), "_push_offline_fanout", lambda self, *a: fan(*a)):
            assert gw._pb_push("did:key:zC") is True
            assert gw._pb_push("did:key:zC") is False
            await asyncio.sleep(0)
    run(go())
    assert payloads == [{"type": "dm", "thread_id": ""}]
    gw._last_inbox_push.clear()
    payloads.clear()

    async def go_call():
        with patch.object(type(gw), "_push_offline_fanout", lambda self, *a: fan(*a)):
            assert gw._pb_push("did:key:zC", "missed_call") is True
            await asyncio.sleep(0)
    run(go_call())
    assert payloads == [{"type": "missed_call", "thread_id": ""}]


def test_rate_limit_answers_with_the_req_id(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    gw._POD_BRIDGE_RATE_LIMIT = 2
    with patch("proxion_messenger_core.network.async_safe_request", AsyncMock(return_value=(200, {}, b"{}"))):
        for i in range(2):
            fetch(gw, ws, "GET", PEER_ROOT + "proxion/presence.json", req_id=f"r{i}")
        res = fetch(gw, ws, "GET", PEER_ROOT + "proxion/presence.json", req_id="r9")
    assert res == {"type": "pod_fetch_result", "req_id": "r9", "status": 429, "headers": {}, "body": "", "error": "rate_limited"}


def test_pod_thread_lists_the_webid_thread(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    run(gw._handle_pod_thread(ws, {"peer_webid": PEER, "display_name": "Bob"}))
    run(gw._handle_pod_thread(ws, {"peer_webid": "https://x.example/i/token", "display_name": "x"}))
    threads = gw._store.get_dm_threads(owner_webid=did)
    assert [t["thread_id"] for t in threads] == [PEER]


def test_box_setup_writes_a_public_append_acl(gw):
    for box in ("dm", "call", "join"):
        url = f"{ROOT}proxion/{box}-inbox/"
        gw._pod.routes[("PUT", url)] = (201, {}, b"")
        gw._pod.routes[("HEAD", url)] = (200, {"link": f'<{url}.acl>; rel="acl"'}, b"")
        gw._pod.routes[("PUT", url + ".acl")] = (205, {}, b"")
    run(gw._pb_ensure_boxes())
    acls = [c for c in gw._pod.calls if c[0] == "PUT" and c[1].endswith(".acl")]
    assert len(acls) == 3
    body = acls[0][2].decode()
    assert "acl:agentClass foaf:Agent" in body and "acl:mode acl:Append" in body
    assert f"acl:agent <{WEBID}>" in body

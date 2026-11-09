"""Pod bridge, pod rooms: the desktop client joining a room that lives in a
browser-build user's pod, and hosting one browser-build users can join.

The only place the gateway's pod credentials leave the owner's own pod is a
joined room's Long Chat container, registered after an approved join, over
DPoP only, GET/HEAD/PATCH only, SSRF-pinned and without redirects."""
from __future__ import annotations

import base64
import json
from unittest.mock import AsyncMock, patch

import pytest

from test_pod_bridge import (  # noqa: F401  (gw is a fixture)
    FakePod, PEER, PEER_ROOT, ROOT, WEBID, fetch, gw, make_ws, register, run, sent,
)
from proxion_messenger_core import podbridge as pbp

ROOM = "room-1"
CONTAINER = PEER_ROOT + f"proxion/rooms/{ROOM}/"
DAY = CONTAINER + "2026/11/01/chat.ttl"


@pytest.fixture
def dpop(gw):
    from proxion_messenger_core.css_auth import DpopSolidClient
    from proxion_messenger_core.dpop import generate_ec_dpop_key

    class Creds:
        _dpop_ec_key = generate_ec_dpop_key()

        def get_token(self, scope="pod_rw"):
            return "tok-123"

    class FakeDpopPod(FakePod, DpopSolidClient):
        """A FakePod the bridge takes for a DPoP client."""

    pod = FakeDpopPod()
    pod._owns_session, pod._session = False, None
    pod._credentials = Creds()
    gw._pod = pod
    gw.own_pod_clients[WEBID] = (None, pod)
    return gw


def _marker(g):
    url = ROOT + pbp.pending_join_path(ROOM, PEER)
    g._pod.routes[("GET", url)] = (200, {}, json.dumps({"px:roomId": ROOM, "px:ownerWebId": PEER}).encode())


def _register(g, ws, room=ROOM, owner=PEER, safe_ip="203.0.113.5", request_id=None):
    with patch("proxion_messenger_core.network._resolve_safe_ip", lambda url: safe_ip):
        run(g._handle_pod_room_register(ws, {"room_id": room, "owner_webid": owner,
                                           **({"req_id": request_id} if request_id is not None else {})}))
    return [m for m in sent(ws) if m.get("type") == "pod_room_registered"][-1]


def _jwt_payload(token):
    part = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


# ── registration ─────────────────────────────────────────────────────────────

def test_registration_echoes_request_correlation_id(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    reply = _register(dpop, ws, request_id='session-request-1')
    assert reply['ok'] is True
    assert reply['req_id'] == 'session-request-1'


@pytest.mark.parametrize('change', ['account', 'client'])
def test_registration_does_not_finish_after_its_pod_session_changes(dpop, monkeypatch, change):
    import copy
    ws, did = make_ws()
    register(dpop, ws, did)
    old_rooms = dpop._pb_rooms()

    async def record_after_switch(*args):
        if change == 'account':
            dpop._pod_webid = PEER
        else:
            client = copy.copy(dpop._pod)
            dpop._pod = client
            dpop.own_pod_clients[WEBID] = (None, client)
        return True

    monkeypatch.setattr(dpop, '_pb_join_on_record', record_after_switch)
    reply = _register(dpop, ws, request_id='old-request')
    assert reply['ok'] is False
    assert reply['reason'] == 'account_changed'
    assert reply['req_id'] == 'old-request'
    assert CONTAINER not in old_rooms
    assert CONTAINER not in dpop._pb_rooms()

def test_bearer_pods_cannot_register_a_foreign_room(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    _marker(gw)
    res = _register(gw, ws)
    assert res["ok"] is False and res["reason"] == "bearer"
    called = AsyncMock()
    with patch("proxion_messenger_core.network.async_safe_request", called):
        assert fetch(gw, ws, "GET", DAY)["status"] == 403
    assert not called.called


def test_register_needs_a_join_on_record(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    assert _register(dpop, ws)["reason"] == "not_joined"
    called = AsyncMock()
    with patch("proxion_messenger_core.network.async_safe_request", called):
        res = fetch(dpop, ws, "GET", DAY)
    assert res["status"] == 403 and not called.called
    # The joined-room list counts too (a restart, after the marker was cleared).
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.jsonld")] = (200, {}, json.dumps(
        {"px:rooms": [{"roomId": ROOM, "owner": PEER, "container": CONTAINER}]}).encode())
    res = _register(dpop, ws)
    assert res["ok"] is True and res["container"] == CONTAINER


def test_canonical_joined_list_restores_rooms_and_overrides_legacy_memberships(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    old = {"px:rooms": [{"roomId": ROOM, "owner": PEER, "container": CONTAINER}]}
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.jsonld")] = (200, {}, json.dumps(old).encode())
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.json")] = (200, {}, b'{"px:rooms": []}')
    assert _register(dpop, ws)["reason"] == "not_joined"
    assert not any(url.endswith("joined-rooms.jsonld") for _, url, _, _ in dpop._pod.calls)
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.json")] = (200, {}, json.dumps(old).encode())
    assert _register(dpop, ws)["ok"] is True


@pytest.mark.parametrize("status", [403, 503])
def test_unavailable_canonical_joined_list_never_falls_back_to_stale_legacy(dpop, status):
    ws, did = make_ws()
    register(dpop, ws, did)
    old = {"px:rooms": [{"roomId": ROOM, "owner": PEER, "container": CONTAINER}]}
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.jsonld")] = (200, {}, json.dumps(old).encode())
    dpop._pod.routes[("GET", ROOT + "proxion/joined-rooms.json")] = (status, {}, b'')
    assert _register(dpop, ws)["reason"] == "not_joined"
    assert not any(url.endswith("joined-rooms.jsonld") for _, url, _, _ in dpop._pod.calls)


@pytest.mark.parametrize("graph", [False, True])
def test_register_accepts_expanded_legacy_pending_markers(dpop, graph):
    ws, did = make_ws()
    register(dpop, ws, did)
    px = "https://proxion.dev/vocab/v1#"
    nodes = [{px + "roomId": [{"@value": ROOM}], px + "ownerWebId": [{"@value": PEER}]}]
    doc = {"@graph": nodes} if graph else nodes
    dpop._pod.routes[("GET", ROOT + pbp.pending_join_path(ROOM, PEER))] = (200, {}, json.dumps(doc).encode())
    assert _register(dpop, ws)["ok"] is True


def test_register_never_matches_pending_fields_across_different_subjects(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    px = "https://proxion.dev/vocab/v1#"
    doc = [{px + "roomId": [{"@value": ROOM}]}, {px + "ownerWebId": [{"@value": PEER}]}]
    dpop._pod.routes[("GET", ROOT + pbp.pending_join_path(ROOM, PEER))] = (200, {}, json.dumps(doc).encode())
    assert _register(dpop, ws)["reason"] == "not_joined"


def test_register_refuses_non_owner_own_pod_bad_ids_and_private_hosts(dpop):
    _marker(dpop)
    ws, did = make_ws()
    register(dpop, ws, did)
    assert _register(dpop, ws, owner=WEBID)["reason"] == "own_pod"
    assert _register(dpop, ws, room="../x")["reason"] == "bad_room"
    assert _register(dpop, ws, owner="https://x.example/i/tok")["reason"] == "bad_room"
    assert _register(dpop, ws, safe_ip=None)["reason"] == "unsafe_host"
    dpop._force_auth = True
    stranger, sdid = make_ws(did="did:key:zStranger", ip="203.0.113.9")
    register(dpop, stranger, sdid, ip="203.0.113.9")
    assert _register(dpop, stranger)["reason"] == "not_owner"
    assert dpop._pb_rooms() == {}


def test_the_container_comes_from_the_owner_webid_not_the_client():
    assert pbp.joined_room_container(PEER, ROOM) == CONTAINER
    assert pbp.joined_room_container(PEER, "a/b") is None
    assert pbp.joined_room_container("not a webid", ROOM) is None


def test_pending_join_path_matches_the_client():
    # podio.js: sha256Hex(roomId + '\n' + ownerWebId)
    import hashlib
    want = hashlib.sha256(f"{ROOM}\n{PEER}".encode()).hexdigest()
    assert pbp.pending_join_path(ROOM, PEER) == f"proxion/join-requests/{want}.jsonld"


# ── authenticated access to a registered room ────────────────────────────────

def test_registered_room_gets_dpop_on_its_chat_files_only(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    assert _register(dpop, ws)["ok"] is True
    seen = []

    async def fake(method, url, body=None, headers=None, timeout=0, max_bytes=0):
        seen.append((method, url, dict(headers or {})))
        return 200, {"content-type": "text/turtle", "set-cookie": "x"}, b"<> a <x>."
    with patch("proxion_messenger_core.network.async_safe_request", fake):
        res = fetch(dpop, ws, "GET", DAY, headers={"Authorization": "Bearer stolen", "Accept": "application/ld+json"})
        assert res["status"] == 200 and "set-cookie" not in res["headers"]
        res = fetch(dpop, ws, "PATCH", DAY, body="INSERT DATA { <a> <b> <c> . }",
                    headers={"Content-Type": "application/sparql-update"})
        assert res["status"] == 200
    method, url, headers = seen[0]
    assert headers["Authorization"] == "DPoP tok-123"
    proof = _jwt_payload(headers["DPoP"])
    assert proof["htm"] == "GET" and proof["htu"] == DAY
    assert headers["Accept"] == "application/ld+json"
    assert seen[1][2]["Content-Type"] == "application/sparql-update"
    assert _jwt_payload(seen[1][2]["DPoP"])["htm"] == "PATCH"
    assert dpop._pod.calls and all(c[1].startswith(ROOT) for c in dpop._pod.calls)


@pytest.mark.parametrize("method,url", [
    ("PUT", DAY),
    ("DELETE", DAY),
    ("POST", CONTAINER),
    ("GET", CONTAINER),
    ("PUT", CONTAINER + ".acl"),
    ("GET", CONTAINER + ".acl"),
    ("PATCH", CONTAINER + "members.jsonld"),
    ("GET", CONTAINER + "notes/secret.ttl"),
    ("GET", CONTAINER + "2026/11/01/chat.ttl?x=1"),
    ("GET", CONTAINER + "2026/../../other/chat.ttl"),
    ("GET", PEER_ROOT + "rooms/room-1/room.json"),
    ("GET", PEER_ROOT + "proxion/rooms/room-2/2026/11/01/chat.ttl"),
    ("GET", "https://evil.example/bob/proxion/rooms/room-1/2026/11/01/chat.ttl"),
])
def test_everything_else_near_a_registered_room_is_refused(dpop, method, url):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    _register(dpop, ws)
    called = AsyncMock()
    with patch("proxion_messenger_core.network.async_safe_request", called):
        res = fetch(dpop, ws, method, url)
    assert res["status"] == 403
    assert not called.called


def test_redirects_come_back_unfollowed_and_nonce_is_retried_once(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    _register(dpop, ws)
    calls = []

    async def fake(method, url, body=None, headers=None, timeout=0, max_bytes=0):
        calls.append(_jwt_payload(headers["DPoP"]).get("nonce"))
        if len(calls) == 1:
            return 401, {"dpop-nonce": "n-1"}, b""
        return 302, {"location": "http://169.254.169.254/"}, b""
    with patch("proxion_messenger_core.network.async_safe_request", fake):
        res = fetch(dpop, ws, "GET", DAY)
    assert res["status"] == 302 and calls == [None, "n-1"]


def test_unregister_and_pod_switch_withdraw_access(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    _register(dpop, ws)
    run(dpop._handle_pod_room_unregister(ws, {"container": CONTAINER}))
    called = AsyncMock()
    with patch("proxion_messenger_core.network.async_safe_request", called):
        assert fetch(dpop, ws, "GET", DAY)["status"] == 403
    _register(dpop, ws)
    dpop._pod_webid = "https://pod.example/carol/profile/card#me"
    assert dpop._pb_rooms() == {}


def test_room_day_watch_needs_a_registration(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    run(dpop._handle_pod_watch(ws, {"url": DAY}))
    assert sent(ws)[-1]["type"] == "pod_watch_refused"
    _marker(dpop)
    _register(dpop, ws)
    run(dpop._handle_pod_watch(ws, {"url": DAY}))
    w = dpop._pb()["watches"][DAY]
    assert w["auth"] is True and w["room"] is True
    with patch.object(type(dpop), "_pb_signature", AsyncMock(return_value="a")) as sig:
        run(dpop._pb_poll_watches(10.0))
        assert sig.call_args.args == (DAY, False, True)
        run(dpop._handle_pod_room_unregister(ws, {"container": CONTAINER}))
        run(dpop._pb_poll_watches(100.0))
    assert DAY not in dpop._pb()["watches"]


# ── hosting a room in our own pod ────────────────────────────────────────────

def test_own_room_paths_for_hosting(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    own_day = ROOT + "proxion/rooms/r9/2026/11/01/chat.ttl"
    desc = ROOT + "rooms/r9/room.json"
    for k in [("PATCH", own_day), ("PUT", desc), ("PUT", ROOT + "proxion/rooms/r9/.acl"),
              ("PUT", ROOT + "proxion/rooms/r9/index.ttl"), ("PUT", ROOT + "proxion/rooms/r9/members.jsonld"),
              ("PATCH", ROOT + "settings/publicTypeIndex.ttl"), ("GET", ROOT + "profile/card")]:
        dpop._pod.routes[k] = (205, {}, b"")
    assert fetch(dpop, ws, "PATCH", own_day, body="INSERT DATA {}",
                 headers={"Content-Type": "text/n3"})["status"] == 205
    good = {"room_id": "r9", "owner": WEBID, "title": "t"}
    assert fetch(dpop, ws, "PUT", desc, body=json.dumps(good))["status"] == 205
    assert fetch(dpop, ws, "PUT", desc, body=json.dumps({**good, "owner": PEER}))["error"] == "bad_body"
    assert fetch(dpop, ws, "PUT", desc, body=json.dumps({**good, "room_id": "r8"}))["error"] == "bad_body"
    assert fetch(dpop, ws, "PUT", ROOT + "proxion/rooms/r9/.acl", body="@prefix acl: <x>.")["status"] == 205
    assert fetch(dpop, ws, "PUT", ROOT + "proxion/rooms/r9/index.ttl", body="<#this> a <x>.")["status"] == 205
    assert fetch(dpop, ws, "PATCH", ROOT + "settings/publicTypeIndex.ttl", body="INSERT DATA {}")["status"] == 205
    assert fetch(dpop, ws, "GET", ROOT + "profile/card")["status"] == 205
    for method, url in [("PUT", ROOT + "profile/card"), ("DELETE", ROOT + "proxion/rooms/r9/index.ttl"),
                        ("GET", ROOT + "rooms/r9/secret.json"), ("PUT", ROOT + "proxion/rooms/r9/notes.ttl"),
                        ("DELETE", own_day), ("GET", ROOT + "proxion/rooms/")]:
        assert fetch(dpop, ws, method, url)["status"] == 403, (method, url)


def test_join_answers_speak_as_owner_webid(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    ok = AsyncMock(return_value=(201, {}, b""))
    box = PEER_ROOT + "proxion/join-inbox/"
    with patch("proxion_messenger_core.network.async_safe_request", ok):
        assert fetch(dpop, ws, "POST", box, body=json.dumps(
            {"kind": "join_approved", "room_id": "r9", "owner_webid": WEBID}))["status"] == 201
        assert fetch(dpop, ws, "POST", box, body=json.dumps(
            {"kind": "join_approved", "room_id": "r9", "owner_webid": PEER}))["error"] == "bad_sender"
        # owner_webid only stands in for a join answer, never a DM.
        assert fetch(dpop, ws, "POST", PEER_ROOT + "proxion/dm-inbox/", body=json.dumps(
            {"kind": "join_approved", "owner_webid": WEBID}))["error"] == "bad_sender"
    assert ok.call_count == 1


def test_joined_pin_index_is_authenticated_read_only(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    _marker(dpop)
    assert _register(dpop, ws)["ok"] is True
    url = CONTAINER + "pins.json"
    seen = []

    async def fake(method, url, body=None, headers=None, timeout=0, max_bytes=0):
        seen.append((method, url, dict(headers or {})))
        return 200, {"content-type": "application/json", "etag": '"pins-1"'}, b'{}'

    with patch("proxion_messenger_core.network.async_safe_request", fake):
        assert fetch(dpop, ws, "GET", url, headers={"Accept": "application/json"})["status"] == 200
        for method in ("PUT", "PATCH", "DELETE", "POST"):
            assert fetch(dpop, ws, method, url, body='{}')["status"] == 403
    assert len(seen) == 1
    assert seen[0][2]["Authorization"] == "DPoP tok-123"
    assert _jwt_payload(seen[0][2]["DPoP"])["htu"] == url
    assert pbp.classify("GET", PEER_ROOT + "proxion/rooms/unregistered/pins.json", ROOT, {}).allow is False


def test_owned_pin_snapshot_body_and_conditional_headers(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    url = ROOT + "proxion/rooms/r9/pins.json"
    dpop._pod.routes[("PUT", url)] = (200, {}, b"")
    doc = {"v": 1, "owner": WEBID, "index": url, "pins": []}
    result = fetch(dpop, ws, "PUT", url, body=json.dumps(doc),
                   headers={"Content-Type": "application/json", "If-Match": '"pins-1"'})
    assert result["status"] == 200
    assert dpop._pod.calls[-1][3]["If-Match"] == '"pins-1"'
    for fields in ({"owner": PEER}, {"index": url + "?other"}, {"v": 2}):
        assert fetch(dpop, ws, "PUT", url, body=json.dumps({**doc, **fields}))["error"] == "bad_body"
    for method in ("PATCH", "DELETE", "POST"):
        assert fetch(dpop, ws, method, url, body='{}')["status"] == 403


def test_pin_index_watch_requires_registration_and_negotiates_json(dpop):
    ws, did = make_ws()
    register(dpop, ws, did)
    url = CONTAINER + "pins.json"
    run(dpop._handle_pod_watch(ws, {"url": url}))
    assert sent(ws)[-1]["type"] == "pod_watch_refused"
    _marker(dpop)
    assert _register(dpop, ws)["ok"] is True
    run(dpop._handle_pod_watch(ws, {"url": url}))
    assert dpop._pb()["watches"][url]["room"] is True
    seen = []

    async def fake(method, url, body=None, headers=None, timeout=0, max_bytes=0):
        seen.append(headers)
        return 200, {"etag": '"pin-snapshot"'}, b'{"pins": []}'

    with patch("proxion_messenger_core.network.async_safe_request", fake):
        assert run(dpop._pb_signature(url, False, True)) is not None
    assert seen[0]["Accept"] == "application/json"
    run(dpop._handle_pod_room_unregister(ws, {"container": CONTAINER}))
    run(dpop._pb_poll_watches(100.0))
    assert url not in dpop._pb()["watches"]


def test_room_attachment_scope_and_immutable_paths():
    own = ROOT + 'proxion/rooms/room-1/'
    for container, registered in [(own, {}), (CONTAINER, {CONTAINER: ROOM})]:
        assert pbp.classify('POST', container, ROOT, registered).allow
        asset = container + 'asset-upload-0.json'
        assert pbp.classify('GET', asset, ROOT, registered).allow
        for method in ['PUT', 'PATCH', 'DELETE']:
            assert not pbp.classify(method, asset, ROOT, registered).allow
        for suffix in ['../asset-upload-0.json', 'asset-upload-0.json/child', 'asset-upload-0.json?x=1']:
            assert not pbp.classify('GET', container + suffix, ROOT, registered).allow
    assert not pbp.classify('POST', CONTAINER, ROOT, {}).allow


def test_room_attachment_post_checks_chunk_and_slug(gw):
    ws, did = make_ws()
    register(gw, ws, did)
    container = ROOT + 'proxion/rooms/room-1/'
    headers = {'Content-Type': 'application/json', 'Slug': 'asset-upload-0.json'}
    chunk = {'v': 1, 'id': 'upload', 'index': 0, 'data': base64.b64encode(b'hello').decode()}
    gw._pod.routes[('POST', container)] = (201, {'location': container + headers['Slug']}, b'')
    assert fetch(gw, ws, 'POST', container, json.dumps(chunk), headers)['status'] == 201
    assert fetch(gw, ws, 'POST', container, json.dumps(chunk), {**headers, 'Slug': '../pins.json'})['error'] == 'bad_slug'
    for invalid in [{**chunk, 'v': True}, {**chunk, 'index': -1}, {**chunk, 'index': 64}, {**chunk, 'index': True},
                    {**chunk, 'data': 'not base64!'}, {**chunk, 'data': base64.b64encode(b'x' * 32769).decode()}]:
        assert fetch(gw, ws, 'POST', container, json.dumps(invalid), headers)['error'] == 'bad_body'

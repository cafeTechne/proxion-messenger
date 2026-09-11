"""F2/F3/F4/F10: gateway HTTP surface hardening.

On an auth-enforced (public/tunnel) gateway an anonymous, non-browser caller (an
untrusted Origin, no authenticated WS actor) must not be able to read TURN
credentials, the /profile social-graph fields, or /metrics; a trusted/loopback
caller and an authenticated WS actor still can. Also: the per-IP rate maps are
pruned/bounded and the sensitive endpoints are rate limited.
"""
from __future__ import annotations
import http.client
import json

import pytest

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.didkey import pub_key_to_did

from gwharness import free_port, start_gateway

_UNTRUSTED_ORIGIN = "http://evil.example"


def _make_gateway(tmp_path, ws_port, http_port, **cfg):
    agent = AgentState.generate()
    config = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"), **cfg,
    )
    return ProxionGateway(agent, {}, {}, config, ReadState())


def _get(http_port, path, origin=None):
    conn = http.client.HTTPConnection("127.0.0.1", http_port, timeout=10)
    headers = {}
    if origin is not None:
        headers["Origin"] = origin
    conn.request("GET", path, headers=headers)
    resp = conn.getresponse()
    body = resp.read()
    conn.close()
    return resp.status, body


class _FakeWS:
    def __init__(self, ip):
        self.remote_address = (ip, 40000)


# ── F2: /turn-credentials ─────────────────────────────────────────────────────

def test_turn_credentials_denied_to_anonymous_when_auth_enforced(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port,
                       turn_url="turn:turn.example.com:3478", turn_secret="s3cr3t")
    gw._force_auth = True
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/turn-credentials", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert "username" not in data and "credential" not in data
    assert data == {"urls": []}


def test_turn_credentials_served_to_authenticated_ws_actor(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port,
                       turn_url="turn:turn.example.com:3478", turn_secret="s3cr3t")
    gw._force_auth = True
    # Simulate an authenticated WS client connected from loopback.
    gw._client_webids[_FakeWS("127.0.0.1")] = "did:key:zActor"
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/turn-credentials", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert "username" in data and "credential" in data


def test_turn_credentials_served_on_loopback_dev(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port,
                       turn_url="turn:turn.example.com:3478", turn_secret="s3cr3t")
    # auth not enforced (loopback host, no force_auth)
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/turn-credentials", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert "username" in data and "credential" in data


# ── F3: /profile/{did} ────────────────────────────────────────────────────────

def _seed_profile(gw, did):
    gw._store.save_display_name(did, "Alice Example")
    gw._store.save_relationship(
        {"certificate_id": "cert-x", "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=did, owner_webid="")


def test_profile_social_fields_hidden_from_anonymous_when_auth_enforced(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    contact = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    _seed_profile(gw, contact)
    gw._force_auth = True
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/profile/" + contact, origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert data == {"did": contact, "status": "offline"}
    assert "display_name" not in data and "fingerprint" not in data


def test_profile_social_fields_served_on_loopback_dev(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    contact = pub_key_to_did(AgentState.generate().identity_pub_bytes)
    _seed_profile(gw, contact)
    # auth not enforced
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/profile/" + contact, origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert data.get("display_name") == "Alice Example"
    assert "gateway_url" in data  # social-graph field only the full path emits


# ── F10: /metrics, /health, /connectivity ─────────────────────────────────────

def test_metrics_denied_to_anonymous_when_auth_enforced(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    gw._force_auth = True
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/metrics", origin=_UNTRUSTED_ORIGIN)
    assert status == 403
    assert b"proxion_" not in body


def test_metrics_served_on_loopback_dev(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/metrics", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    assert b"proxion_ws_connections_current" in body


def test_health_minimal_for_anonymous_full_on_loopback(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    gw._force_auth = True
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/health", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert data == {"status": "ok"}
    assert "connected_clients" not in data
    # Drop the enforced-auth flag to model loopback dev; full detail returns.
    gw._force_auth = False
    status, body = _get(http_port, "/health", origin=_UNTRUSTED_ORIGIN)
    data = json.loads(body)
    assert "connected_clients" in data and "uptime_s" in data


def test_connectivity_minimal_for_anonymous_when_auth_enforced(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    gw._force_auth = True
    start_gateway(gw, ws_port, http_port)
    status, body = _get(http_port, "/connectivity", origin=_UNTRUSTED_ORIGIN)
    assert status == 200
    data = json.loads(body)
    assert "local_ip" not in data and "local_port" not in data
    assert set(data.keys()) <= {"relay_capable"}


# ── F10: rate limiting on /fingerprint, /profile, /webhook ────────────────────

def _post(http_port, path):
    conn = http.client.HTTPConnection("127.0.0.1", http_port, timeout=10)
    conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
    resp = conn.getresponse()
    resp.read()
    conn.close()
    return resp.status


def test_fingerprint_rate_limited(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    start_gateway(gw, ws_port, http_port)
    did = pub_key_to_did(gw.agent.identity_pub_bytes)
    saw_429 = False
    for _ in range(65):
        status, _ = _get(http_port, "/fingerprint/" + did)
        if status == 429:
            saw_429 = True
            break
    assert saw_429


def test_profile_rate_limited(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    start_gateway(gw, ws_port, http_port)
    did = pub_key_to_did(gw.agent.identity_pub_bytes)
    saw_429 = False
    for _ in range(65):
        status, _ = _get(http_port, "/profile/" + did)
        if status == 429:
            saw_429 = True
            break
    assert saw_429


def test_webhook_rate_limited(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    start_gateway(gw, ws_port, http_port)
    saw_429 = False
    for _ in range(65):
        status = _post(http_port, "/webhook/nonexistent-token")
        if status == 429:
            saw_429 = True
            break
    assert saw_429


# ── F4: rate-map pruning ──────────────────────────────────────────────────────

def test_prune_invite_enum_counters_drops_stale_only(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    now = 10_000.0
    # Fill past the prune threshold with stale invite_enum slots.
    for i in range(gw._INVITE_ENUM_PRUNE_AT + 10):
        gw._rate_counters[("invite_enum", f"10.0.0.{i}")] = [1, now - 120.0]
    # A fresh invite_enum slot and an unrelated key must survive.
    gw._rate_counters[("invite_enum", "fresh")] = [3, now - 1.0]
    gw._rate_counters[("someuser", "room-1")] = ["deque-placeholder"]
    gw._prune_invite_enum_counters(now)
    remaining = [k for k in gw._rate_counters
                 if isinstance(k, tuple) and k[0] == "invite_enum"]
    assert ("invite_enum", "fresh") in gw._rate_counters
    assert ("someuser", "room-1") in gw._rate_counters
    assert all(k == ("invite_enum", "fresh") for k in remaining)


def test_prune_invite_enum_counters_noop_below_threshold(tmp_path):
    ws_port, http_port = free_port(), free_port()
    gw = _make_gateway(tmp_path, ws_port, http_port)
    now = 10_000.0
    gw._rate_counters[("invite_enum", "1.2.3.4")] = [1, now - 300.0]
    gw._prune_invite_enum_counters(now)
    # Under the size threshold nothing is pruned (avoids churn on small maps).
    assert ("invite_enum", "1.2.3.4") in gw._rate_counters

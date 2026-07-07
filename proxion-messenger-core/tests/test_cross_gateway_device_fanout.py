"""Cross-gateway multi-device DM fanout (R53).

A peer on another gateway could not learn an account's device roster, so it
single-sent to the primary key and secondary devices never saw incoming DMs.
Covers the four pieces:
  1. POST /devices — signed, relationship-gated device-roster endpoint.
  2. get_peer_device_keys remote fallback (fetches the roster cross-gateway).
  3. send_dm_fanout relays envelopes for non-local targets to the peer gateway.
  4. _handle_dm_fanout_relay verifies + emits dm_fanout to local sockets.
Plus a two-gateway in-process integration of 3→4.
"""
from __future__ import annotations

import json
import base64
import secrets
from datetime import datetime, timezone, timedelta

import pytest
from unittest.mock import AsyncMock
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did


def _did(priv):
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _mock_ws():
    ws = AsyncMock()
    ws.send = AsyncMock(); ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _got(ws, type_):
    for c in ws.send.call_args_list:
        m = json.loads(c[0][0])
        if m.get("type") == type_:
            return m
    return None


def _gw(tmp_path, name):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / f"{name}.db")),
    )


def _sign_devices_request(requester_key, requester_did, target_did, ts=None, nonce=None):
    ts = ts or datetime.now(timezone.utc).isoformat()
    nonce = nonce or secrets.token_hex(8)
    sig = base64.urlsafe_b64encode(
        requester_key.sign(f"{requester_did}|{target_did}|{ts}|{nonce}".encode())
    ).rstrip(b"=").decode()
    return {"requester_did": requester_did, "target_did": target_did,
            "ts": ts, "nonce": nonce, "signature": sig}


def _seed_relationship(gw, peer_did, owner=""):
    gw._store.save_relationship(
        {"certificate_id": f"cert-{peer_did[-6:]}", "subject": "ab" * 32,
         "created_at": 0, "expires_at": 2**31 - 1},
        peer_did=peer_did, owner_webid=owner,
    )


# ── 1. POST /devices ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_devices_endpoint_returns_roster_for_related_requester(tmp_path):
    gw = _gw(tmp_path, "a")
    own_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    req_key = Ed25519PrivateKey.generate()
    req_did = _did(req_key)
    _seed_relationship(gw, req_did, owner="did:key:zLocalUser")
    gw._store.save_device_e2e_key("did:key:zLocalUser", "did:key:zLocalUser", "PRIMARY_PUB")
    gw._store.save_device_e2e_key("did:key:zLocalUser", "did:key:zDev2", "DEV2_PUB")

    body = json.dumps(_sign_devices_request(req_key, req_did, own_did)).encode()
    status, resp = await gw._handle_devices_post(body)
    assert status.startswith("200")
    devices = json.loads(resp)["devices"]
    assert {d["device_id"] for d in devices} == {"did:key:zLocalUser", "did:key:zDev2"}


@pytest.mark.asyncio
async def test_devices_endpoint_owner_fallback_to_union(tmp_path):
    """Relationships saved without an owner (older accept path) still resolve
    via the one-gateway-per-user union."""
    gw = _gw(tmp_path, "a")
    own_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    req_key = Ed25519PrivateKey.generate()
    req_did = _did(req_key)
    _seed_relationship(gw, req_did, owner="")  # no owner recorded
    gw._store.save_device_e2e_key("did:key:zUser", "did:key:zUser", "P1")
    gw._store.save_device_e2e_key("did:key:zUser", "did:key:zDev2", "P2")

    body = json.dumps(_sign_devices_request(req_key, req_did, own_did)).encode()
    status, resp = await gw._handle_devices_post(body)
    assert status.startswith("200")
    assert len(json.loads(resp)["devices"]) == 2


@pytest.mark.asyncio
async def test_devices_endpoint_rejects_stranger_bad_sig_stale_wrong_target(tmp_path):
    gw = _gw(tmp_path, "a")
    own_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    req_key = Ed25519PrivateKey.generate()
    req_did = _did(req_key)

    # No relationship → 403 (even with a valid signature).
    body = json.dumps(_sign_devices_request(req_key, req_did, own_did)).encode()
    status, _ = await gw._handle_devices_post(body)
    assert status.startswith("403")

    _seed_relationship(gw, req_did)

    # Bad signature → 401.
    bad = _sign_devices_request(req_key, req_did, own_did)
    bad["signature"] = base64.urlsafe_b64encode(b"x" * 64).rstrip(b"=").decode()
    status, _ = await gw._handle_devices_post(json.dumps(bad).encode())
    assert status.startswith("401")

    # Stale timestamp → 400.
    old_ts = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    stale = _sign_devices_request(req_key, req_did, own_did, ts=old_ts)
    status, _ = await gw._handle_devices_post(json.dumps(stale).encode())
    assert status.startswith("400")

    # Wrong target (not this gateway's user) → 404, no directory service.
    other = _sign_devices_request(req_key, req_did, "did:key:zSomeoneElse")
    status, _ = await gw._handle_devices_post(json.dumps(other).encode())
    assert status.startswith("404")


# ── 2. get_peer_device_keys remote fallback ──────────────────────────────────

@pytest.mark.asyncio
async def test_get_peer_device_keys_falls_back_to_remote(tmp_path, monkeypatch):
    gw = _gw(tmp_path, "b")
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    ws = _mock_ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _did(Ed25519PrivateKey.generate())})

    remote = [{"device_id": "did:key:zP", "pub_b64u": "PK1"},
              {"device_id": "did:key:zD2", "pub_b64u": "PK2"}]
    fetched = {}
    async def _fake_fetch(peer_did):
        fetched["did"] = peer_did
        return remote
    monkeypatch.setattr(gw, "_fetch_remote_device_keys", _fake_fetch)

    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_peer_device_keys", "peer_webid": "did:key:zRemotePeer"})
    msg = _got(ws, "peer_device_keys")
    assert fetched["did"] == "did:key:zRemotePeer"
    assert msg["devices"] == remote


# ── 3+4. fanout relays cross-gateway and delivers on the far side ────────────

@pytest.mark.asyncio
async def test_fanout_entry_relays_to_remote_and_delivers(tmp_path, monkeypatch):
    """Two in-process gateways: B's send_dm_fanout envelope for a non-local
    device is signed + relayed; A's relay handler verifies it and emits the
    dm_fanout event (with the LOGICAL message id) to A's local sockets."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw_b = _gw(tmp_path, "b")
    gw_a = _gw(tmp_path, "a")
    a_gw_did = pub_key_to_did(gw_a.agent.identity_pub_bytes)

    # B knows A's gateway; route post_relay into gw_a's handler in-process.
    gw_b._peer_gateway_urls[a_gw_did] = "http://peer-a.test"
    async def _fake_post(url, payload):
        status, _ = await gw_a._handle_relay_post(json.dumps(payload).encode())
        return status.startswith("200")
    monkeypatch.setattr("proxion_messenger_core.relay.post_relay", _fake_post)

    # A's two devices are connected on gw_a under the gateway identity fallback.
    ws_dev = _mock_ws()
    gw_a.clients.add(ws_dev)
    gw_a._client_webids[ws_dev] = "did:key:zADevice"

    # B's browser sends a fanout with one envelope addressed to A's device.
    sender = Ed25519PrivateKey.generate()
    ws_b = _mock_ws()
    gw_b.clients.add(ws_b)
    await gw_b.process_command(ws_b, {"cmd": "register", "did": _did(sender)})
    await gw_b.process_command(ws_b, {
        "cmd": "send_dm_fanout", "message_id": "logical-uuid-1",
        "fanout": [{"to_webid": a_gw_did, "to_device_id": "did:key:zADevice",
                    "payload": {"content": "CIPHERTEXT", "e2e": True, "nonce": "n",
                                "from_device_id": _did(sender)}}],
    })

    evt = _got(ws_dev, "dm_fanout")
    assert evt is not None, "A's device must receive the relayed dm_fanout event"
    assert evt["message_id"] == "logical-uuid-1"          # logical id restored
    assert evt["to_device_id"] == "did:key:zADevice"
    assert evt["payload"]["content"] == "CIPHERTEXT"
    assert evt["from_webid"] == pub_key_to_did(gw_b.agent.identity_pub_bytes)


@pytest.mark.asyncio
async def test_dm_fanout_relay_rejects_bad_signature(tmp_path):
    gw = _gw(tmp_path, "a")
    body = json.dumps({
        "content_type": "dm_fanout",
        "from_webid": _did(Ed25519PrivateKey.generate()),
        "to_webid": "did:key:zX", "message_id": "m:1",
        "content": json.dumps({"message_id": "m", "to_device_id": "d", "payload": {}}),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "relay_nonce": secrets.token_hex(8),
        "signature": base64.urlsafe_b64encode(b"y" * 64).rstrip(b"=").decode(),
        "message_scope": "dm-fanout",
    }).encode()
    status, _ = await gw._handle_relay_post(body)
    assert status.startswith("400")

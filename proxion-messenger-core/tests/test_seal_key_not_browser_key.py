"""A peer gateway's seal key must never be handed to a browser as that peer's E2E key.

Gateway seal keys (derived from a gateway's Ed25519 key, used only for sealed
relay transport) and browser X25519 keys (the DM ratchet identity) used to share
the ``x25519_pubs`` table. The relationships list fell back to that table when no
browser key was known, so for a remote peer it could serve the peer gateway's seal
key, which the browser would then cache and encrypt DMs to.
"""
from __future__ import annotations

import json
import sqlite3

import pytest
from unittest.mock import AsyncMock

from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.didkey import pub_key_to_did

SEAL = "U0VBTF9LRVlfT0ZfVEhFX1BFRVJfR0FURVdBWV8zMkI"
BROWSER = "QlJPV1NFUl9FMkVfS0VZX09GX1RIRV9QRUVSXzMyQll"


@pytest.fixture(autouse=True)
def _noauth(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _gw(db):
    return ProxionGateway(AgentState.generate(), {}, {},
                          GatewayConfig(host="127.0.0.1", db_path=str(db)))


def _ws():
    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 1)
    return ws


def _peer_did():
    return pub_key_to_did(AgentState.generate().identity_pub_bytes)


async def _contacts(gw, ws):
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_relationships"})
    for c in ws.send.call_args_list:
        ev = json.loads(c[0][0])
        if ev.get("type") == "relationships":
            return {x["peer_did"]: x for x in ev["contacts"]}
    return {}


async def _discover(gw, monkeypatch, peer_did):
    async def _fake_get(url, **_kw):
        return json.dumps({"did": peer_did, "gateway_http_url": "https://peer.example",
                           "x25519_pub": SEAL}).encode()
    monkeypatch.setattr("proxion_messenger_core.network.async_safe_get", _fake_get)
    assert await gw._discover_peer_gateway(f"{peer_did}@https://peer.example")


@pytest.mark.asyncio
async def test_relationships_never_serve_a_discovered_seal_key(tmp_path, monkeypatch):
    gw = _gw(tmp_path / "a.db")
    peer = _peer_did()
    await _discover(gw, monkeypatch, peer)
    gw._store.save_relationship({"certificate_id": "c1", "subject": "ab" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1}, peer_did=peer)
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _peer_did()})

    contact = (await _contacts(gw, ws))[peer]
    assert contact["x25519_pub"] != SEAL, "peer gateway seal key served as the browser E2E key"
    assert contact["x25519_pub"] is None
    # Sealed relay still finds the seal key.
    assert gw._resolve_peer_x25519_pub(peer) == SEAL

    gw._store.save_e2e_key(peer, BROWSER)
    assert (await _contacts(gw, ws))[peer]["x25519_pub"] == BROWSER
    assert gw._resolve_peer_x25519_pub(peer) == SEAL


@pytest.mark.asyncio
async def test_register_push_never_serves_a_seal_key(tmp_path, monkeypatch):
    gw = _gw(tmp_path / "b.db")
    peer = _peer_did()
    await _discover(gw, monkeypatch, peer)
    gw._store.save_relationship({"certificate_id": "c2", "subject": "ab" * 32,
                                 "created_at": 0, "expires_at": 2**31 - 1}, peer_did=peer)
    ws = _ws()
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": _peer_did()})
    pushed = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    rel = [e for e in pushed if e.get("type") == "relationships"]
    assert rel, pushed
    assert all(c.get("x25519_pub") != SEAL for c in rel[0]["contacts"])


@pytest.mark.asyncio
async def test_well_known_publishes_the_seal_key_not_a_browser_key(tmp_path):
    """A browser registering as the gateway DID must not replace the published seal key."""
    gw = _gw(tmp_path / "c.db")
    seal = gw._own_x25519_pub_b64
    ws = _ws()
    gw.clients.add(ws)
    gw_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    await gw.process_command(ws, {"cmd": "register", "did": gw_did, "x25519_pub": BROWSER})
    assert gw._build_discovery_data().get("x25519_pub") == seal
    # And the gateway's own seal key is not this account's browser key.
    contacts_key = gw._store.get_peer_browser_e2e_key(gw_did)
    assert contacts_key in (None, BROWSER)


def test_legacy_seal_rows_are_moved_out_of_the_browser_key_table(tmp_path):
    """Databases written before the split hold seal keys in x25519_pubs."""
    db = tmp_path / "legacy.db"
    gw = _gw(db)
    peer = _peer_did()
    gw._store.save_peer_gateway(peer, "https://peer.example")
    own_seal = gw._own_x25519_pub_b64
    own_did = pub_key_to_did(gw.agent.identity_pub_bytes)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT OR REPLACE INTO x25519_pubs (did, pub_b64u, updated_at) VALUES (?,?,0)",
                     (peer, SEAL))
        conn.execute("INSERT OR REPLACE INTO x25519_pubs (did, pub_b64u, updated_at) VALUES (?,?,0)",
                     (own_did, own_seal))

    gw2 = ProxionGateway(gw.agent, {}, {}, GatewayConfig(host="127.0.0.1", db_path=str(db)))
    assert gw2._store.get_peer_browser_e2e_key(peer) is None
    assert gw2._store.get_peer_browser_e2e_key(own_did) is None
    assert gw2._resolve_peer_x25519_pub(peer) == SEAL

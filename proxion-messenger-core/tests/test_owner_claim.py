"""Persisted gateway owners and the one-time claim of a public gateway.

A gateway that requires auth by configuration has no loopback owner. Until it
has one it keeps a single-use claim code that only goes to the gateway log; a
session that proved its did presents it once and becomes the owner, which the
store then remembers across restarts.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
import time

import pytest
import websockets
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from gwharness import start_gateway
from proxion_messenger_core.device_cert import issue_device_cert, verify_device_cert
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState


def _did(priv: Ed25519PrivateKey) -> str:
    return pub_key_to_did(priv.public_key().public_bytes_raw())


def _gateway(db_path, agent=None, host="127.0.0.1") -> ProxionGateway:
    return ProxionGateway(
        agent=agent or AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host=host, port=0, http_port=None, db_path=str(db_path)),
    )


def _serve(gw) -> str:
    handle = start_gateway(gw, 0, 0, serve_http=False)
    return f"ws://127.0.0.1:{handle.ws_port}"


class Session:
    """A live socket registered (and challenge-proven when asked) as *key*."""

    def __init__(self, ws, registered: dict):
        self.ws = ws
        self.registered = registered

    async def cmd(self, payload: dict, want: set, timeout: float = 5.0) -> dict:
        await self.ws.send(json.dumps(payload))
        return await self.wait(want, timeout)

    async def wait(self, want: set, timeout: float = 5.0) -> dict:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            ev = json.loads(await asyncio.wait_for(self.ws.recv(), deadline - loop.time()))
            if ev.get("type") in want:
                return ev


async def _connect(url: str, key: Ed25519PrivateKey, cert=None) -> Session:
    ws = await websockets.connect(url)
    payload = {"cmd": "register", "did": _did(key), "display_name": "x"}
    if cert is not None:
        payload["delegation_cert"] = cert
    await ws.send(json.dumps(payload))
    for _ in range(20):
        ev = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        if ev.get("type") == "auth_challenge":
            sig = key.sign(ev["nonce"].encode())
            await ws.send(json.dumps({
                "cmd": "auth_response", "nonce": ev["nonce"],
                "signature": base64.urlsafe_b64encode(sig).rstrip(b"=").decode(),
            }))
        elif ev.get("type") == "registered":
            return Session(ws, ev)
        elif ev.get("type") == "auth_failed":
            raise AssertionError(f"auth failed: {ev}")
    raise AssertionError("no registered event")


_CLAIM_REPLIES = {"gateway_claimed", "claim_failed"}


@pytest.fixture
def public_auth(monkeypatch):
    """A gateway behind a reverse proxy: loopback bind, auth required by config."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    monkeypatch.delenv("PROXION_ADMIN_API_TOKEN", raising=False)


@pytest.mark.asyncio
async def test_unclaimed_gateway_is_claimed_once_by_the_proven_session(tmp_path, public_auth):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    owner_key, other_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    owner = await _connect(url, owner_key)
    try:
        assert owner.registered.get("claimable") is True
        assert owner.registered.get("owner") is False
        code = gw._owner_claim_code()
        assert code and code not in json.dumps(owner.registered)

        denied = await owner.cmd({"cmd": "list_owner_devices"}, {"owner_devices", "error"})
        assert denied.get("message") == "gateway_owner_only"

        bad = await owner.cmd({"cmd": "claim_gateway", "code": "not-the-code"}, _CLAIM_REPLIES)
        assert bad == {"type": "claim_failed", "reason": "invalid_code"}

        ok = await owner.cmd({"cmd": "claim_gateway", "code": code}, _CLAIM_REPLIES)
        assert ok["type"] == "gateway_claimed" and ok["webid"] == _did(owner_key)
        # The claimed owner gets the gateway delegation cert for its signing key.
        own_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        assert verify_device_cert(ok["gateway_delegation_cert"], expected_device_did=_did(owner_key),
                                  expected_account_did=own_did) == own_did
        listed = await owner.cmd({"cmd": "list_owner_devices"}, {"owner_devices"})
        assert [(d["did"], d["via"], d["self"]) for d in listed["devices"]] == [
            (_did(owner_key), "claim", True)]

        # Once claimed: the code is dead, nobody is offered a claim, the same
        # code (replay) and the gateway's old code do not work.
        assert gw._owner_claim is None and gw.owner_claim_url() == ""
        other = await _connect(url, other_key)
        try:
            assert "claimable" not in other.registered and other.registered["owner"] is False
            assert "gateway_delegation_cert" not in other.registered
            replay = await other.cmd({"cmd": "claim_gateway", "code": code}, _CLAIM_REPLIES)
            assert replay == {"type": "claim_failed", "reason": "already_claimed"}
            denied = await other.cmd({"cmd": "tunnel_status"}, {"tunnel_status", "error"})
            assert denied.get("message") == "gateway_owner_only"
        finally:
            await other.ws.close()
        # The owner-only commands that consult the owner check now admit the owner.
        status = await owner.cmd({"cmd": "tunnel_status"}, {"tunnel_status", "error"})
        assert status["type"] == "tunnel_status"
    finally:
        await owner.ws.close()


@pytest.mark.asyncio
async def test_two_claimers_with_the_code_only_one_wins(tmp_path, public_auth):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    a = await _connect(url, Ed25519PrivateKey.generate())
    b = await _connect(url, Ed25519PrivateKey.generate())
    try:
        code = gw._owner_claim_code()
        await asyncio.gather(
            a.ws.send(json.dumps({"cmd": "claim_gateway", "code": code})),
            b.ws.send(json.dumps({"cmd": "claim_gateway", "code": code})),
        )
        ra, rb = await asyncio.gather(a.wait(_CLAIM_REPLIES), b.wait(_CLAIM_REPLIES))
        assert sorted([ra["type"], rb["type"]]) == ["claim_failed", "gateway_claimed"]
        assert len(gw._store.list_owner_devices()) == 1
    finally:
        await a.ws.close()
        await b.ws.close()


@pytest.mark.asyncio
async def test_expired_code_is_refused_and_a_new_one_is_logged(tmp_path, public_auth, caplog):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    s = await _connect(url, Ed25519PrivateKey.generate())
    try:
        old = gw._owner_claim_code()
        gw._owner_claim["expires_at"] = time.time() - 1
        r = await s.cmd({"cmd": "claim_gateway", "code": old}, _CLAIM_REPLIES)
        assert r == {"type": "claim_failed", "reason": "invalid_code"}
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="proxion_messenger_core.gateway"):
            new = gw._owner_claim_code()
        assert new and new != old
        assert f"#claim={new}" in caplog.text and old not in caplog.text
        assert len(new) >= 32
    finally:
        await s.ws.close()


@pytest.mark.asyncio
async def test_claim_attempts_are_rate_limited(tmp_path, public_auth):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    s = await _connect(url, Ed25519PrivateKey.generate())
    try:
        code = gw._owner_claim_code()
        for _ in range(gw._OWNER_CLAIM_FAIL_LIMIT):
            r = await s.cmd({"cmd": "claim_gateway", "code": "guess"}, _CLAIM_REPLIES)
            assert r["reason"] == "invalid_code"
        # Locked out: even the right code is refused until the window passes.
        r = await s.cmd({"cmd": "claim_gateway", "code": code}, _CLAIM_REPLIES)
        assert r == {"type": "claim_failed", "reason": "rate_limited"}
        assert gw._store.list_owner_devices() == []
    finally:
        await s.ws.close()


@pytest.mark.asyncio
async def test_claim_needs_a_proven_identity(tmp_path, monkeypatch):
    """With auth off nothing is proven, so there is nothing to bind a claim to."""
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")
    gw = _gateway(tmp_path / "gw.db", host="0.0.0.0")
    url = _serve(gw)
    s = await _connect(url, Ed25519PrivateKey.generate())
    try:
        gw._owner_claim = {"code": "x" * 32, "expires_at": time.time() + 60}
        r = await s.cmd({"cmd": "claim_gateway", "code": "x" * 32}, _CLAIM_REPLIES)
        assert r == {"type": "claim_failed", "reason": "auth_required"}
        assert all(d["via"] != "claim" for d in gw._store.list_owner_devices())
    finally:
        await s.ws.close()


@pytest.mark.asyncio
async def test_public_bind_requires_claim_and_owner_survives_restart(tmp_path, monkeypatch):
    """A non-loopback bind with no auth override: auth is enforced, the gateway
    starts unclaimed, and after a restart the claimed owner (and its linked
    device) are still the owner while a guest is not."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    monkeypatch.delenv("PROXION_ADMIN_API_TOKEN", raising=False)
    agent = AgentState.generate()
    db = tmp_path / "gw.db"
    gw = _gateway(db, agent=agent, host="0.0.0.0")
    assert gw._auth_enforced() and gw._owner_claim_open()
    url = _serve(gw)
    owner_key = Ed25519PrivateKey.generate()
    s = await _connect(url, owner_key)
    try:
        r = await s.cmd({"cmd": "claim_gateway", "code": gw._owner_claim_code()}, _CLAIM_REPLIES)
        assert r["type"] == "gateway_claimed"
    finally:
        await s.ws.close()

    gw2 = _gateway(db, agent=agent, host="0.0.0.0")
    assert gw2._owner_account_dids == {_did(owner_key)}
    assert not gw2._owner_claim_open() and gw2.owner_claim_url() == ""
    url2 = _serve(gw2)
    again = await _connect(url2, owner_key)
    phone_key = Ed25519PrivateKey.generate()
    phone = await _connect(url2, phone_key, cert=issue_device_cert(owner_key, _did(phone_key)))
    guest = await _connect(url2, Ed25519PrivateKey.generate())
    try:
        assert again.registered["owner"] is True and again.registered.get("gateway_delegation_cert")
        assert phone.registered["webid"] == _did(owner_key)
        assert phone.registered["owner"] is True and phone.registered.get("gateway_delegation_cert")
        assert guest.registered["owner"] is False and "claimable" not in guest.registered
        st = await phone.cmd({"cmd": "tunnel_status"}, {"tunnel_status", "error"})
        assert st["type"] == "tunnel_status"
        st = await guest.cmd({"cmd": "tunnel_status"}, {"tunnel_status", "error"})
        assert st.get("message") == "gateway_owner_only"
    finally:
        for x in (again, phone, guest):
            await x.ws.close()


@pytest.mark.asyncio
async def test_owner_paired_device_through_pairing_flow_is_owner(tmp_path, public_auth):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    owner_key = Ed25519PrivateKey.generate()
    owner = await _connect(url, owner_key)
    try:
        await owner.cmd({"cmd": "claim_gateway", "code": gw._owner_claim_code()}, {"gateway_claimed"})
        started = await owner.cmd({"cmd": "pair_start"}, {"pairing_started"})
        dev_key = Ed25519PrivateKey.generate()
        async with websockets.connect(url) as dev_ws:
            await dev_ws.send(json.dumps({"cmd": "pair_submit", "pairing_code": started["pairing_code"],
                                          "device_did": _did(dev_key)}))
            req = await owner.wait({"pairing_request"})
            cert = issue_device_cert(owner_key, req["device_did"])
            await owner.cmd({"cmd": "pair_approve", "pairing_code": started["pairing_code"],
                             "delegation_cert": cert}, {"pairing_approve_ack"})
            while True:
                ev = json.loads(await asyncio.wait_for(dev_ws.recv(), 5.0))
                if ev.get("type") == "pairing_approved":
                    break
        dev = await _connect(url, dev_key, cert=ev["delegation_cert"])
        try:
            assert dev.registered["owner"] is True
            listed = await dev.cmd({"cmd": "list_owner_devices"}, {"owner_devices", "error"})
            assert listed["type"] == "owner_devices"
        finally:
            await dev.ws.close()
    finally:
        await owner.ws.close()


@pytest.mark.asyncio
async def test_admin_token_claims(tmp_path, public_auth, monkeypatch):
    monkeypatch.setenv("PROXION_ADMIN_API_TOKEN", "t" * 64)
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    first, second = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    a = await _connect(url, first)
    try:
        r = await a.cmd({"cmd": "claim_gateway", "code": "t" * 64}, _CLAIM_REPLIES)
        assert r["type"] == "gateway_claimed"
    finally:
        await a.ws.close()
    # The token is the operator's credential, so it still adds an owner after
    # the claim (recovery when every owner device is lost); a code does not.
    b = await _connect(url, second)
    try:
        r = await b.cmd({"cmd": "claim_gateway", "code": "t" * 63}, _CLAIM_REPLIES)
        assert r["reason"] == "invalid_code"
        r = await b.cmd({"cmd": "claim_gateway", "code": "t" * 64}, _CLAIM_REPLIES)
        assert r["type"] == "gateway_claimed"
        vias = sorted(d["via"] for d in gw._store.list_owner_devices())
        assert vias == ["token", "token"]
    finally:
        await b.ws.close()


@pytest.mark.asyncio
async def test_remove_self_needs_confirm_and_reopens_the_claim(tmp_path, public_auth):
    gw = _gateway(tmp_path / "gw.db")
    url = _serve(gw)
    key = Ed25519PrivateKey.generate()
    s = await _connect(url, key)
    try:
        await s.cmd({"cmd": "claim_gateway", "code": gw._owner_claim_code()}, {"gateway_claimed"})
        r = await s.cmd({"cmd": "remove_owner_device", "did": _did(key)}, {"owner_device_removed", "error"})
        assert r.get("message") == "confirm_required"
        assert gw._store.list_owner_devices()
        r = await s.cmd({"cmd": "remove_owner_device", "did": _did(key), "confirm": True},
                        {"owner_device_removed", "error"})
        assert r == {"type": "owner_device_removed", "did": _did(key), "removed": True}
        assert gw._store.list_owner_devices() == []
        assert gw._owner_claim_open() and gw._owner_claim and gw._owner_claim["code"]
        r = await s.cmd({"cmd": "list_owner_devices"}, {"owner_devices", "error"})
        assert r.get("message") == "gateway_owner_only"
    finally:
        await s.ws.close()


@pytest.mark.asyncio
async def test_loopback_desktop_owner_is_recorded_without_a_claim(tmp_path, monkeypatch):
    """The desktop gateway: loopback bind, auth off. No claim, nothing to do,
    and the local browser identity is recorded as the owner."""
    monkeypatch.delenv("PROXION_REQUIRE_AUTH", raising=False)
    gw = _gateway(tmp_path / "gw.db")
    assert not gw._owner_claim_open() and gw.owner_claim_url() == ""
    url = _serve(gw)
    key = Ed25519PrivateKey.generate()
    s = await _connect(url, key)
    try:
        assert s.registered["owner"] is True and "claimable" not in s.registered
        assert [d["did"] for d in gw._store.list_owner_devices()] == [_did(key)]
        listed = await s.cmd({"cmd": "list_owner_devices"}, {"owner_devices", "error"})
        assert listed["type"] == "owner_devices"
        # Still no claim: the desktop never logs a claim link.
        assert gw._owner_claim is None
    finally:
        await s.ws.close()


def test_recorded_owner_matches_only_a_proven_socket(tmp_path, monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "1")
    gw = _gateway(tmp_path / "gw.db")
    did = "did:key:z6MkOwnerExample"
    gw._remember_owner(did, "claim")

    class _Ws:
        remote_address = ("203.0.113.9", 1)
        request = None
        request_headers = {}

    ws = _Ws()
    gw._client_webids[ws] = did
    assert not gw._owner_by_did_or_local(ws)
    gw._proven_sockets.add(ws)
    assert gw._owner_by_did_or_local(ws)


def test_owner_rows_are_wrapped_at_rest_and_hand_written_rows_ignored(tmp_path):
    agent = AgentState.generate()
    db = tmp_path / "s.db"
    store = LocalStore(str(db), db_wrap_key=agent.db_wrap_key())
    did = "did:key:z6MkWrappedOwner"
    assert store.add_owner_device(did, "claim") is True
    assert store.add_owner_device(did, "claim") is False
    raw = sqlite3.connect(str(db)).execute("SELECT did_wrapped FROM owner_devices").fetchall()
    assert raw and all(did not in r[0] for r in raw)
    # A row written straight into the file, without the store key, is not an owner.
    conn = sqlite3.connect(str(db))
    conn.execute("INSERT INTO owner_devices (did_wrapped, via, added_at) VALUES (?, 'x', 0)",
                 ("did:key:z6MkIntruder",))
    conn.commit()
    conn.close()
    assert [d["did"] for d in store.list_owner_devices()] == [did]
    assert store.remove_owner_device(did) is True
    assert store.list_owner_devices() == []

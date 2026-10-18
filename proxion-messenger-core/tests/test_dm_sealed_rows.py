"""An E2E DM is stored as browser ratchet ciphertext the gateway cannot read.

The row is marked (content_type 'e2e') so local_history flags it ``e2e``, a pin
of it carries no ciphertext, and server-side search never matches it. The
browser then shows its own plaintext copy or an undecryptable notice.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.persist import AgentState

CT = "q3Zx9_Lw-0aBcDeFgHiJkLmNoPqRsTuV"


def _did():
    return pub_key_to_did(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _ws():
    ws = AsyncMock()
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 12345)
    return ws


def _events(ws, kind):
    out = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    return [e for e in out if e.get("type") == kind]


@pytest.fixture
def noauth(monkeypatch):
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


@pytest.fixture
def gw(tmp_path):
    return ProxionGateway(
        agent=AgentState.generate(), dm_clients={}, room_memberships={},
        config=GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "g.db")),
    )


async def _register(gw, ws, did):
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": did, "display_name": "A"})


def _seed(gw, owner):
    gw._store.save_relationship(
        {"certificate_id": "cert-A", "subject": "ab" * 32, "created_at": 0,
         "expires_at": 2**31 - 1}, peer_did="did:key:zPeer", owner_webid=owner)
    gw._store.save_dm_thread("cert-A", "did:key:zPeer", "Peer", owner_webid=owner)
    gw._store.save_message("m-sealed", "cert-A", "dm", owner, "A", CT,
                           "2026-01-01T00:00:00Z", e2e=True)
    gw._store.save_message("m-plain", "cert-A", "dm", owner, "A", "plain hello",
                           "2026-01-01T00:00:01Z")


def test_store_marks_sealed_rows(tmp_path):
    st = LocalStore(str(tmp_path / "s.db"))
    st.save_message("a", "t", "dm", "did:key:x", None, CT, "2026-01-01T00:00:00Z", e2e=True)
    st.save_message("b", "t", "dm", "did:key:x", None, "hi", "2026-01-01T00:00:01Z")
    assert st.get_message("a")["content_type"] == "e2e"
    assert st.get_message("b")["content_type"] == "text"


def test_search_skips_sealed_rows(tmp_path):
    st = LocalStore(str(tmp_path / "s.db"))
    # Ciphertext that happens to contain the query is still not a hit.
    st.save_message("a", "t", "dm", "did:key:x", None, "zebra" + CT, "2026-01-01T00:00:00Z", e2e=True)
    st.save_message("a2", "t", "dm", "did:key:x", None, "zebra", "2026-01-01T00:00:00Z", e2e=True)
    st.save_message("b", "t", "dm", "did:key:x", None, "zebra plain", "2026-01-01T00:00:01Z")
    ids = [r["message_id"] for r in st.search_messages("zebra", ["t"])]
    assert ids == ["b"]


@pytest.mark.asyncio
async def test_local_dm_e2e_send_is_stored_sealed(gw, noauth):
    a = _did()
    ws = _ws()
    await _register(gw, ws, a)
    await gw.process_command(ws, {
        "cmd": "local_dm", "target_webid": "did:key:zBob", "thread_id": "did:key:zBob",
        "content": CT, "message_id": "m-e2e", "e2e": True, "nonce": "bm9uY2U",
        "msg_num": 0, "pn": 0, "ratchet_pub": "cHVi",
    })
    row = gw._store.get_message("m-e2e")
    assert row is not None and row["content_type"] == "e2e"


@pytest.mark.asyncio
async def test_history_flags_sealed_dm(gw, noauth):
    a = _did()
    _seed(gw, a)
    ws = _ws()
    await _register(gw, ws, a)
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_local_history", "thread_id": "cert-A"})
    msgs = {m["message_id"]: m for m in _events(ws, "local_history")[-1]["messages"]}
    assert msgs["m-sealed"].get("e2e") is True
    assert "e2e" not in msgs["m-plain"]


@pytest.mark.asyncio
async def test_pin_of_sealed_dm_carries_no_ciphertext(gw, noauth):
    a = _did()
    _seed(gw, a)
    # A pin saved before this fix kept the ciphertext snapshot.
    gw._store.save_pin("cert-A", "m-old", a, CT)
    gw._store.save_message("m-old", "cert-A", "dm", a, "A", CT, "2026-01-01T00:00:02Z", e2e=True)
    ws = _ws()
    await _register(gw, ws, a)
    for mid in ("m-sealed", "m-plain"):
        await gw.process_command(ws, {"cmd": "pin_message", "message_id": mid, "thread_id": "dm:cert-A"})
    stored = {p["message_id"]: p for p in gw._store.get_pins("cert-A")}
    assert stored["m-sealed"]["content"] == ""
    ws.send.reset_mock()
    await gw.process_command(ws, {"cmd": "get_pins", "thread_id": "dm:cert-A"})
    pins = {p["message_id"]: p for p in _events(ws, "pins")[-1]["pins"]}
    assert pins["m-sealed"] == {**pins["m-sealed"], "content": "", "e2e": True}
    assert pins["m-old"]["content"] == "" and pins["m-old"]["e2e"] is True
    assert pins["m-plain"]["content"] == "plain hello" and "e2e" not in pins["m-plain"]
    assert CT not in json.dumps(pins)


def test_pins_in_the_same_millisecond_are_all_kept(tmp_path, monkeypatch):
    st = LocalStore(str(tmp_path / "s.db"))
    monkeypatch.setattr("proxion_messenger_core._store.messages.time.time", lambda: 1700000000.0)
    for mid in ("a", "b", "c"):
        st.save_pin("t", mid, "did:key:x")
    assert sorted(p["message_id"] for p in st.get_pins("t")) == ["a", "b", "c"]

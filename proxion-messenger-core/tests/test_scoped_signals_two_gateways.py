"""Two real gateways: typing and a sessionless hangup still cross, scoped.

Typing from a friend on another gateway reaches the account that holds the
relationship, and a callee who repeats a hangup with no local session (the
cross-gateway decline path) reaches the caller once, not once per repeat.
"""
import asyncio
import json
import time
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("websockets")
from gwharness import free_port, start_gateway


@pytest.fixture(autouse=True)
def _loopback_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


def _start(tmp_path, name):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    ws_port, http_port = free_port(), free_port()
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}",
                        db_path=str(tmp_path / f"{name}.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    handle = start_gateway(gw, ws_port, http_port)
    return gw, handle.loop, http_port


def _sock():
    s = AsyncMock()
    s.__hash__ = lambda x: id(x)
    s.__eq__ = lambda x, o: x is o
    return s


def _attach(gw, sock, did):
    gw.clients.add(sock)
    gw._client_webids[sock] = did
    gw._webid_sockets.setdefault(did, set()).add(sock)


def _sent(sock, type_):
    return [e for e in (json.loads(c.args[0]) for c in sock.send.call_args_list)
            if e.get("type") == type_]


async def _wait(pred, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.1)
    return pred()


@pytest.mark.asyncio
async def test_typing_and_sessionless_hangup_cross_scoped(tmp_path):
    from proxion_messenger_core import _gateway_voice
    getattr(_gateway_voice, "_sessionless_hangups", {}).clear()
    gw_a, loop_a, http_a = _start(tmp_path, "a")
    gw_b, loop_b, http_b = _start(tmp_path, "b")
    a_did, b_did = gw_a._own_gateway_did(), gw_b._own_gateway_did()
    alice, bob, carol = "did:key:z6MkAliceSess", "did:key:z6MkBobSess", "did:key:z6MkCarolSess"
    alice_sock, carol_sock, bob_sock = _sock(), _sock(), _sock()

    def _setup_a():
        gw_a._store.save_relationship({"certificate_id": "cert-A", "subject": gw_b.agent.identity_pub_bytes.hex(),
                                       "created_at": 0, "expires_at": 2**31 - 1},
                                      peer_did=b_did, owner_webid=alice)
        gw_a._peer_gateway_urls[b_did] = f"http://127.0.0.1:{http_b}"
        _attach(gw_a, alice_sock, alice)
        _attach(gw_a, carol_sock, carol)    # another account on A, not Bob's contact

    def _setup_b():
        gw_b._store.save_relationship({"certificate_id": "cert-B", "subject": gw_a.agent.identity_pub_bytes.hex(),
                                       "created_at": 0, "expires_at": 2**31 - 1},
                                      peer_did=a_did, owner_webid=bob)
        gw_b._peer_gateway_urls[a_did] = f"http://127.0.0.1:{http_a}"
        _attach(gw_b, bob_sock, bob)
    loop_a.call_soon_threadsafe(_setup_a)
    loop_b.call_soon_threadsafe(_setup_b)
    await asyncio.sleep(0.2)

    asyncio.run_coroutine_threadsafe(
        gw_b._handle_typing(bob_sock, {"cert_id": "cert-B"}), loop_b).result(timeout=5)
    assert await _wait(lambda: _sent(alice_sock, "typing")), "typing never crossed"
    assert _sent(alice_sock, "typing")[-1]["cert_id"] == "cert-A"
    assert not _sent(carol_sock, "typing")

    for _ in range(5):
        asyncio.run_coroutine_threadsafe(gw_b._handle_voice_hangup(
            bob_sock, {"session_id": "call-1", "target_webid": a_did, "reason": "declined"}),
            loop_b).result(timeout=5)
    def _hangups(sock):
        return [e for e in _sent(sock, "voice_signal") if e.get("signal_type") == "hangup"]
    assert await _wait(lambda: _hangups(alice_sock)), "hangup never crossed"
    await asyncio.sleep(0.5)
    assert len(_hangups(alice_sock)) == 1
    assert not _hangups(carol_sock)
    getattr(_gateway_voice, "_sessionless_hangups", {}).clear()

"""An E2E DM sent with send_dm must reach the peer with its ratchet header intact,
also when the sending gateway has a pod client for the relationship.

With a pod connected, a relationship cert lands in ``dm_clients`` and send_dm used
to hand the browser's ciphertext to ``messaging.compose``. That kept only
``content`` and ``reply_to_id`` (dropping ``e2e``, ``ratchet_pub``, ``msg_num``,
``pn``, ``nonce`` and ``x25519_pub``), wrapped the ciphertext again under a key
derived from the relationship certificate, wrote it to the sender's own pod and
never relayed it, so the recipient's browser could not decrypt it even when it
arrived.

Two live gateways (tests/gwharness.py) share an in-memory pod stub so the pod
path is exercised end to end without a Solid server.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

pytest.importorskip("websockets")
import websockets
from gwharness import free_port, start_gateway

_DELIVER_TIMEOUT = 15.0

_HEADER = {
    "e2e": True,
    "nonce": "bm9uY2Vub25jZW5v",
    "msg_num": 3,
    "pn": 2,
    "ratchet_pub": "UkFUQ0hFVF9QVUJfQkFTRTY0VVJMX0VOQ09ERURfMzJC",
    "x25519_pub": "WDI1NTE5X1BVQl9CQVNFNjRVUkxfRU5DT0RFRF8zMkJZ",
}
_CIPHERTEXT = "Q0lQSEVSVEVYVF9BTkRfR0NNX1RBR19CQVNFNjRVUkw"


class _MemPod:
    """Minimal stash:// pod stub with the SolidClient surface the gateway uses."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put(self, uri, data, content_type="application/octet-stream", **_kw):
        self.objects[uri] = data if isinstance(data, bytes) else str(data).encode()
        return uri

    def get(self, uri, **_kw):
        from proxion_messenger_core.solid_client import SolidError
        if uri not in self.objects:
            raise SolidError(f"404 {uri}")
        return self.objects[uri]

    def list(self, container, **_kw):
        return [u for u in self.objects if u.startswith(container)
                and "/" not in u[len(container):]]

    def delete(self, uri, **_kw):
        self.objects.pop(uri, None)

    def exists(self, uri, **_kw):
        return uri in self.objects


def _gateway(agent, ws_port, http_port, db_path):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.readstate import ReadState
    cfg = GatewayConfig(host="127.0.0.1", port=ws_port, http_port=http_port,
                        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path)
    gw = ProxionGateway(agent, {}, {}, cfg, ReadState())
    return gw, start_gateway(gw, ws_port, http_port)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_REQUIRE_AUTH", "0")


async def _drain(ws, timeout=0.1):
    while True:
        try:
            await asyncio.wait_for(ws.recv(), timeout=timeout)
        except Exception:
            break


@pytest.mark.asyncio
async def test_send_dm_with_pod_client_keeps_ratchet_header(tmp_path):
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.federation import RelationshipCertificate, Capability

    agent_a, agent_b = AgentState.generate(), AgentState.generate()
    ws_a, ws_b, http_a, http_b = free_port(), free_port(), free_port(), free_port()
    gw_a, _ = _gateway(agent_a, ws_a, http_a, str(tmp_path / "a.db"))
    gw_b, h_b = _gateway(agent_b, ws_b, http_b, str(tmp_path / "b.db"))

    did_a = pub_key_to_did(agent_a.identity_pub_bytes)
    did_b = pub_key_to_did(agent_b.identity_pub_bytes)

    # A real relationship cert, shaped like the one the invite flow mints.
    cert = RelationshipCertificate(
        issuer=agent_a.identity_pub_bytes.hex(),
        subject=agent_b.identity_pub_bytes.hex(),
        capabilities=[Capability(with_="stash://dm/", can="crud/write")],
    )
    cert.sign(agent_a.identity_key)
    pod = _MemPod()
    for gw, peer in ((gw_a, did_b), (gw_b, did_a)):
        gw._store.save_relationship(cert.to_dict(), peer_did=peer)
        gw.dm_clients[cert.certificate_id] = (cert, pod)
    # A's operator pod is connected (what puts the cert into dm_clients for real).
    gw_a._pod_webid = "https://a.example/profile/card#me"
    gw_a.own_pod_clients[gw_a._pod_webid] = (None, pod)
    gw_a._record_peer_gateway(did_b, f"http://127.0.0.1:{http_b}")
    gw_b._record_peer_gateway(did_a, f"http://127.0.0.1:{http_a}")

    async with websockets.connect(f"ws://127.0.0.1:{ws_a}") as conn_a, \
               websockets.connect(f"ws://127.0.0.1:{ws_b}") as conn_b:
        await conn_a.send(json.dumps({"cmd": "register", "did": did_a}))
        await conn_b.send(json.dumps({"cmd": "register", "did": did_b}))
        await asyncio.sleep(0.15)
        await _drain(conn_a)
        await _drain(conn_b)

        await conn_a.send(json.dumps({
            "cmd": "send_dm", "cert_id": cert.certificate_id,
            "message_id": "msg-e2e-pod-1", "content": _CIPHERTEXT,
            **_HEADER,
        }))

        delivered = None
        polled = False
        deadline = time.monotonic() + _DELIVER_TIMEOUT
        while time.monotonic() < deadline:
            # Give the pod path its chance too: run B's pod poll once.
            if not polled and time.monotonic() > deadline - _DELIVER_TIMEOUT + 1.0:
                polled = True
                fut = asyncio.run_coroutine_threadsafe(gw_b.do_poll(), h_b.loop)
                try:
                    await asyncio.wrap_future(fut)
                except Exception:
                    pass
            try:
                msg = json.loads(await asyncio.wait_for(conn_b.recv(), timeout=0.3))
            except asyncio.TimeoutError:
                continue
            if msg.get("type") == "message":
                delivered = msg
                break

    assert delivered is not None, (
        f"recipient got no message event; pod holds {sorted(pod.objects)}")
    assert delivered["content"] == _CIPHERTEXT, "ciphertext must arrive untouched"
    for k, v in _HEADER.items():
        assert delivered.get(k) == v, f"ratchet header field {k!r} lost: {delivered.get(k)!r}"
    assert delivered.get("message_id") == "msg-e2e-pod-1"
    # No header-less re-composed copy of the E2E DM on the pod: it could never be
    # decrypted, and a pod restore would import it as a duplicate message.
    await asyncio.sleep(0.3)  # let the pod write-through tasks finish
    stray = [u for u in pod.objects if u.startswith("stash://messages/thread/")]
    assert not stray, f"E2E DM re-composed without its ratchet header at {stray}"
    stored = [json.loads(v) for u, v in pod.objects.items()
              if u.startswith("stash://pod/local_dms/")]
    assert stored and all(s.get("ratchet_pub") == _HEADER["ratchet_pub"] for s in stored)


@pytest.mark.asyncio
async def test_e2e_send_dm_still_honours_sender_block(tmp_path):
    """Routing E2E sends around the pod compose path must keep its block check."""
    from unittest.mock import AsyncMock
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.didkey import pub_key_to_did
    from proxion_messenger_core.federation import RelationshipCertificate, Capability

    gw = ProxionGateway(AgentState.generate(), {}, {},
                        GatewayConfig(host="127.0.0.1", db_path=str(tmp_path / "blk.db")))
    peer = AgentState.generate()
    peer_did = pub_key_to_did(peer.identity_pub_bytes)
    cert = RelationshipCertificate(
        issuer=gw.agent.identity_pub_bytes.hex(), subject=peer.identity_pub_bytes.hex(),
        capabilities=[Capability(with_="stash://dm/", can="crud/write")])
    cert.sign(gw.agent.identity_key)
    gw._store.save_relationship(cert.to_dict(), peer_did=peer_did)
    gw.dm_clients[cert.certificate_id] = (cert, _MemPod())

    ws = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    ws.remote_address = ("127.0.0.1", 1)
    me = pub_key_to_did(gw.agent.identity_pub_bytes)
    gw.clients.add(ws)
    await gw.process_command(ws, {"cmd": "register", "did": me})
    await gw.process_command(ws, {"cmd": "block", "webid": peer_did})
    ws.send.reset_mock()

    await gw.process_command(ws, {
        "cmd": "send_dm", "cert_id": cert.certificate_id, "message_id": "m-blk",
        "content": _CIPHERTEXT, **_HEADER,
    })
    sent = [json.loads(c[0][0]) for c in ws.send.call_args_list]
    assert any(e.get("message") == "Recipient is blocked" for e in sent), sent
    assert not any(e.get("type") == "message" for e in sent)

"""A removed contact must not re-add themselves by replaying /invite/accept.

The nonce row for an acceptance only lives 24h. After that, an old acceptance of a
request the owner sent long ago reached _lift_revocation_on_readd and re-friended a
contact the owner had removed. /invite/accept now only honours a live, still-pending
request this gateway sent, and removing a contact cancels the requests pending to
them. The owner re-adding someone (a new request, then their accept) still works.
"""
import asyncio
import json
import socket
import time
from unittest.mock import AsyncMock, patch

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from proxion_messenger_core import handshake
from proxion_messenger_core.didkey import pub_key_to_did
from proxion_messenger_core.federation import Capability, RelationshipCertificate
from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


def _raw(priv):
    return priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def _gw(tmp_path, **cfg):
    return ProxionGateway(agent=AgentState.generate(), dm_clients={}, room_memberships={},
                          config=GatewayConfig(db_path=str(tmp_path / "s.db"), **cfg),
                          read_state=ReadState())


def _send_request(gw, peer_did, expires_at=None):
    """Store a friend request from the owner to peer_did, as _handle_send_friend_request does."""
    store_pub = _raw(X25519PrivateKey.generate())
    invite = handshake.create_invite(gw.agent.identity_key, store_pub,
                                     [Capability(with_="stash://dm/", can="crud/write")],
                                     endpoint_hints=["https://me.example.com"])
    if expires_at is not None:
        invite.expires_at = expires_at
        invite.sign(gw.agent.identity_key)
    gw._store.save_pending_invite(invite.to_dict(), peer_did,
                                  sender_webid=gw._own_gateway_did())
    return invite.invitation_id


def _accept_body(gw, inv_id, peer):
    raw = _raw(peer)
    cert = RelationshipCertificate(issuer=raw.hex(), subject=gw.agent.identity_pub_bytes.hex(),
                                   capabilities=[Capability(with_="stash://dm/", can="crud/write")])
    cert.sign(peer)
    return json.dumps({"@type": "InviteAcceptance", "invitation_id": inv_id,
                       "certificate": cert.to_dict(), "from_did": pub_key_to_did(raw),
                       "from_pub_hex": raw.hex(), "from_display_name": "Bob"}).encode()


def _pod_patches(gw):
    return (patch.object(gw, "_sync_cert_to_pod", new=AsyncMock()),
            patch.object(gw, "_sync_revocation_to_pod", new=AsyncMock()),
            patch.object(gw, "_delete_revocation_tombstones_from_pod", new=AsyncMock()))


async def _remove(gw, peer_did):
    ws = AsyncMock()
    gw._client_webids[ws] = gw._own_gateway_did()
    rel = gw._store.get_relationship_by_did(peer_did)
    await gw._handle_revoke_contact(ws, {"cert_id": rel["certificate_id"]})


@pytest.mark.asyncio
async def test_replayed_accept_after_removal_is_refused(tmp_path):
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    inv = _send_request(gw, bob_did)
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        st, _ = await gw._handle_invite_accept_post(_accept_body(gw, inv, bob))
        assert st.startswith("200")
        await _remove(gw, bob_did)
        assert bob_did in gw._revoked_dids
        # A day later the nonce row is gone; Bob replays an acceptance of the same request.
        gw._store.prune_invite_nonces(time.time() + 10)
        st2, _ = await gw._handle_invite_accept_post(_accept_body(gw, inv, bob))
    assert st2.startswith("409")
    assert bob_did in gw._revoked_dids
    assert gw._store.is_revoked(bob_did)
    assert gw._store.get_relationship_by_did(bob_did) is None


@pytest.mark.asyncio
async def test_removing_a_contact_cancels_requests_still_pending_to_them(tmp_path):
    """A request sent before the removal, never accepted, cannot re-add them either."""
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    first = _send_request(gw, bob_did)
    stale = _send_request(gw, bob_did)
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        assert (await gw._handle_invite_accept_post(_accept_body(gw, first, bob)))[0].startswith("200")
        await _remove(gw, bob_did)
        st, _ = await gw._handle_invite_accept_post(_accept_body(gw, stale, bob))
    assert st.startswith("409")
    assert bob_did in gw._revoked_dids


@pytest.mark.asyncio
async def test_expired_request_cannot_be_accepted(tmp_path):
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    inv = _send_request(gw, bob_did, expires_at=int(time.time()) - 60)
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        st, _ = await gw._handle_invite_accept_post(_accept_body(gw, inv, bob))
    assert st.startswith("410")
    assert gw._store.get_relationship_by_did(bob_did) is None


@pytest.mark.asyncio
async def test_accepting_their_own_inbound_request_is_refused(tmp_path):
    """The pending_invites table also holds requests others sent us. Bob cannot
    'accept' his own request over HTTP; only the owner accepts it (WS path)."""
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    invite = handshake.create_invite(bob, _raw(X25519PrivateKey.generate()),
                                     [Capability(with_="stash://dm/", can="crud/write")],
                                     endpoint_hints=["https://bob.example.com"])
    gw._store.save_pending_invite(invite.to_dict(), bob_did)
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        st, _ = await gw._handle_invite_accept_post(_accept_body(gw, invite.invitation_id, bob))
    assert st.startswith("400")
    assert gw._store.get_relationship_by_did(bob_did) is None


@pytest.mark.asyncio
async def test_owner_readd_after_removal_still_works(tmp_path):
    """The owner sends a NEW request after removing Bob; Bob accepting it re-adds him."""
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        first = _send_request(gw, bob_did)
        assert (await gw._handle_invite_accept_post(_accept_body(gw, first, bob)))[0].startswith("200")
        await _remove(gw, bob_did)
        again = _send_request(gw, bob_did)
        st, _ = await gw._handle_invite_accept_post(_accept_body(gw, again, bob))
    assert st.startswith("200")
    assert bob_did not in gw._revoked_dids
    assert not gw._store.is_revoked(bob_did)
    assert gw._store.get_relationship_by_did(bob_did)


@pytest.mark.asyncio
async def test_accept_never_lifts_a_device_revocation(tmp_path):
    gw = _gw(tmp_path)
    gw.broadcast = AsyncMock()
    dev = Ed25519PrivateKey.generate()
    dev_did = pub_key_to_did(_raw(dev))
    gw._store.mark_revoked(f"device:{dev_did}", dev_did)
    gw._revoked_dids.add(dev_did)
    inv = _send_request(gw, dev_did)
    p1, p2, p3 = _pod_patches(gw)
    with p1, p2, p3:
        await gw._handle_invite_accept_post(_accept_body(gw, inv, dev))
    assert dev_did in gw._revoked_dids
    assert gw._store.is_revoked(dev_did)


def test_restored_tombstone_cancels_only_older_requests(tmp_path):
    from proxion_messenger_core.local_store import LocalStore
    store = LocalStore(str(tmp_path / "t.db"))
    store.save_pending_invite({"invitation_id": "old"}, "did:key:zP")
    with store._conn() as c:
        c.execute("UPDATE pending_invites SET created_at = ? WHERE invitation_id='old'",
                  (int(time.time()) - 1000,))
    store.save_pending_invite({"invitation_id": "new"}, "did:key:zP")
    assert store.cancel_pending_invites_for_peer("did:key:zP", created_before=time.time() - 500) == 1
    assert store.get_pending_invite_row("old")["status"] == "revoked"
    assert store.get_pending_invite_row("new")["status"] == "pending"


# ── HTTP level: the replay over a real socket ────────────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(port, path, body):
    # Synchronous, so the test never resets the main thread's event loop.
    import httpx
    return httpx.post(f"http://127.0.0.1:{port}{path}", content=body,
                      headers={"Content-Type": "application/json"}, timeout=10).status_code


def test_http_replay_after_removal_is_refused(tmp_path):
    from gwharness import start_gateway
    ws_port, http_port = _free_port(), _free_port()
    gw = _gw(tmp_path, host="127.0.0.1", port=ws_port, http_port=http_port,
             public_url=f"ws://127.0.0.1:{ws_port}")
    handle = start_gateway(gw, ws_port, http_port)
    bob = Ed25519PrivateKey.generate()
    bob_did = pub_key_to_did(_raw(bob))
    inv = _send_request(gw, bob_did)

    def on_loop(coro):
        return asyncio.run_coroutine_threadsafe(coro, handle.loop).result(timeout=10)

    assert _post(http_port, "/invite/accept", _accept_body(gw, inv, bob)) == 200
    assert gw._store.get_relationship_by_did(bob_did)

    on_loop(_remove(gw, bob_did))
    assert bob_did in gw._revoked_dids
    gw._store.prune_invite_nonces(time.time() + 10)

    assert _post(http_port, "/invite/accept", _accept_body(gw, inv, bob)) == 409
    assert bob_did in gw._revoked_dids
    assert gw._store.get_relationship_by_did(bob_did) is None

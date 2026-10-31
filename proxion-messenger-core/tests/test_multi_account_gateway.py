"""Several accounts on one gateway: what each session is handed at register."""
import asyncio
import time

import pytest

pytest.importorskip("websockets")
from test_room_multi_gateway import GW, Client, _room, _join, _loopback_federation  # noqa: F401


def _cert(cid, issuer_hex, peer_hex):
    now = int(time.time())
    return {"certificate_id": cid, "issuer": issuer_hex, "subject": peer_hex,
            "created_at": now, "expires_at": now + 86400}


async def _rooms_at_register(gw, client):
    await client.connect(gw)
    await asyncio.sleep(0.5)
    return {r["id"] for e in client.of("rooms") for r in e.get("rooms", [])}


@pytest.mark.asyncio
async def test_register_snapshot_lists_only_the_accounts_own_contacts(tmp_path):
    G = GW(tmp_path, "g")
    alice, bob = Client("Alice"), Client("Bob")
    peer_a, peer_b = Client("PeerA"), Client("PeerB")
    own = G.agent.identity_pub_bytes.hex()

    def seed():
        s = G.gw._store
        s.save_relationship(_cert("cert-a", own, peer_a.agent.identity_pub_bytes.hex()),
                            peer_did=peer_a.did, owner_webid=alice.did)
        s.save_relationship(_cert("cert-b", own, peer_b.agent.identity_pub_bytes.hex()),
                            peer_did=peer_b.did, owner_webid=bob.did)
    G.call(seed)
    try:
        await alice.connect(G)
        await bob.connect(G)
        for c, mine, theirs in ((alice, "cert-a", "cert-b"), (bob, "cert-b", "cert-a")):
            ev = await c.wait("relationships")
            assert ev, c.name
            ids = {x["certificate_id"] for x in ev["contacts"]}
            assert mine in ids and theirs not in ids, (c.name, ids)
    finally:
        for c in (alice, bob):
            await c.close()


@pytest.mark.asyncio
async def test_did_change_recovers_only_the_previous_identitys_rooms(tmp_path):
    """A loopback owner whose browser identity changed gets back the rooms its
    previous identity was in, but not a room it had left."""
    A, B = GW(tmp_path, "a"), GW(tmp_path, "b")
    old = await Client("Old").connect(A)
    bob = await Client("Bob").connect(B)
    try:
        left_rid, link = await _room(old, "left")
        await _join(bob, link, left_rid)
        old.events.clear()
        kept_rid, _ = await _room(old, "kept")
        assert kept_rid != left_rid
        await old.cmd(cmd="leave_local_room", room_id=left_rid)
        assert await old.wait("left_room", room_id=left_rid)
        assert A.call(lambda: left_rid in A.gw._local_rooms)
        await old.close()
        await asyncio.sleep(0.3)

        new = Client("New")
        rooms = await _rooms_at_register(A, new)
        assert rooms == {kept_rid}
        assert new.did not in A.call(lambda: A.gw._store.get_room_members(left_rid))
        await new.close()
    finally:
        for c in (old, bob):
            await c.close()


@pytest.mark.asyncio
async def test_new_account_is_not_adopted_into_other_accounts_rooms(tmp_path):
    G = GW(tmp_path, "g")
    alice = await Client("Alice").connect(G)
    bob = await Client("Bob").connect(G)
    try:
        ra, _ = await _room(alice, "alices")
        rb, _ = await _room(bob, "bobs")
        assert ra != rb
        # A newcomer while the accounts are online, and after they went offline.
        carol = Client("Carol")
        assert await _rooms_at_register(G, carol) == set()
        await carol.close()
        await alice.close()
        await bob.close()
        await asyncio.sleep(0.3)
        dave = Client("Dave")
        assert await _rooms_at_register(G, dave) == set()
        await dave.close()
        for rid in (ra, rb):
            members = G.call(lambda rid=rid: G.gw._store.get_room_members(rid))
            assert carol.did not in members and dave.did not in members
    finally:
        for c in (alice, bob):
            await c.close()


@pytest.mark.asyncio
async def test_new_account_is_not_adopted_while_the_previous_one_is_online(tmp_path):
    G = GW(tmp_path, "g")
    alice = await Client("Alice").connect(G)
    try:
        await _room(alice, "alices")
        carol = Client("Carol")
        assert await _rooms_at_register(G, carol) == set()
        await carol.close()
    finally:
        await alice.close()

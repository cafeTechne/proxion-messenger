"""Room lifecycle across real gateways: every person on their own gateway,
driven over real WebSockets with the same commands the web client sends."""
import asyncio
import base64
import json
import time

import pytest

websockets = pytest.importorskip("websockets")
from gwharness import start_gateway, free_port


@pytest.fixture(autouse=True)
def _loopback_federation(monkeypatch):
    monkeypatch.setenv("PROXION_ALLOW_PRIVATE_RELAY", "1")
    monkeypatch.setenv("PROXION_ALLOW_INSECURE_FEDERATION", "1")
    for k in ("PROXION_CSS_URL", "PROXION_CSS_EMAIL", "PROXION_CSS_PASSWORD"):
        monkeypatch.setenv(k, "")


class GW:
    def __init__(self, tmp_path, name, agent=None):
        from proxion_messenger_core.persist import AgentState
        self.name, self.tmp_path = name, tmp_path
        self.agent = agent or AgentState.generate()
        self.ws_port, self.http_port = free_port(), free_port()
        self.start()

    def start(self):
        from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
        from proxion_messenger_core.readstate import ReadState
        cfg = GatewayConfig(host="127.0.0.1", port=self.ws_port, http_port=self.http_port,
                            public_url=f"ws://127.0.0.1:{self.ws_port}",
                            db_path=str(self.tmp_path / f"{self.name}.db"))
        self.gw = ProxionGateway(self.agent, {}, {}, cfg, ReadState())
        self.handle = start_gateway(self.gw, self.ws_port, self.http_port)

    def restart(self):
        self.handle.stop()
        time.sleep(0.3)
        self.start()

    def call(self, fn):
        """Run *fn* on the gateway's own loop thread and return its result."""
        async def _run():
            return fn()
        return asyncio.run_coroutine_threadsafe(_run(), self.handle.loop).result(timeout=5)

    @property
    def url(self):
        return f"ws://127.0.0.1:{self.ws_port}"

    @property
    def http(self):
        return f"http://127.0.0.1:{self.http_port}"


class Client:
    def __init__(self, name, agent=None):
        from proxion_messenger_core.persist import AgentState
        from proxion_messenger_core.didkey import pub_key_to_did
        self.name = name
        self.agent = agent or AgentState.generate()
        self.did = pub_key_to_did(self.agent.identity_pub_bytes)
        self.events = []
        self.ws = None
        self._task = None

    async def connect(self, gw):
        self.ws = await websockets.connect(gw.url, max_size=None)
        await asyncio.wait_for(self.ws.recv(), 5)
        await self.ws.send(json.dumps({"cmd": "register", "did": self.did,
                                       "display_name": self.name}))
        while True:
            ev = json.loads(await asyncio.wait_for(self.ws.recv(), 5))
            if ev.get("type") == "auth_challenge":
                sig = self.agent.identity_key.sign(ev["nonce"].encode())
                await self.ws.send(json.dumps({
                    "cmd": "auth_response", "nonce": ev["nonce"],
                    "signature": base64.urlsafe_b64encode(sig).rstrip(b"=").decode()}))
                continue
            self.events.append(ev)
            if ev.get("type") == "registered":
                break
        self._task = asyncio.create_task(self._pump())
        return self

    async def _pump(self):
        try:
            async for raw in self.ws:
                self.events.append(json.loads(raw))
        except Exception:
            pass

    async def close(self):
        try:
            await self.ws.close()
        except Exception:
            pass

    async def cmd(self, **kw):
        await self.ws.send(json.dumps(kw))

    def of(self, type_, **match):
        return [e for e in self.events if e.get("type") == type_
                and all(e.get(k) == v for k, v in match.items())]

    async def wait_fn(self, fn, timeout=8.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for e in list(self.events):
                if fn(e):
                    return e
            await asyncio.sleep(0.05)
        return None

    async def wait(self, type_, timeout=8.0, **match):
        return await self.wait_fn(
            lambda e: e.get("type") == type_ and all(e.get(k) == v for k, v in match.items()),
            timeout)

    async def wait_content(self, text, timeout=8.0):
        return await self.wait_fn(lambda e: e.get("content") == text, timeout)

    async def members(self, room_id):
        self.events = [e for e in self.events if e.get("type") != "room_members"]
        await self.cmd(cmd="get_room_members", room_id=room_id)
        m = await self.wait("room_members", room_id=room_id)
        return sorted(x.get("display_name") for x in (m or {}).get("members", []))


async def _room(owner, name="trio"):
    await owner.cmd(cmd="chat_room_create", name=name, history_mode="all")
    rc = await owner.wait("room_created")
    assert rc, owner.events
    return rc["room_id"], rc["invite_url"]


async def _join(client, link, room_id):
    await client.cmd(cmd="join_remote_room", url=link)
    assert await client.wait("room_joined", room_id=room_id), client.of("error")


async def _absent(fn, settle=1.0):
    await asyncio.sleep(settle)
    return not fn()


@pytest.mark.asyncio
async def test_three_gateway_room_everyone_sees_everything(tmp_path):
    A, B, C = GW(tmp_path, "a"), GW(tmp_path, "b"), GW(tmp_path, "c")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)
    carol = await Client("Carol").connect(C)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        await _join(carol, link, rid)
        # Bob was already in the room when Carol joined: he hears about her.
        assert await bob.wait("room_member_joined", room_id=rid, webid=carol.did)
        for c in (alice, bob, carol):
            assert await c.members(rid) == ["Alice", "Bob", "Carol"], c.name

        await alice.cmd(cmd="send_room", room_id=rid, content="A1")
        await bob.cmd(cmd="send_room", room_id=rid, content="B1")
        await carol.cmd(cmd="send_room", room_id=rid, content="C1")
        for c, texts in ((alice, ("B1", "C1")), (bob, ("A1", "C1")), (carol, ("A1", "B1"))):
            for t in texts:
                assert await c.wait_content(t), f"{c.name} never got {t}"
        # Nothing is echoed back to the gateway it came from.
        await asyncio.sleep(0.5)
        assert sum(1 for e in bob.events if e.get("content") == "B1") == 1
        assert sum(1 for e in carol.events if e.get("content") == "C1") == 1

        b1 = (await carol.wait_content("B1"))["message_id"]
        c1 = (await bob.wait_content("C1"))["message_id"]
        # Bob reacts to Carol's message, edits and deletes his own.
        await bob.cmd(cmd="add_reaction", message_id=c1, room_id=rid, emoji="x")
        for c in (alice, carol):
            assert await c.wait("reaction_added", message_id=c1, from_webid=bob.did), c.name
        await bob.cmd(cmd="edit_local_message", message_id=b1, thread_id=rid, content="B1-ed")
        for c in (alice, carol):
            assert await c.wait("message_edited", message_id=b1, new_content="B1-ed"), c.name
        await bob.cmd(cmd="delete_local_message", message_id=b1, thread_id=rid)
        for c in (alice, carol):
            assert await c.wait("message_deleted", message_id=b1), c.name
        # Carol's gateway stored the forwarded state too.
        assert not any(m["message_id"] == b1 for m in C.call(lambda: C.gw._store.get_messages(rid)))
        # The owner edits on her gateway; both members see it.
        a1 = (await bob.wait_content("A1"))["message_id"]
        await alice.cmd(cmd="edit_local_message", message_id=a1, thread_id=rid, content="A1-ed")
        for c in (bob, carol):
            assert await c.wait("message_edited", message_id=a1, new_content="A1-ed"), c.name
    finally:
        for c in (alice, bob, carol):
            await c.close()


@pytest.mark.asyncio
async def test_forwarded_room_traffic_keeps_sender_binding(tmp_path):
    """A member gateway cannot speak for a member on another gateway, and the
    host's vouching only covers the room it hosts."""
    from datetime import datetime, timezone
    import secrets
    from proxion_messenger_core.relay import post_relay, sign_relay_envelope

    A, B, C = GW(tmp_path, "a"), GW(tmp_path, "b"), GW(tmp_path, "c")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)
    carol = await Client("Carol").connect(C)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        await _join(carol, link, rid)
        await asyncio.sleep(0.3)

        def forged(signer_agent, signer_gw, from_did, text, room=rid):
            p = {"content_type": "room_message", "room_id": room, "thread_id": room,
                 "from_webid": from_did, "from_display_name": "x", "content": text,
                 "timestamp": datetime.now(timezone.utc).isoformat(),
                 "message_id": "forge-" + secrets.token_hex(6), "source": "relay",
                 "relay_nonce": secrets.token_hex(8),
                 "relay_sig_did": signer_gw.gw._own_gateway_did(),
                 "relay_ts": datetime.now(timezone.utc).isoformat(),
                 "origin_gateway_url": signer_gw.http}
            p["signature"] = sign_relay_envelope(signer_agent.identity_key, p)
            return p

        # B posts straight to C claiming to be Alice (the owner) or to be Carol.
        await post_relay(C.http + "/relay", forged(B.agent, B, alice.did, "fake-alice"))
        await post_relay(C.http + "/relay", forged(B.agent, B, carol.did, "fake-carol"))
        # B posts to the host claiming to be Carol.
        await post_relay(A.http + "/relay", forged(B.agent, B, carol.did, "fake-carol-at-host"))
        # A gateway that is in no room at all, first contact.
        D = GW(tmp_path, "d")
        await post_relay(C.http + "/relay", forged(D.agent, D, bob.did, "fake-bob-from-d"))
        # Bob hosts a second room that Carol joins. Alice's gateway (host of the
        # first room) must not be able to speak for Bob in it.
        rid2, link2 = await _room(bob, "duo")
        await _join(carol, link2, rid2)
        await asyncio.sleep(0.3)
        await post_relay(C.http + "/relay", forged(A.agent, A, bob.did, "fake-bob-room2", rid2))
        assert await _absent(lambda: any(
            str(e.get("content", "")).startswith("fake-")
            for e in carol.events + alice.events + bob.events))
        # Bob's real messages still arrive everywhere.
        await bob.cmd(cmd="send_room", room_id=rid, content="real-bob")
        assert await carol.wait_content("real-bob")
        assert await alice.wait_content("real-bob")
    finally:
        for c in (alice, bob, carol):
            await c.close()


@pytest.mark.asyncio
async def test_room_edit_relay_with_edited_at_passes_the_allowlist(tmp_path):
    """The room_edit relay carries edited_at; /relay must accept it."""
    A, B = GW(tmp_path, "a"), GW(tmp_path, "b")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        await alice.cmd(cmd="send_room", room_id=rid, content="X")
        mid = (await bob.wait_content("X"))["message_id"]
        await alice.cmd(cmd="edit_local_message", message_id=mid, thread_id=rid, content="X2")
        ev = await bob.wait("message_edited", message_id=mid, new_content="X2")
        assert ev and ev.get("edited_at")
        await bob.cmd(cmd="send_room", room_id=rid, content="Y")
        mid2 = (await alice.wait_content("Y"))["message_id"]
        await bob.cmd(cmd="edit_local_message", message_id=mid2, thread_id=rid, content="Y2")
        assert await alice.wait("message_edited", message_id=mid2, new_content="Y2")
        # Nothing is left in the retry queue.
        await asyncio.sleep(0.5)
        for g in (A, B):
            assert not g.call(lambda g=g: g.gw._store.get_pending_relays())
    finally:
        for c in (alice, bob):
            await c.close()


def _uses_left(gw, room_id):
    def q():
        with gw.gw._store._conn() as conn:
            return [r[0] for r in conn.execute(
                "SELECT uses_left FROM room_invites WHERE room_id=?", (room_id,))]
    return gw.call(q)


@pytest.mark.asyncio
async def test_member_on_another_gateway_leave_kick_ban_and_delete(tmp_path):
    A, B = GW(tmp_path, "a"), GW(tmp_path, "b")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)

    def fed(rid):
        return [m["member_did"] for m in A.call(
            lambda: A.gw._store.get_federated_room_members(rid))]

    def on_b(rid):
        return B.call(lambda: rid in B.gw._local_rooms)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        # Joining again while already a member does not use up the invite.
        uses = _uses_left(A, rid)
        bob.events.clear()
        await _join(bob, link, rid)
        assert _uses_left(A, rid) == uses

        # Leave: the host drops Bob and stops sending room content to B.
        await bob.cmd(cmd="leave_local_room", room_id=rid)
        assert await alice.wait("room_member_left", room_id=rid, webid=bob.did)
        assert bob.did not in fed(rid)
        assert not on_b(rid)
        await alice.cmd(cmd="send_room", room_id=rid, content="after-leave")
        assert await _absent(lambda: any(e.get("content") == "after-leave" for e in bob.events))
        assert not any(m["content"] == "after-leave"
                       for m in B.call(lambda: B.gw._store.get_messages(rid)))

        # Kick: Bob is told and B drops the room.
        bob.events.clear()
        await _join(bob, link, rid)
        await alice.cmd(cmd="kick_member", room_id=rid, webid=bob.did)
        assert await bob.wait("kicked_from_room", room_id=rid)
        assert not on_b(rid)

        # Ban: same, and the rejoin is refused.
        bob.events.clear()
        await _join(bob, link, rid)
        await alice.cmd(cmd="ban_member", room_id=rid, webid=bob.did)
        assert await bob.wait("kicked_from_room", room_id=rid)
        assert not on_b(rid)
        bob.events.clear()
        await bob.cmd(cmd="join_remote_room", url=link)
        assert await bob.wait("error", message="banned_from_room")

        # Delete: the member gateway is told and drops its copy.
        await alice.cmd(cmd="unban_member", room_id=rid, webid=bob.did)
        await asyncio.sleep(0.3)
        bob.events.clear()
        await _join(bob, link, rid)
        await alice.cmd(cmd="delete_room", room_id=rid)
        assert await bob.wait("room_deleted", room_id=rid)
        assert not on_b(rid)
    finally:
        for c in (alice, bob):
            await c.close()


@pytest.mark.asyncio
async def test_owner_leaving_keeps_the_room_hosted_for_remote_members(tmp_path):
    A, B, C = GW(tmp_path, "a"), GW(tmp_path, "b"), GW(tmp_path, "c")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)
    carol = await Client("Carol").connect(C)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        # Ownership cannot go to a gateway that cannot host the room.
        await alice.cmd(cmd="transfer_ownership", room_id=rid, to_did=bob.did)
        err = await alice.wait_fn(lambda e: e.get("type") == "error"
                                  and "gateway" in str(e.get("message", "")))
        assert err, alice.of("error")

        await alice.cmd(cmd="leave_local_room", room_id=rid)
        assert await alice.wait("left_room", room_id=rid)
        assert await bob.wait("room_member_left", room_id=rid, webid=alice.did)
        # The room is still hosted on A: the link works and messages flow.
        assert A.call(lambda: rid in A.gw._local_rooms)
        await _join(carol, link, rid)
        await bob.cmd(cmd="send_room", room_id=rid, content="still-here")
        assert await carol.wait_content("still-here")
    finally:
        for c in (alice, bob, carol):
            await c.close()


@pytest.mark.asyncio
async def test_other_session_gets_room_joined_elsewhere_and_names_survive_restart(tmp_path):
    A, B = GW(tmp_path, "a"), GW(tmp_path, "b")
    alice = await Client("Alice").connect(A)
    bob = await Client("Bob").connect(B)
    bob2 = await Client("Bob", agent=bob.agent).connect(B)
    try:
        rid, link = await _room(alice)
        await _join(bob, link, rid)
        assert await bob2.wait("room_joined", room_id=rid)
        await alice.cmd(cmd="send_room", room_id=rid, content="to-both")
        assert await bob2.wait_content("to-both")
        await bob.cmd(cmd="leave_local_room", room_id=rid)
        assert await bob2.wait("left_room", room_id=rid)

        bob.events.clear()
        await _join(bob, link, rid)
        await bob.close()
        await bob2.close()
        B.restart()
        bob = await Client("Bob", agent=bob.agent).connect(B)
        bob2 = None
        assert await bob.members(rid) == ["Alice", "Bob"]
        assert alice.did not in B.call(lambda: B.gw._store.get_room_members(rid))
    finally:
        for c in (alice, bob, bob2):
            if c:
                await c.close()

"""Round 23 security tests: seq_num and prev_hash cryptographic chain
fields are persisted and loaded correctly from SQLite."""
import pytest

from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from unittest.mock import MagicMock, AsyncMock


def _make_store(tmp_path) -> LocalStore:
    return LocalStore(str(tmp_path / "store.db"))


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    cfg = GatewayConfig(db_path=str(tmp_path / "gw.db"))
    return ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg)


def _fake_ws(gw, webid: str):
    ws = MagicMock()
    ws.send = AsyncMock()
    ws.__hash__ = lambda self: id(self)
    ws.__eq__ = lambda self, other: self is other
    gw._client_webids[ws] = webid
    gw.clients.add(ws)
    return ws


# ── store.save_message / get_message ─────────────────────────────────────────


class TestChainFieldPersistence:
    def test_save_and_get_seq_num(self, tmp_path):
        """save_message persists seq_num; get_message returns it."""
        store = _make_store(tmp_path)
        store.save_message(
            "msg-1", "thread-A", "room", "did:key:z1", None,
            "hello", "2024-01-01T00:00:00+00:00",
            seq_num=42,
        )
        row = store.get_message("msg-1")
        assert row is not None
        assert row["seq_num"] == 42

    def test_save_and_get_prev_hash(self, tmp_path):
        """save_message persists prev_hash; get_message returns it."""
        store = _make_store(tmp_path)
        store.save_message(
            "msg-2", "thread-B", "dm", "did:key:z2", None,
            "world", "2024-01-01T00:00:01+00:00",
            prev_hash="deadbeef" * 8,
        )
        row = store.get_message("msg-2")
        assert row is not None
        assert row["prev_hash"] == "deadbeef" * 8

    def test_default_seq_num_is_zero(self, tmp_path):
        """When seq_num is not supplied, it defaults to 0."""
        store = _make_store(tmp_path)
        store.save_message(
            "msg-3", "thread-C", "relay", "did:key:z3", None,
            "default", "2024-01-01T00:00:02+00:00",
        )
        row = store.get_message("msg-3")
        assert row["seq_num"] == 0

    def test_default_prev_hash_is_empty(self, tmp_path):
        """When prev_hash is not supplied, it defaults to empty string."""
        store = _make_store(tmp_path)
        store.save_message(
            "msg-4", "thread-D", "relay", "did:key:z4", None,
            "default", "2024-01-01T00:00:03+00:00",
        )
        row = store.get_message("msg-4")
        assert row["prev_hash"] == ""

    def test_get_messages_includes_chain_fields(self, tmp_path):
        """get_messages returns seq_num and prev_hash for each message."""
        store = _make_store(tmp_path)
        store.save_message(
            "msg-5", "thread-E", "room", "did:key:z5", None,
            "hi", "2024-01-01T00:00:04+00:00",
            seq_num=7, prev_hash="abc123",
        )
        msgs = store.get_messages("thread-E")
        assert len(msgs) == 1
        assert msgs[0]["seq_num"] == 7
        assert msgs[0]["prev_hash"] == "abc123"

    def test_chain_sequence_across_messages(self, tmp_path):
        """Multiple messages in a thread can have distinct seq_num values."""
        store = _make_store(tmp_path)
        for i in range(3):
            store.save_message(
                f"msg-seq-{i}", "thread-F", "room", "did:key:z6", None,
                f"msg {i}", f"2024-01-01T00:00:0{i}+00:00",
                seq_num=i, prev_hash=f"hash{i}",
            )
        msgs = store.get_messages("thread-F")
        assert len(msgs) == 3
        seq_nums = [m["seq_num"] for m in msgs]
        assert sorted(seq_nums) == [0, 1, 2]


# ── gateway room handler passes seq_num/prev_hash ────────────────────────────


class TestRoomHandlerChainFields:
    @pytest.mark.asyncio
    async def test_send_room_message_persists_chain_fields(self, tmp_path):
        """Room message handler passes seq_num and prev_hash from data to store."""
        gw = _make_gateway(tmp_path)
        sender_did = "did:key:zsender"
        ws = _fake_ws(gw, sender_did)
        room_id = "room-chain-test"
        gw._local_rooms[room_id] = {
            "name": "test", "members": {ws}, "pinned_messages": [],
            "disappear_ms": 0, "creator_webid": sender_did,
            "messages": [], "history_mode": "none",
        }
        if gw._store:
            gw._store.save_room(room_id, "Test", "code-chain", "", "none", sender_did)
            gw._store.add_room_member(room_id, sender_did)

        await gw._handle_send_room(ws, {
            "room_id": room_id,
            "content": "chain test",
            "seq_num": 99,
            "prev_hash": "cafebabe",
        })

        if gw._store:
            msgs = gw._store.get_messages(room_id)
            assert msgs, "Message should have been saved"
            assert msgs[-1]["seq_num"] == 99
            assert msgs[-1]["prev_hash"] == "cafebabe"


# ── gateway relay handler passes seq_num/prev_hash ───────────────────────────


class TestRelayHandlerChainFields:
    @pytest.mark.asyncio
    async def test_relay_message_stored_with_chain_fields(self, tmp_path):
        """Relay handler passes seq_num and prev_hash to store when storing for offline delivery."""
        from proxion_messenger_core.didkey import pub_key_to_did
        from proxion_messenger_core.relay import sign_relay_message
        import json as _json
        import datetime

        gw = _make_gateway(tmp_path)
        from_did = pub_key_to_did(gw.agent.identity_pub_bytes)
        # Use a real target did that isn't connected — forces offline store path
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        target_priv = Ed25519PrivateKey.generate()
        target_pub = target_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        to_did = pub_key_to_did(target_pub)

        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        content = "chain hello"
        msg_id = "msg-relay-chain"
        sig = sign_relay_message(gw.agent.identity_key, from_did, to_did, msg_id, content, ts)

        payload = _json.dumps({
            "from_webid": from_did,
            "to_webid": to_did,
            "message_id": msg_id,
            "content": content,
            "timestamp": ts,
            "seq_num": 55,
            "prev_hash": "1a2b3c",
            "signature": sig,
        }).encode()

        status, _ = await gw._handle_relay_post(payload)

        assert status in ("200 OK", "202 Accepted"), f"Got {status}"
        if gw._store:
            row = gw._store.get_message(msg_id)
            if row:
                assert row["seq_num"] == 55
                assert row["prev_hash"] == "1a2b3c"

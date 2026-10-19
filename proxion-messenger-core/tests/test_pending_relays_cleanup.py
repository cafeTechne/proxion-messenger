"""Queued relays keep their full payload (message text, file bytes), so a row
must be gone once it is delivered, expired or given up on, and rows left by
older versions are pruned by the retry pass."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from proxion_messenger_core.local_store import LocalStore

PEER = "did:key:zPeer"
URL = "https://peer.example"


def _ids(store) -> list[str]:
    with store._conn() as conn:
        return [r[0] for r in conn.execute("SELECT id FROM pending_relays ORDER BY id")]


def _enqueue(store, rid):
    store.enqueue_relay(rid, PEER, URL, {"to_webid": PEER, "message_id": rid,
                                         "file": {"data_b64": "QUJD" * 100}})


def test_delivered_and_failed_relays_are_deleted(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    for rid in ("a", "b", "c"):
        _enqueue(store, rid)
    store.mark_relay_delivered("a")
    store.mark_relay_permanently_failed("b")
    assert _ids(store) == ["c"]


def test_expired_relays_are_deleted_when_taken(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    _enqueue(store, "old")
    _enqueue(store, "fresh")
    with store._conn() as conn:
        conn.execute("UPDATE pending_relays SET expires_at = ? WHERE id = 'old'", (time.time() - 5,))
    taken = store.take_expired_relays()
    assert [r["id"] for r in taken] == ["old"]
    assert _ids(store) == ["fresh"]


def test_prune_removes_rows_left_delivered_or_failed(tmp_path):
    store = LocalStore(tmp_path / "s.db")
    for rid in ("d", "f", "p"):
        _enqueue(store, rid)
    with store._conn() as conn:
        conn.execute("UPDATE pending_relays SET status = 'delivered' WHERE id = 'd'")
        conn.execute("UPDATE pending_relays SET status = 'failed' WHERE id = 'f'")
    assert store.prune_finished_relays() == 2
    assert _ids(store) == ["p"]


@pytest.mark.asyncio
async def test_retry_pass_delivers_and_prunes(tmp_path, monkeypatch):
    from proxion_messenger_core.gateway import GatewayConfig, ProxionGateway
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState
    import proxion_messenger_core.relay as relay_mod

    cfg = GatewayConfig(host="127.0.0.1", port=0, http_port=0,
                        public_url="ws://127.0.0.1:1", db_path=str(tmp_path / "g.db"))
    gw = ProxionGateway(AgentState.generate(), {}, {}, cfg, ReadState())
    gw._send_to_identity = AsyncMock()
    monkeypatch.setattr(relay_mod, "post_relay", AsyncMock(return_value=True))
    _enqueue(gw._store, "live")
    _enqueue(gw._store, "legacy")
    with gw._store._conn() as conn:
        conn.execute("UPDATE pending_relays SET status = 'delivered' WHERE id = 'legacy'")

    await gw._relay_retry_pass()

    assert _ids(gw._store) == []

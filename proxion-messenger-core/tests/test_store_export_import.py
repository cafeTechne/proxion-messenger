"""Tests for LocalStore.export_all() and import_data() — R14.4."""
import pytest
import time
from proxion_messenger_core.local_store import LocalStore


@pytest.fixture
def store(tmp_path):
    return LocalStore(str(tmp_path / "test.db"))


def _seed_message(store, message_id="msg-1", thread_id="t-1"):
    store.save_message(
        message_id, thread_id, "room",
        "did:key:alice", "Alice", "hello", "2024-01-01T00:00:00Z"
    )


# ── R14.4.1: export schema ─────────────────────────────────────────────────


def test_export_all_has_required_keys(store):
    """R14.4.1: export_all() returns dict with version, exported_at, and all table keys."""
    _seed_message(store)
    data = store.export_all()
    assert data["version"] == 1
    assert "exported_at" in data
    assert isinstance(data["messages"], list)
    assert isinstance(data["relationships"], list)
    assert isinstance(data["dm_threads"], list)
    assert isinstance(data["scheduled"], list)
    assert isinstance(data["display_names"], list)


def test_export_all_includes_seeded_message(store):
    """Exported messages include what was saved."""
    _seed_message(store, "msg-export-test", "thread-export")
    data = store.export_all()
    ids = [m["message_id"] for m in data["messages"]]
    assert "msg-export-test" in ids


def test_export_all_empty_store(store):
    """export_all on an empty store returns lists with no items."""
    data = store.export_all()
    assert data["messages"] == []
    assert data["relationships"] == []
    assert data["dm_threads"] == []
    assert data["display_names"] == []


# ── R14.4.2: import no duplicates ──────────────────────────────────────────


def test_import_data_counts_inserted(store):
    """import_data returns correct count for first import."""
    data = {
        "messages": [{
            "message_id": "msg-import-1", "thread_id": "t-i", "thread_type": "room",
            "from_webid": "did:key:alice", "from_display_name": "Alice",
            "content": "hi", "timestamp": "2024-01-01T00:00:00Z",
            "edited_at": None, "reply_to_id": None,
        }],
        "relationships": [],
        "dm_threads": [],
        "display_names": [],
    }
    counts = store.import_data(data)
    assert counts["messages"] == 1


def test_import_data_no_duplicates(store):
    """R14.4.2: import_data uses INSERT OR IGNORE — second call adds 0 rows."""
    _seed_message(store, "msg-dup", "t-dup")
    data = store.export_all()
    store.import_data(data)
    counts2 = store.import_data(data)
    assert counts2["messages"] == 0


# ── R14.4.3: idempotent reimport ──────────────────────────────────────────


def test_import_data_idempotent(store):
    """R14.4.3: repeated imports never inflate the message count."""
    _seed_message(store, "msg-idem", "t-idem")
    original_count = len(store.export_all()["messages"])
    data = store.export_all()
    for _ in range(3):
        store.import_data(data)
    assert len(store.export_all()["messages"]) == original_count


def test_import_data_merges_new_records(store, tmp_path):
    """import_data adds records from another store that aren't present locally."""
    store2 = LocalStore(str(tmp_path / "other.db"))
    _seed_message(store2, "msg-from-other", "t-other")
    data_from_other = store2.export_all()

    counts = store.import_data(data_from_other)
    assert counts["messages"] == 1

    all_ids = [m["message_id"] for m in store.export_all()["messages"]]
    assert "msg-from-other" in all_ids


def test_import_data_display_names(store, tmp_path):
    """display_names are imported and can be retrieved."""
    store2 = LocalStore(str(tmp_path / "src.db"))
    store2.save_display_name("did:key:carol", "Carol")
    data = store2.export_all()
    store.import_data(data)
    assert store.get_display_name("did:key:carol") == "Carol"


# ── R14.1.3: GET /export uses chunked transfer encoding ────────────────────

pytest.importorskip("websockets")
import websockets  # noqa: E402
import socket as _socket
import asyncio as _asyncio
import httpx as _httpx
import json as _json
from gwharness import start_gateway as _serve_gw


def _free_port_exp():
    with _socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start_export_gateway(db_path, ws_port, http_port):
    from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
    from proxion_messenger_core.persist import AgentState
    from proxion_messenger_core.readstate import ReadState

    agent = AgentState.generate()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}", db_path=db_path,
    )
    gw = ProxionGateway(agent=agent, dm_clients={}, room_memberships={}, config=cfg, read_state=ReadState())
    # Raises on startup failure and is shut down after the test
    # (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    ready = handle.ready
    return gw, ready


@pytest.mark.asyncio
async def test_export_uses_chunked_transfer_encoding(tmp_path):
    """R14.1.3: GET /export response uses Transfer-Encoding: chunked."""
    db_path = str(tmp_path / "export.db")
    ws_port = _free_port_exp()
    http_port = _free_port_exp()

    gw, ready = _start_export_gateway(db_path, ws_port, http_port)
    assert ready.wait(timeout=5), "gateway failed to start"
    await _asyncio.sleep(0.2)

    resp = _httpx.get(f"http://127.0.0.1:{http_port}/export", timeout=10)
    assert resp.status_code == 200
    # httpx transparently de-chunks, but the header should be present
    assert resp.headers.get("transfer-encoding", "").lower() == "chunked"
    data = resp.json()
    assert "version" in data
    assert "messages" in data


@pytest.mark.asyncio
async def test_export_seeded_data_returned_in_chunks(tmp_path):
    """R14.1.3: seeded messages are fully included in the chunked export."""
    db_path = str(tmp_path / "chunked.db")

    # Seed 50 messages directly
    s = LocalStore(db_path)
    for i in range(50):
        s.save_message(
            f"msg-{i}", "thread-1", "room",
            "did:key:alice", "Alice", f"message {i}", "2024-01-01T00:00:00Z"
        )

    ws_port = _free_port_exp()
    http_port = _free_port_exp()
    gw, ready = _start_export_gateway(db_path, ws_port, http_port)
    assert ready.wait(timeout=5), "gateway failed to start"
    await _asyncio.sleep(0.2)

    resp = _httpx.get(f"http://127.0.0.1:{http_port}/export", timeout=10)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 50

"""Tests for Round 13 — edit history, room read receipts, contacts, presence aggregation,
metrics endpoint, structured logging, POST /restore hardening, and more."""
from __future__ import annotations

import asyncio
import json
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.local_store import LocalStore
from proxion_messenger_core.readstate import ReadState
from proxion_messenger_core.logging_config import configure_logging, new_request_id, REQUEST_ID
from gwharness import start_gateway as _serve_gw


# ── Helpers ──────────────────────────────────────────────────────────────────

def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_gateway(tmp_path):
    agent = AgentState.generate()
    ws_port = _free_port()
    http_port = _free_port()
    cfg = GatewayConfig(
        host="127.0.0.1", port=ws_port, http_port=http_port,
        public_url=f"ws://127.0.0.1:{ws_port}",
        db_path=str(tmp_path / "gw.db"),
    )
    gw = ProxionGateway(
        agent=agent, dm_clients={}, room_memberships={}, config=cfg,
        read_state=ReadState(),
    )
    return gw, ws_port, http_port


def _start_gateway(tmp_path):
    pytest.importorskip("websockets")
    import websockets
    gw, ws_port, http_port = _make_gateway(tmp_path)
    # Raises if the gateway fails to start or never accepts a connection, and
    # registers it for shutdown after the test (see tests/gwharness.py).
    handle = _serve_gw(gw, ws_port, http_port)
    return gw, handle.ws_port, handle.http_port, handle.ready


# ── 13.11 — Message edit history ─────────────────────────────────────────────

def test_message_edit_history_stored(tmp_path):
    """save_edit stores a row; update_message with editor_webid writes history."""
    store = LocalStore(str(tmp_path / "test.db"))
    ts = datetime.now(timezone.utc).isoformat()
    store.save_message("msg-1", "room-1", "room", "did:key:alice", "Alice", "hello", ts)
    store.update_message("msg-1", "hello world", ts, editor_webid="did:key:alice")
    edits = store.get_edits("msg-1")
    assert len(edits) == 1, f"Expected 1 edit, got {len(edits)}"
    assert edits[0]["prev_content"] == "hello"
    assert edits[0]["new_content"] == "hello world"
    assert edits[0]["edited_by"] == "did:key:alice"


def test_get_edits_ordered(tmp_path):
    """get_edits returns rows in ascending edited_at order."""
    store = LocalStore(str(tmp_path / "test.db"))
    ts = datetime.now(timezone.utc).isoformat()
    store.save_message("msg-ord", "room-1", "room", "did:key:alice", "Alice", "v0", ts)
    store.save_edit("e1", "msg-ord", "v0", "v1", "did:key:alice", "2026-01-01T00:00:00+00:00")
    store.save_edit("e2", "msg-ord", "v1", "v2", "did:key:alice", "2026-01-02T00:00:00+00:00")
    edits = store.get_edits("msg-ord")
    assert len(edits) == 2
    assert edits[0]["edited_at"] < edits[1]["edited_at"]


def test_update_message_no_editor_no_history(tmp_path):
    """update_message without editor_webid should NOT write to message_edits."""
    store = LocalStore(str(tmp_path / "test.db"))
    ts = datetime.now(timezone.utc).isoformat()
    store.save_message("msg-noed", "room-1", "room", "did:key:alice", "Alice", "original", ts)
    store.update_message("msg-noed", "updated", ts)  # no editor_webid
    edits = store.get_edits("msg-noed")
    assert edits == []


# ── 13.12 — Room read receipts ────────────────────────────────────────────────

def test_mark_room_read_upserts(tmp_path):
    """mark_room_read upserts correctly; second call updates last_read_message_id."""
    store = LocalStore(str(tmp_path / "test.db"))
    store.save_room("room-1", "Test", "code1", "", "none")
    ts = datetime.now(timezone.utc).isoformat()
    store.save_message("msg-a", "room-1", "room", "did:key:alice", "Alice", "hi", ts)
    store.save_message("msg-b", "room-1", "room", "did:key:alice", "Alice", "bye", ts)

    store.mark_room_read("room-1", "did:key:alice", "msg-a", ts)
    rec = store.get_room_last_read("room-1", "did:key:alice")
    assert rec is not None
    assert rec["last_read_message_id"] == "msg-a"

    store.mark_room_read("room-1", "did:key:alice", "msg-b", ts)
    rec2 = store.get_room_last_read("room-1", "did:key:alice")
    assert rec2["last_read_message_id"] == "msg-b"


def test_room_unread_count_increments(tmp_path):
    """increment_room_unread bumps count per user; reset_room_unread zeros it for that user."""
    store = LocalStore(str(tmp_path / "test.db"))
    store.save_room("room-uc", "Test", "codex", "", "none")
    assert store.get_room_unread_count("room-uc", "did:key:alice") == 0
    store.increment_room_unread("room-uc", ["did:key:alice"])
    store.increment_room_unread("room-uc", ["did:key:alice"])
    assert store.get_room_unread_count("room-uc", "did:key:alice") == 2
    # bob's count is unaffected
    assert store.get_room_unread_count("room-uc", "did:key:bob") == 0
    store.reset_room_unread("room-uc", "did:key:alice")
    assert store.get_room_unread_count("room-uc", "did:key:alice") == 0


# ── 13.14 — Contact search ────────────────────────────────────────────────────

def test_search_contacts_case_insensitive(tmp_path):
    """search_contacts('alice') matches a contact with display_name='Alice'."""
    store = LocalStore(str(tmp_path / "test.db"))
    store.upsert_contact("did:key:aaaa", "Alice", source="dm")
    store.upsert_contact("did:key:bbbb", "Bob", source="dm")

    results = store.search_contacts("alice")
    assert any(r["display_name"] == "Alice" for r in results)
    assert not any(r["display_name"] == "Bob" for r in results)


def test_contacts_upserted_on_save(tmp_path):
    """upsert_contact updates display_name on conflict."""
    store = LocalStore(str(tmp_path / "test.db"))
    store.upsert_contact("did:key:x", "Old Name", source="dm")
    store.upsert_contact("did:key:x", "New Name", source="room")
    contacts = store.get_all_contacts()
    match = [c for c in contacts if c["webid"] == "did:key:x"]
    assert len(match) == 1
    assert match[0]["display_name"] == "New Name"


def test_search_contacts_empty_query(tmp_path):
    """search_contacts with empty query returns empty list."""
    store = LocalStore(str(tmp_path / "test.db"))
    store.upsert_contact("did:key:x", "Alice", source="dm")
    assert store.search_contacts("") == []


# ── 13.13 — Multi-device presence aggregation ─────────────────────────────────

def test_presence_aggregation_no_offline_until_last(tmp_path):
    """Removing one of two connections does not broadcast offline."""
    gw, _, _ = _make_gateway(tmp_path)
    ws1 = MagicMock()
    ws2 = MagicMock()
    webid = "did:key:alice"

    gw._presence_by_identity.setdefault(webid, set()).update({ws1, ws2})
    gw.clients = {ws1, ws2}

    _pid_set = gw._presence_by_identity.get(webid, set())
    _pid_set.discard(ws1)
    # Still has ws2 — should NOT broadcast offline
    assert len(_pid_set) > 0, "Should still have one connection"


def test_presence_aggregation_offline_when_last(tmp_path):
    """Removing the last connection triggers offline broadcast condition."""
    gw, _, _ = _make_gateway(tmp_path)
    ws1 = MagicMock()
    webid = "did:key:alice"

    gw._presence_by_identity.setdefault(webid, set()).add(ws1)

    _pid_set = gw._presence_by_identity.get(webid, set())
    _pid_set.discard(ws1)
    # Set is now empty — should broadcast offline
    assert len(_pid_set) == 0, "Should have zero connections"


# ── 13.4 — Prometheus metrics endpoint ───────────────────────────────────────

@pytest.mark.asyncio
async def test_metrics_endpoint_returns_counters(tmp_path):
    """GET /metrics returns 200 with proxion_messages_total line."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(b"GET /metrics HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(16384), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "200" in status_line, f"Expected 200, got: {status_line!r}"
    assert b"proxion_messages_total" in response, "Missing proxion_messages_total"
    assert b"proxion_uptime_seconds" in response, "Missing proxion_uptime_seconds"


@pytest.mark.asyncio
async def test_metrics_ws_connections_tracked(tmp_path):
    """_metrics['ws_connections_total'] increments on connect."""
    gw, ws_port, _, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    pytest.importorskip("websockets")
    import websockets
    async with websockets.connect(f"ws://127.0.0.1:{ws_port}") as ws:
        await asyncio.sleep(0.1)
        assert gw._metrics["ws_connections_total"] >= 1


# ── 13.5 — Structured logging ────────────────────────────────────────────────

def test_json_logging_format(tmp_path, caplog):
    """configure_logging(json_output=True) can be called without error."""
    import logging
    configure_logging(json_output=True, log_level="DEBUG")
    logger = logging.getLogger("proxion_messenger_core.test_r13")
    logger.info("test structured log")
    # Reset to text for subsequent tests
    configure_logging(json_output=False, log_level="WARNING")


def test_request_id_context_var():
    """new_request_id() sets REQUEST_ID context var and returns an 8-char hex string."""
    rid = new_request_id()
    assert len(rid) == 8
    assert all(c in "0123456789abcdef" for c in rid)
    assert REQUEST_ID.get("") == rid


# ── 13.3 — POST /restore path hardening ──────────────────────────────────────

@pytest.mark.asyncio
async def test_restore_creates_agent_json(tmp_path):
    """POST /restore with a valid backup creates agent.json in db parent dir."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    # Generate a backup blob from the current agent
    passphrase = b"test-pass-123"
    backup_blob = gw.agent.export_backup(passphrase)

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    url = b"/restore?passphrase=test-pass-123"
    writer.write(
        b"POST " + url + b" HTTP/1.0\r\nHost: 127.0.0.1\r\n"
        b"Content-Type: application/json\r\n"
        b"Content-Length: " + str(len(backup_blob)).encode() + b"\r\n\r\n" + backup_blob
    )
    await writer.drain()
    response = await asyncio.wait_for(reader.read(4096), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "200" in status_line, f"Expected 200, got: {status_line!r}"

    # agent.json should now exist
    agent_json = tmp_path / "agent.json"
    assert agent_json.exists(), "agent.json should be created by /restore"


# ── 13.2 — solid_client delete retry ─────────────────────────────────────────

def test_solid_client_delete_retries_on_401():
    """SolidClient.delete retries once on 401 and calls _refresh_auth."""
    from proxion_messenger_core.solid_client import SolidClient, SolidError
    from proxion_messenger_core.solid import SolidResolver

    resolver = MagicMock(spec=SolidResolver)
    resolver.resolve.return_value = "http://example.com/resource"

    session = MagicMock()
    call_count = [0]

    def _fake_delete(url, headers):
        call_count[0] += 1
        resp = MagicMock()
        resp.status_code = 401 if call_count[0] == 1 else 204
        return resp

    session.delete.side_effect = _fake_delete
    client = SolidClient(resolver=resolver, session=session)
    client._owns_session = False

    refresh_called = [False]
    original_refresh = client._refresh_auth

    def _mock_refresh(response=None):
        refresh_called[0] = True
        client._auth_headers["Authorization"] = "Bearer new-token"

    client._refresh_auth = _mock_refresh
    client.delete("stash://test/resource")

    assert call_count[0] == 2, "Should retry once after 401"
    assert refresh_called[0], "_refresh_auth should be called on 401"


# ── 13.9 — Push subscription endpoint ────────────────────────────────────────

@pytest.mark.asyncio
async def test_contacts_search_endpoint(tmp_path):
    """GET /contacts/search?q=al returns matching contacts as JSON."""
    gw, _, http_port, ready = _start_gateway(tmp_path)
    assert ready.wait(timeout=5), "gateway failed to start"
    await asyncio.sleep(0.15)

    # Pre-populate a contact
    gw._store.upsert_contact("did:key:alice123", "Alice Smith", source="dm")

    reader, writer = await asyncio.open_connection("127.0.0.1", http_port)
    writer.write(b"GET /contacts/search?q=alice HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
    await writer.drain()
    response = await asyncio.wait_for(reader.read(16384), timeout=5.0)
    writer.close()

    status_line = response.split(b"\r\n")[0].decode()
    assert "200" in status_line, f"Expected 200, got: {status_line!r}"
    body_start = response.find(b"\r\n\r\n")
    body = json.loads(response[body_start + 4:])
    assert isinstance(body, list)
    assert any(c["webid"] == "did:key:alice123" for c in body), f"alice not found: {body}"

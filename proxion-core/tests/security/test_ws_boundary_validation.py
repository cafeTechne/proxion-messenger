"""Round 8: WebSocket command payload boundary validation tests."""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock

from proxion_messenger_core.command_validation import (
    validate_command_payload,
    SchemaError,
    MUTATING_COMMANDS,
    AUTH_RATE_COMMANDS,
    HEAVY_COMMANDS,
)
from proxion_messenger_core.gateway import ProxionGateway, GatewayConfig
from proxion_messenger_core.persist import AgentState
from proxion_messenger_core.readstate import ReadState


# ---------------------------------------------------------------------------
# Unit tests: validate_command_payload
# ---------------------------------------------------------------------------

def test_unknown_command_passes():
    """Commands with no schema entry are not rejected by the validator."""
    validate_command_payload("totally_unknown_cmd", {})  # must not raise


def test_valid_send_dm():
    validate_command_payload("send_dm", {"cert_id": "cid", "content": "hello"})


def test_send_dm_missing_cert_id():
    with pytest.raises(SchemaError, match="cert_id"):
        validate_command_payload("send_dm", {"content": "hello"})


def test_send_dm_missing_content():
    with pytest.raises(SchemaError, match="content"):
        validate_command_payload("send_dm", {"cert_id": "cid"})


def test_send_dm_wrong_type():
    with pytest.raises(SchemaError, match="expected str"):
        validate_command_payload("send_dm", {"cert_id": 123, "content": "hi"})


def test_send_dm_content_too_large():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("send_dm", {
            "cert_id": "cid",
            "content": "x" * 20_000,   # >16384 bytes
        })


def test_send_room_validates_room_id():
    with pytest.raises(SchemaError, match="room_id"):
        validate_command_payload("send_room", {"content": "hi"})


def test_emoji_too_long_rejected():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("add_reaction", {
            "message_id": "mid", "emoji": "😀" * 5,  # >12 chars
        })


def test_register_no_schema_constraint():
    """register accepts either did or webid — no schema-level required field."""
    validate_command_payload("register", {})  # must not raise


def test_auth_response_requires_signature():
    with pytest.raises(SchemaError, match="signature"):
        validate_command_payload("auth_response", {})


def test_schedule_message_enforces_4kb_cap():
    with pytest.raises(SchemaError, match="exceeds max length"):
        validate_command_payload("schedule_message", {
            "thread_id": "t",
            "content": "x" * 5_000,   # >4096 bytes
            "send_at": "2030-01-01T00:00:00+00:00",
        })


def test_valid_schedule_message():
    validate_command_payload("schedule_message", {
        "thread_id": "t",
        "content": "reminder",
        "send_at": "2030-01-01T00:00:00+00:00",
    })


def test_voice_invite_validates():
    with pytest.raises(SchemaError, match="target_webid"):
        validate_command_payload("voice_invite", {"sdp_offer": "v=0\r\n"})


def test_typing_has_no_required_fields():
    """typing accepts room_id or cert_id — no schema-level required field."""
    validate_command_payload("typing", {})  # must not raise


# ---------------------------------------------------------------------------
# Integration tests: gateway process_command rejects invalid payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def gateway():
    agent = AgentState.generate()
    gw = ProxionGateway(
        agent=agent,
        dm_clients=[],
        room_memberships=[],
        config=GatewayConfig(port=9988),
        read_state=ReadState(),
    )
    return gw


def _registered_ws(gw, webid="did:key:test"):
    ws = MagicMock()
    ws.send = AsyncMock()
    gw.clients.add(ws)
    gw._client_webids[ws] = webid
    return ws


@pytest.mark.asyncio
async def test_process_command_rejects_schema_error(gateway):
    """process_command returns E_SCHEMA for missing required field."""
    ws = _registered_ws(gateway)
    await gateway.process_command(ws, {"cmd": "send_dm", "content": "oops"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent["code"] == "E_SCHEMA"


@pytest.mark.asyncio
async def test_process_command_allows_unknown_cmd(gateway):
    """process_command does not raise on an unknown command (handled by router)."""
    ws = _registered_ws(gateway)
    # unknown cmd with no required fields — should not get E_SCHEMA
    await gateway.process_command(ws, {"cmd": "does_not_exist"})
    sent = json.loads(ws.send.call_args[0][0])
    # Router sends "Unknown command" error, not E_SCHEMA
    assert sent.get("code") != "E_SCHEMA"


@pytest.mark.asyncio
async def test_process_command_no_exc_leakage(gateway):
    """Internal errors must not expose exception detail via str(exc)."""
    ws = _registered_ws(gateway)
    # Trigger a valid-schema command that fails internally (cert_id not found)
    await gateway.process_command(ws, {"cmd": "send_dm", "cert_id": "no-such", "content": "hi"})
    for call in ws.send.call_args_list:
        msg = json.loads(call[0][0])
        # Error message must not contain raw exception text
        assert "Traceback" not in msg.get("message", "")
        assert "AttributeError" not in msg.get("message", "")
        assert "KeyError" not in msg.get("message", "")


@pytest.mark.asyncio
async def test_mutating_command_rejected_for_revoked_did(gateway):
    """Mutating commands from revoked identities receive E_REVOKED."""
    ws = _registered_ws(gateway, webid="did:key:revoked")
    gateway._revoked_dids.add("did:key:revoked")
    await gateway.process_command(ws, {"cmd": "send_room", "room_id": "r", "content": "hi"})
    sent = json.loads(ws.send.call_args[0][0])
    assert sent.get("code") == "E_REVOKED"
